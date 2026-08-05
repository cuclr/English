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

    def test_empty_date_input_uses_friendly_prompt(self):
        html = self.client.get("/").get_data(as_text=True)

        self.assertIn("请选择日期", html)
        self.assertIn('class="date-input-placeholder"', html)


if __name__ == "__main__":
    unittest.main()
