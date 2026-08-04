import json
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest

import app as vocabulary_app


PROJECT_DIR = Path(__file__).resolve().parents[1]


class StudyModeTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = vocabulary_app.DATABASE
        vocabulary_app.DATABASE = Path(self.temp_dir.name) / "test.db"
        vocabulary_app.init_db()
        vocabulary_app.app.config.update(TESTING=True)
        self.client = vocabulary_app.app.test_client()

        with vocabulary_app.get_db() as connection:
            connection.execute(
                "INSERT INTO study_days (study_date) VALUES (?)", ("2099-10-30",)
            )
            self.day_id = connection.execute(
                "SELECT id FROM study_days WHERE study_date = ?", ("2099-10-30",)
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, phrases)
                VALUES (?, ?, ?, ?)
                """,
                (self.day_id, "alpha", "第一", json.dumps([])),
            )
            self.first_word_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, phrases)
                VALUES (?, ?, ?, ?)
                """,
                (self.day_id, "beta", "第二", json.dumps([])),
            )

    def tearDown(self):
        vocabulary_app.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def test_study_page_excludes_previous_word_and_hides_answer(self):
        response = self.client.get(
            f"/study/{self.day_id}?exclude_word_id={self.first_word_id}"
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("beta", html)
        self.assertIn("第二", html)
        self.assertIn('id="answer" class="answer" hidden', html)
        self.assertIn('data-rating="again" aria-pressed="false"', html)
        self.assertIn('data-rating="vague" aria-pressed="false"', html)
        self.assertIn('data-rating="known" aria-pressed="false"', html)
        self.assertIn('data-rating="easy" aria-pressed="false"', html)
        self.assertIn('id="selected-rating" type="hidden" name="rating"', html)
        self.assertIn('id="review-confirmation"', html)
        self.assertNotIn('value="known" disabled', html)
        self.assertIn("等级 1", html)

    def test_rating_selection_reveals_answer_before_confirmation(self):
        response = self.client.get(f"/study/{self.day_id}")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("selectedRating.value = rating", html)
        self.assertIn("reviewConfirmation.hidden = false", html)
        self.assertIn("revealAnswer();", html)
        self.assertIn("如果判断有误，可以重新选择", html)
        self.assertIn("确认并进入下一个", html)

    def test_study_page_uses_compact_layout(self):
        css = (PROJECT_DIR / "static" / "style.css").read_text(encoding="utf-8")

        self.assertIn(".study-page { width: min(860px", css)
        self.assertIn("padding-top: 24px; padding-bottom: 40px", css)
        self.assertIn(".recall-card { padding: 20px 24px", css)
        self.assertIn(".answer { margin-top: 12px; padding-top: 12px", css)
        self.assertIn(".review-actions { margin-top: 14px; }", css)

    def test_review_is_saved(self):
        response = self.client.post(
            f"/study/{self.day_id}/review",
            data={"word_id": self.first_word_id, "rating": "known"},
        )

        self.assertEqual(response.status_code, 302)
        with vocabulary_app.get_db() as connection:
            record = connection.execute(
                "SELECT result FROM learning_records WHERE word_id = ?",
                (self.first_word_id,),
            ).fetchone()
            word = connection.execute(
                """
                SELECT level, correct_count, wrong_count, next_review_date
                FROM words WHERE id = ?
                """,
                (self.first_word_id,),
            ).fetchone()
        self.assertEqual(record["result"], "known")
        self.assertEqual(word["level"], 2)
        self.assertEqual(word["correct_count"], 1)
        self.assertEqual(word["wrong_count"], 0)
        self.assertEqual(
            word["next_review_date"], (date.today() + timedelta(days=2)).isoformat()
        )

    def test_review_rejects_future_word_from_another_day(self):
        with vocabulary_app.get_db() as connection:
            connection.execute(
                "INSERT INTO study_days (study_date) VALUES (?)", ("2099-10-31",)
            )
            other_day_id = connection.execute(
                "SELECT id FROM study_days WHERE study_date = ?", ("2099-10-31",)
            ).fetchone()["id"]
            connection.execute(
                """
                UPDATE words
                SET last_reviewed = ?, next_review_date = ?
                WHERE id = ?
                """,
                (date.today().isoformat(), "2999-01-01", self.first_word_id),
            )

        response = self.client.post(
            f"/study/{other_day_id}/review",
            data={"word_id": self.first_word_id, "rating": "again"},
        )

        self.assertEqual(response.status_code, 302)
        with vocabulary_app.get_db() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM learning_records"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_candidates_include_due_old_words_but_not_future_words(self):
        with vocabulary_app.get_db() as connection:
            connection.execute(
                "INSERT INTO study_days (study_date) VALUES (?)", ("2099-11-01",)
            )
            other_day_id = connection.execute(
                "SELECT id FROM study_days WHERE study_date = ?", ("2099-11-01",)
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO words
                    (study_day_id, word, definition, last_reviewed, next_review_date)
                VALUES (?, 'overdue', '到期', ?, '2000-01-01')
                """,
                (other_day_id, date.today().isoformat()),
            )
            overdue_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO words
                    (study_day_id, word, definition, last_reviewed, next_review_date)
                VALUES (?, 'future', '未到期', ?, '2999-01-01')
                """,
                (other_day_id, date.today().isoformat()),
            )
            future_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]

            candidates = vocabulary_app.get_review_candidates(
                connection, self.day_id, date.today()
            )

        candidate_ids = {item["id"] for item in candidates}
        self.assertIn(overdue_id, candidate_ids)
        self.assertNotIn(future_id, candidate_ids)


if __name__ == "__main__":
    unittest.main()
