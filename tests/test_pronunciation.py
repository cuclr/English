from pathlib import Path
import tempfile
import unittest

import app as vocabulary_app


PROJECT_DIR = Path(__file__).resolve().parents[1]


class PronunciationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = vocabulary_app.DATABASE
        vocabulary_app.DATABASE = Path(self.temp_dir.name) / "test.db"
        vocabulary_app.init_db()
        vocabulary_app.app.config.update(TESTING=True)
        self.client = vocabulary_app.app.test_client()

        with vocabulary_app.get_db() as connection:
            day = connection.execute(
                "INSERT INTO study_days (study_date) VALUES ('2099-12-01')"
            )
            self.day_id = day.lastrowid
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, meaning)
                VALUES (?, 'pronunciation', '发音', '发音')
                """,
                (self.day_id,),
            )

    def tearDown(self):
        vocabulary_app.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def test_study_page_has_manual_auto_and_accent_controls(self):
        html = self.client.get(f"/study/{self.day_id}").get_data(as_text=True)

        self.assertIn('data-pronounce-word="pronunciation"', html)
        self.assertIn('data-auto-pronounce-word="pronunciation"', html)
        self.assertIn('data-pronunciation-accent="en-US"', html)
        self.assertIn('data-pronunciation-accent="en-GB"', html)
        self.assertIn("自动播放：关", html)
        self.assertIn("pronunciation.js", html)

    def test_vocabulary_pages_include_reusable_speaker_button(self):
        self.client.post(
            "/preferences/study-day",
            data={"study_day_id": self.day_id},
        )
        for path in ("/", "/dates", "/words/manage"):
            with self.subTest(path=path):
                html = self.client.get(path).get_data(as_text=True)
                self.assertIn('data-pronounce-word="pronunciation"', html)
                self.assertIn("pronunciation.js", html)

    def test_script_uses_free_browser_speech_and_persists_preferences(self):
        script = (PROJECT_DIR / "static" / "pronunciation.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("window.speechSynthesis", script)
        self.assertIn("new SpeechSynthesisUtterance", script)
        self.assertIn("'en-US'", script)
        self.assertIn("'en-GB'", script)
        self.assertIn("window.localStorage", script)
        self.assertIn("voiceschanged", script)
        self.assertIn("if (autoPlay && autoWord)", script)
        self.assertNotIn("fetch(", script)
        self.assertNotIn("http://", script)
        self.assertNotIn("https://", script)


if __name__ == "__main__":
    unittest.main()
