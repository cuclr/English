from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import app as vocabulary_app


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
            f'value="{self.first_day_id}" selected',
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
            f'value="{self.second_day_id}" selected',
            html,
        )


if __name__ == "__main__":
    unittest.main()
