from pathlib import Path
import tempfile
import unittest

import app as vocabulary_app
from book_manager import BookManager


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.temp_dir.name)
        (self.project_dir / "books").mkdir()
        (self.project_dir / "first.pdf").write_bytes(b"first")
        (self.project_dir / "books" / "second.pdf").write_bytes(b"second")

        self.original_database = vocabulary_app.DATABASE
        self.original_book_manager = vocabulary_app.book_manager
        self.original_searcher = vocabulary_app._pdf_searcher
        self.original_searcher_path = vocabulary_app._pdf_searcher_path

        vocabulary_app.DATABASE = self.project_dir / "instance" / "test.db"
        vocabulary_app.book_manager = BookManager(self.project_dir)
        vocabulary_app._pdf_searcher = None
        vocabulary_app._pdf_searcher_path = None
        vocabulary_app.init_db()
        vocabulary_app.app.config.update(TESTING=True)
        self.client = vocabulary_app.app.test_client()

        with vocabulary_app.get_db() as connection:
            connection.execute(
                "INSERT INTO study_days (study_date) VALUES ('2099-08-01')"
            )
            day_id = connection.execute(
                "SELECT id FROM study_days WHERE study_date = '2099-08-01'"
            ).fetchone()["id"]
            connection.execute(
                """
                INSERT INTO words
                    (study_day_id, word, definition, meaning, level,
                     correct_count, wrong_count, last_reviewed, next_review_date)
                VALUES (?, 'example', '例子', '例子', 4, 5, 2,
                        '2026-08-01T10:00:00', '2026-08-08')
                """,
                (day_id,),
            )
            self.word_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO learning_records (word_id, result) VALUES (?, 'known')",
                (self.word_id,),
            )

    def tearDown(self):
        vocabulary_app.DATABASE = self.original_database
        vocabulary_app.book_manager = self.original_book_manager
        vocabulary_app._pdf_searcher = self.original_searcher
        vocabulary_app._pdf_searcher_path = self.original_searcher_path
        self.temp_dir.cleanup()

    def test_settings_shows_and_switches_active_book(self):
        html = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("first", html)
        self.assertIn("second", html)
        self.assertIn("当前使用", html)
        self.assertIn("主题颜色", html)
        self.assertEqual(html.count("data-theme-option="), 4)
        self.assertIn("theme.js", html)

        response = self.client.post(
            "/settings/book",
            data={"book_key": "books/second.pdf"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            vocabulary_app.book_manager.active_book().key, "books/second.pdf"
        )
        main_html = self.client.get("/").get_data(as_text=True)
        self.assertIn("当前词书：second", main_html)

    def test_theme_word_surfaces_and_contrast_palettes_are_defined(self):
        html = self.client.get("/settings").get_data(as_text=True)
        css = (
            Path(__file__).resolve().parents[1] / "static" / "style.css"
        ).read_text(encoding="utf-8")

        self.assertIn("潮汐蓝橙", html)
        self.assertIn("紫金花园", html)
        self.assertIn("玫瑰青瓷", html)
        self.assertEqual(css.count("--word-surface:"), 4)
        self.assertIn("background: var(--word-surface)", css)
        self.assertNotIn("background: #f1f4ee", css)
        self.assertIn("#2864a8 50%, #dc7657 50%", css)
        self.assertIn("#7055a4 50%, #c9a13d 50%", css)
        self.assertIn("#a65370 50%, #4f9b92 50%", css)

    def test_reset_progress_keeps_word_but_clears_review_state(self):
        with vocabulary_app.get_db() as connection:
            connection.execute(
                "UPDATE words SET is_favorite = 1 WHERE id = ?", (self.word_id,)
            )

        response = self.client.post("/review/reset", follow_redirects=True)
        self.assertEqual(response.status_code, 200)

        with vocabulary_app.get_db() as connection:
            word = connection.execute(
                """
                SELECT level, correct_count, wrong_count, last_reviewed,
                       next_review_date, is_favorite
                FROM words WHERE id = ?
                """,
                (self.word_id,),
            ).fetchone()
            record_count = connection.execute(
                "SELECT COUNT(*) FROM learning_records"
            ).fetchone()[0]

        self.assertIsNotNone(word)
        self.assertEqual(word["level"], 1)
        self.assertEqual(word["correct_count"], 0)
        self.assertEqual(word["wrong_count"], 0)
        self.assertIsNone(word["last_reviewed"])
        self.assertEqual(word["is_favorite"], 1)
        self.assertEqual(record_count, 0)


if __name__ == "__main__":
    unittest.main()
