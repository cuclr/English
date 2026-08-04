import json
from pathlib import Path
import tempfile
import unittest

import app as vocabulary_app


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
        self.assertIn('value="known" disabled', html)
        self.assertIn('value="unknown" disabled', html)

    def test_review_is_saved(self):
        response = self.client.post(
            f"/study/{self.day_id}/review",
            data={"word_id": self.first_word_id, "result": "known"},
        )

        self.assertEqual(response.status_code, 302)
        with vocabulary_app.get_db() as connection:
            record = connection.execute(
                "SELECT result FROM learning_records WHERE word_id = ?",
                (self.first_word_id,),
            ).fetchone()
        self.assertEqual(record["result"], "known")

    def test_review_rejects_word_from_another_day(self):
        with vocabulary_app.get_db() as connection:
            connection.execute(
                "INSERT INTO study_days (study_date) VALUES (?)", ("2099-10-31",)
            )
            other_day_id = connection.execute(
                "SELECT id FROM study_days WHERE study_date = ?", ("2099-10-31",)
            ).fetchone()["id"]

        response = self.client.post(
            f"/study/{other_day_id}/review",
            data={"word_id": self.first_word_id, "result": "unknown"},
        )

        self.assertEqual(response.status_code, 302)
        with vocabulary_app.get_db() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM learning_records"
            ).fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
