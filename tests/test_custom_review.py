from pathlib import Path
import tempfile
import unittest

import app as vocabulary_app


class CustomReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = vocabulary_app.DATABASE
        vocabulary_app.DATABASE = Path(self.temp_dir.name) / "test.db"
        vocabulary_app.init_db()
        vocabulary_app.app.config.update(TESTING=True)
        self.client = vocabulary_app.app.test_client()

        with vocabulary_app.get_db() as connection:
            self.day_ids = []
            for study_date in ("2099-12-01", "2099-12-02", "2099-12-03"):
                cursor = connection.execute(
                    "INSERT INTO study_days (study_date) VALUES (?)",
                    (study_date,),
                )
                self.day_ids.append(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, level)
                VALUES (?, 'alpha', '第一', 1)
                """,
                (self.day_ids[0],),
            )
            self.alpha_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, level)
                VALUES (?, 'beta', '第二', 5)
                """,
                (self.day_ids[0],),
            )
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, level)
                VALUES (?, 'gamma', '第三', 2)
                """,
                (self.day_ids[1],),
            )

    def tearDown(self):
        vocabulary_app.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def test_range_page_supports_individual_selection_and_select_all(self):
        html = self.client.get("/study/range").get_data(as_text=True)

        self.assertIn("专项背诵", html)
        self.assertIn("组合筛选", html)
        self.assertIn("生词簿", html)
        self.assertIn('id="select-all-days"', html)
        self.assertEqual(html.count('name="study_day_ids"'), 3)
        self.assertIn("`${selected.length} 个日期`", html)
        self.assertIn("开始背诵 ${wordCount} 个单词", html)
        self.assertIn("disabled", html)

    def test_selected_dates_start_a_custom_round(self):
        response = self.client.post(
            "/study/range",
            data={"study_day_ids": [self.day_ids[0], self.day_ids[1]]},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/study/range/session"))
        with self.client.session_transaction() as client_session:
            state = client_session["custom_review"]
        self.assertEqual(state["study_day_ids"], self.day_ids[:2])

        html = self.client.get(response.location).get_data(as_text=True)
        self.assertIn("组合筛选背诵", html)
        self.assertIn("3 个单词", html)
        self.assertIn('action="/study/range/review"', html)
        self.assertIn("来自 2099-12-0", html)

    def test_unknown_word_remains_until_it_is_marked_known(self):
        self.client.post(
            "/study/range",
            data={"study_day_ids": self.day_ids[:2]},
        )
        with self.client.session_transaction() as client_session:
            state = client_session["custom_review"]

        unknown_response = self.client.post(
            "/study/range/review",
            data={"word_id": self.alpha_id, "rating": "again"},
        )
        self.assertEqual(unknown_response.status_code, 302)
        next_html = self.client.get(unknown_response.location).get_data(as_text=True)
        self.assertNotIn("<h2>alpha</h2>", next_html)

        with vocabulary_app.get_db() as connection:
            candidates = vocabulary_app.get_custom_review_candidates(
                connection,
                state["study_day_ids"],
                state["after_record_id"],
            )
        self.assertIn(self.alpha_id, {word["id"] for word in candidates})

        known_response = self.client.post(
            "/study/range/review",
            data={"word_id": self.alpha_id, "rating": "known"},
        )
        self.assertEqual(known_response.status_code, 302)

        with vocabulary_app.get_db() as connection:
            candidates = vocabulary_app.get_custom_review_candidates(
                connection,
                state["study_day_ids"],
                state["after_record_id"],
            )
            records = connection.execute(
                "SELECT result FROM learning_records WHERE word_id = ? ORDER BY id",
                (self.alpha_id,),
            ).fetchall()
        self.assertNotIn(self.alpha_id, {word["id"] for word in candidates})
        self.assertEqual([record["result"] for record in records], ["unknown", "known"])

    def test_empty_range_is_rejected(self):
        response = self.client.post("/study/range", data={}, follow_redirects=True)

        self.assertIn("请至少选择一个日期、标签或生词簿筛选条件", response.get_data(as_text=True))

    def test_tags_dates_and_favorites_are_intersected_for_custom_review(self):
        with vocabulary_app.get_db() as connection:
            focus_tag = connection.execute(
                "INSERT INTO tags (name) VALUES ('自定义重点')"
            ).lastrowid
            connection.execute(
                "INSERT INTO word_tags (word_id, tag_id) VALUES (?, ?)",
                (self.alpha_id, focus_tag),
            )
            connection.execute(
                "UPDATE words SET is_favorite = 1 WHERE id = ?", (self.alpha_id,)
            )

        response = self.client.post(
            "/study/range",
            data={
                "study_day_ids": [self.day_ids[0]],
                "tag_ids": [focus_tag],
                "favorite_only": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as client_session:
            state = client_session["custom_review"]
        self.assertEqual(state["study_day_ids"], [self.day_ids[0]])
        self.assertEqual(state["tag_ids"], [focus_tag])
        self.assertTrue(state["favorite_only"])
        html = self.client.get(response.location).get_data(as_text=True)
        self.assertIn("自定义重点", html)
        self.assertIn("仅生词簿", html)
        self.assertIn("1 个单词", html)

    def test_tag_filter_can_start_review_without_selecting_a_date(self):
        with vocabulary_app.get_db() as connection:
            tag_id = connection.execute(
                "INSERT INTO tags (name) VALUES ('跨日期标签')"
            ).lastrowid
            gamma_id = connection.execute(
                "SELECT id FROM words WHERE word = 'gamma'"
            ).fetchone()["id"]
            connection.executemany(
                "INSERT INTO word_tags (word_id, tag_id) VALUES (?, ?)",
                [(self.alpha_id, tag_id), (gamma_id, tag_id)],
            )

        response = self.client.post("/study/range", data={"tag_ids": [tag_id]})

        self.assertEqual(response.status_code, 302)
        html = self.client.get(response.location).get_data(as_text=True)
        self.assertIn("跨日期标签", html)
        self.assertIn("2 个单词", html)

    def test_multiple_tag_filters_require_every_selected_tag(self):
        with vocabulary_app.get_db() as connection:
            writing_tag = connection.execute(
                "INSERT INTO tags (name) VALUES ('写作')"
            ).lastrowid
            exam_tag = connection.execute(
                "INSERT INTO tags (name) VALUES ('考前')"
            ).lastrowid
            gamma_id = connection.execute(
                "SELECT id FROM words WHERE word = 'gamma'"
            ).fetchone()["id"]
            connection.executemany(
                "INSERT INTO word_tags (word_id, tag_id) VALUES (?, ?)",
                [
                    (self.alpha_id, writing_tag),
                    (self.alpha_id, exam_tag),
                    (gamma_id, writing_tag),
                ],
            )

        response = self.client.post(
            "/study/range",
            data={"tag_ids": [writing_tag, exam_tag]},
        )

        self.assertEqual(response.status_code, 302)
        html = self.client.get(response.location).get_data(as_text=True)
        self.assertIn("标签：写作 + 考前", html)
        self.assertIn("1 个单词", html)

    def test_study_feedback_auto_dismisses(self):
        html = self.client.get(f"/study/{self.day_ids[0]}").get_data(as_text=True)

        self.assertIn("message.classList.add('message-leaving')", html)
        self.assertIn("}, 3500);", html)

    def test_range_selector_uses_single_column_on_small_screens(self):
        css_path = Path(__file__).resolve().parents[1] / "static" / "style.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertIn(".review-range-page", css)
        self.assertIn(".range-day-list { grid-template-columns: 1fr; }", css)
        self.assertIn(".select-all-option input, .range-day-option input", css)

    def test_favorite_words_have_their_own_mastery_review(self):
        with vocabulary_app.get_db() as connection:
            connection.execute(
                "UPDATE words SET is_favorite = 1 WHERE id = ?",
                (self.alpha_id,),
            )

        setup_html = self.client.get("/study/range").get_data(as_text=True)
        self.assertIn("1 个单词", setup_html)
        self.assertIn('action="/study/favorites"', setup_html)
        self.assertIn("查看收藏的单词", setup_html)
        self.assertIn("alpha", setup_html)
        self.assertIn(f'/words/{self.alpha_id}/favorite', setup_html)

        start_response = self.client.post("/study/favorites")
        self.assertEqual(start_response.status_code, 302)
        self.assertTrue(start_response.location.endswith("/study/favorites/session"))

        study_html = self.client.get(start_response.location).get_data(as_text=True)
        self.assertIn("生词簿背诵", study_html)
        self.assertIn('action="/study/favorites/review"', study_html)
        self.assertIn("favorite-indicator is-active", study_html)
        self.assertNotIn(
            f'action="/words/{self.alpha_id}/favorite"', study_html
        )

        unknown_response = self.client.post(
            "/study/favorites/review",
            data={"word_id": self.alpha_id, "rating": "again"},
        )
        self.assertEqual(unknown_response.status_code, 302)
        self.assertIn(
            "<h2>alpha</h2>",
            self.client.get(unknown_response.location).get_data(as_text=True),
        )

        known_response = self.client.post(
            "/study/favorites/review",
            data={"word_id": self.alpha_id, "rating": "known"},
        )
        completed_html = self.client.get(
            known_response.location
        ).get_data(as_text=True)
        self.assertIn("生词簿中的单词已经全部掌握", completed_html)
        self.assertIn("重新复习生词簿", completed_html)


if __name__ == "__main__":
    unittest.main()
