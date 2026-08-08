import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import app as vocabulary_app
from pdf_search import PdfEntry


class WordManagementTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = vocabulary_app.DATABASE
        vocabulary_app.DATABASE = Path(self.temp_dir.name) / "test.db"
        vocabulary_app.init_db()
        vocabulary_app.app.config.update(TESTING=True)
        self.client = vocabulary_app.app.test_client()

        with vocabulary_app.get_db() as connection:
            connection.execute(
                "INSERT INTO study_days (study_date) VALUES (?)", ("2099-09-01",)
            )
            connection.execute(
                "INSERT INTO study_days (study_date) VALUES (?)", ("2099-09-02",)
            )
            self.first_day_id = connection.execute(
                "SELECT id FROM study_days WHERE study_date = ?", ("2099-09-01",)
            ).fetchone()["id"]
            self.second_day_id = connection.execute(
                "SELECT id FROM study_days WHERE study_date = ?", ("2099-09-02",)
            ).fetchone()["id"]

    def tearDown(self):
        vocabulary_app.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def test_selected_day_is_remembered(self):
        response = self.client.post(
            "/preferences/study-day",
            data={"study_day_id": self.first_day_id},
        )
        self.assertEqual(response.status_code, 204)

        html = self.client.get("/").get_data(as_text=True)
        self.assertIn(
            f'data-active-day-id="{self.first_day_id}"',
            html,
        )

    def test_study_date_can_be_edited(self):
        response = self.client.post(
            f"/days/{self.first_day_id}/edit",
            data={"study_date": "2099-09-03"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("学习日期已修改为 2099-09-03", response.get_data(as_text=True))
        with vocabulary_app.get_db() as connection:
            study_date = connection.execute(
                "SELECT study_date FROM study_days WHERE id = ?",
                (self.first_day_id,),
            ).fetchone()["study_date"]
        self.assertEqual(study_date, "2099-09-03")
        with self.client.session_transaction() as session:
            self.assertEqual(session["selected_day_id"], self.first_day_id)

    def test_study_date_cannot_be_edited_to_an_existing_date(self):
        response = self.client.post(
            f"/days/{self.first_day_id}/edit",
            data={"study_date": "2099-09-02"},
            follow_redirects=True,
        )

        self.assertIn("这个学习日期已经存在", response.get_data(as_text=True))
        with vocabulary_app.get_db() as connection:
            study_date = connection.execute(
                "SELECT study_date FROM study_days WHERE id = ?",
                (self.first_day_id,),
            ).fetchone()["study_date"]
        self.assertEqual(study_date, "2099-09-01")

    def test_delete_study_date_cascades_and_selects_a_remaining_date(self):
        with vocabulary_app.get_db() as connection:
            cursor = connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition)
                VALUES (?, 'dated-word', '日期删除测试')
                """,
                (self.first_day_id,),
            )
            word_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO learning_records (word_id, result) VALUES (?, 'unknown')",
                (word_id,),
            )
        self.client.post(
            "/preferences/study-day",
            data={"study_day_id": self.first_day_id},
        )

        response = self.client.post(
            f"/days/{self.first_day_id}/delete",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("已删除学习日期 2099-09-01", response.get_data(as_text=True))
        with vocabulary_app.get_db() as connection:
            day_count = connection.execute(
                "SELECT COUNT(*) FROM study_days WHERE id = ?", (self.first_day_id,)
            ).fetchone()[0]
            word_count = connection.execute(
                "SELECT COUNT(*) FROM words WHERE id = ?", (word_id,)
            ).fetchone()[0]
            record_count = connection.execute(
                "SELECT COUNT(*) FROM learning_records WHERE word_id = ?", (word_id,)
            ).fetchone()[0]
        self.assertEqual((day_count, word_count, record_count), (0, 0, 0))
        with self.client.session_transaction() as session:
            self.assertEqual(session["selected_day_id"], self.second_day_id)

    def test_date_library_has_two_step_delete_confirmation(self):
        html = self.client.get("/dates").get_data(as_text=True)

        self.assertIn('class="edit-date-form"', html)
        self.assertIn('class="delete-day-form"', html)
        self.assertIn("const firstConfirmed = window.confirm", html)
        self.assertIn("const secondConfirmed = window.confirm", html)

    def test_delete_word_also_deletes_its_learning_records(self):
        with vocabulary_app.get_db() as connection:
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition)
                VALUES (?, 'mistake', '错误')
                """,
                (self.first_day_id,),
            )
            word_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute(
                "INSERT INTO learning_records (word_id, result) VALUES (?, 'unknown')",
                (word_id,),
            )

        response = self.client.post(f"/words/{word_id}/delete")
        self.assertEqual(response.status_code, 302)

        with vocabulary_app.get_db() as connection:
            word_count = connection.execute(
                "SELECT COUNT(*) FROM words WHERE id = ?", (word_id,)
            ).fetchone()[0]
            record_count = connection.execute(
                "SELECT COUNT(*) FROM learning_records WHERE word_id = ?", (word_id,)
            ).fetchone()[0]
        self.assertEqual(word_count, 0)
        self.assertEqual(record_count, 0)

    def test_missing_word_shows_clear_message_and_remembers_day(self):
        searcher = Mock()
        searcher.search.return_value = None
        with patch("app.get_pdf_searcher", return_value=searcher):
            response = self.client.post(
                "/words",
                data={"study_day_id": self.second_day_id, "word": "notinthebook"},
                follow_redirects=True,
            )

        html = response.get_data(as_text=True)
        self.assertIn("没有在词库中找到", html)
        self.assertIn(
            f'data-active-day-id="{self.second_day_id}"',
            html,
        )

    def test_saved_word_message_includes_definition(self):
        searcher = Mock()
        searcher.search.return_value = PdfEntry(
            word="example",
            definition="n. 例子；实例",
            phrases=(),
            page_number=1,
        )
        with patch("app.get_pdf_searcher", return_value=searcher):
            response = self.client.post(
                "/words",
                data={"study_day_id": self.first_day_id, "word": "example"},
                follow_redirects=True,
            )

        html = response.get_data(as_text=True)
        self.assertIn('class="message success"', html)
        self.assertIn("example 添加成功\nn. 例子；实例", html)

    def test_library_hides_phrases_but_study_mode_keeps_them(self):
        phrase = "example phrase 示例词组"
        with vocabulary_app.get_db() as connection:
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, phrases)
                VALUES (?, ?, ?, ?)
                """,
                (
                    self.first_day_id,
                    "example",
                    "n. 例子",
                    json.dumps([phrase], ensure_ascii=False),
                ),
            )

        self.client.post(
            "/preferences/study-day",
            data={"study_day_id": self.first_day_id},
        )
        library_html = self.client.get("/").get_data(as_text=True)
        study_html = self.client.get(
            f"/study/{self.first_day_id}"
        ).get_data(as_text=True)

        self.assertIn("n. 例子", library_html)
        self.assertNotIn(phrase, library_html)
        self.assertIn(phrase, study_html)

    def test_main_page_only_shows_selected_day_and_dates_page_is_foldable(self):
        with vocabulary_app.get_db() as connection:
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition)
                VALUES (?, 'firstword', '第一个日期')
                """,
                (self.first_day_id,),
            )
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition)
                VALUES (?, 'secondword', '第二个日期')
                """,
                (self.second_day_id,),
            )

        response = self.client.post(
            "/preferences/study-day",
            data={
                "study_day_id": self.first_day_id,
                "redirect_to": "index",
            },
            follow_redirects=True,
        )
        main_html = response.get_data(as_text=True)
        self.assertIn("firstword", main_html)
        self.assertNotIn("secondword", main_html)

        dates_html = self.client.get("/dates").get_data(as_text=True)
        self.assertGreaterEqual(dates_html.count('<details class="date-accordion"'), 2)
        self.assertIn("firstword", dates_html)
        self.assertIn("secondword", dates_html)

    def test_main_page_supports_continuous_word_entry(self):
        html = self.client.get("/").get_data(as_text=True)

        self.assertIn("continuous-word-entry", html)
        self.assertIn("sessionStorage", html)
        self.assertIn("wordInput.focus()", html)

    def test_main_page_has_animated_back_to_top_button(self):
        html = self.client.get("/").get_data(as_text=True)
        script = (
            Path(__file__).resolve().parents[1] / "static" / "back_to_top.js"
        ).read_text(encoding="utf-8")
        css = (
            Path(__file__).resolve().parents[1] / "static" / "style.css"
        ).read_text(encoding="utf-8")

        self.assertIn('id="back-to-top"', html)
        self.assertIn("back_to_top.js", html)
        self.assertIn("window.scrollTo({", script)
        self.assertIn("behavior: reducedMotion.matches ? 'auto' : 'smooth'", script)
        self.assertIn("window.scrollY > 320", script)
        self.assertNotIn("window.location", script)
        self.assertIn(".back-to-top.is-visible", css)

    def test_main_word_star_is_visually_centered_with_word(self):
        css = (
            Path(__file__).resolve().parents[1] / "static" / "style.css"
        ).read_text(encoding="utf-8")

        self.assertIn(
            ".favorite-form { display: flex; flex: 0 0 auto; align-items: center",
            css,
        )
        self.assertIn(
            ".word-title-line .favorite-button svg, "
            ".word-title-line .favorite-indicator svg { transform: translateY(1px); }",
            css,
        )

    def test_empty_date_input_uses_friendly_prompt(self):
        html = self.client.get("/").get_data(as_text=True)

        self.assertIn("请选择日期", html)
        self.assertIn('class="date-input-placeholder"', html)

    def test_favorite_column_exists_and_defaults_to_false(self):
        with vocabulary_app.get_db() as connection:
            columns = {
                row["name"]: row for row in connection.execute("PRAGMA table_info(words)")
            }
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition)
                VALUES (?, 'favorite-default', '默认收藏状态')
                """,
                (self.first_day_id,),
            )
            is_favorite = connection.execute(
                "SELECT is_favorite FROM words WHERE word = 'favorite-default'"
            ).fetchone()["is_favorite"]

        self.assertIn("is_favorite", columns)
        self.assertEqual(is_favorite, 0)

    def test_word_can_be_added_to_and_removed_from_favorites(self):
        with vocabulary_app.get_db() as connection:
            cursor = connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition)
                VALUES (?, 'favorite-word', '收藏测试')
                """,
                (self.first_day_id,),
            )
            word_id = cursor.lastrowid
        self.client.post(
            "/preferences/study-day",
            data={"study_day_id": self.first_day_id},
        )

        initial_html = self.client.get("/").get_data(as_text=True)
        self.assertIn(f'/words/{word_id}/favorite', initial_html)
        self.assertIn("data-favorite-button", initial_html)
        favorite_css = (
            Path(__file__).resolve().parents[1] / "static" / "style.css"
        ).read_text(encoding="utf-8")
        self.assertIn("stroke-linejoin: round", favorite_css)
        self.assertIn("--favorite-yellow: #f9a900", favorite_css)
        self.assertIn(
            "fill: var(--favorite-yellow); stroke: var(--favorite-yellow)",
            favorite_css,
        )

        favorite_response = self.client.post(
            f"/words/{word_id}/favorite",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(favorite_response.status_code, 200)
        self.assertTrue(favorite_response.get_json()["is_favorite"])
        with vocabulary_app.get_db() as connection:
            is_favorite = connection.execute(
                "SELECT is_favorite FROM words WHERE id = ?", (word_id,)
            ).fetchone()["is_favorite"]
        self.assertEqual(is_favorite, 1)
        self.assertIn(
            'favorite-button is-active',
            self.client.get("/").get_data(as_text=True),
        )

        self.client.post(f"/words/{word_id}/favorite")
        with vocabulary_app.get_db() as connection:
            is_favorite = connection.execute(
                "SELECT is_favorite FROM words WHERE id = ?", (word_id,)
            ).fetchone()["is_favorite"]
        self.assertEqual(is_favorite, 0)

    def test_favorite_script_updates_without_page_reload(self):
        script_path = Path(__file__).resolve().parents[1] / "static" / "favorites.js"
        script = script_path.read_text(encoding="utf-8")

        self.assertIn("event.preventDefault()", script)
        self.assertIn("await fetch(form.action", script)
        self.assertIn("Accept: 'application/json'", script)
        self.assertNotIn("window.location.reload", script)

    def test_success_toast_width_adapts_to_content(self):
        css_path = Path(__file__).resolve().parents[1] / "static" / "style.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn("width: max-content", css)
        self.assertIn("width: fit-content", css)


if __name__ == "__main__":
    unittest.main()
