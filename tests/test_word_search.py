import json
from pathlib import Path
import tempfile
import unittest

import app as vocabulary_app


class WordSearchAndEditTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = vocabulary_app.DATABASE
        vocabulary_app.DATABASE = Path(self.temp_dir.name) / "test.db"
        vocabulary_app.init_db()
        vocabulary_app.app.config.update(TESTING=True)
        self.client = vocabulary_app.app.test_client()

        with vocabulary_app.get_db() as connection:
            first = connection.execute(
                "INSERT INTO study_days (study_date) VALUES ('2099-10-01')"
            )
            self.first_day_id = first.lastrowid
            second = connection.execute(
                "INSERT INTO study_days (study_date) VALUES ('2099-10-02')"
            )
            self.second_day_id = second.lastrowid
            atmosphere = connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, meaning, phrases)
                VALUES (?, 'atmosphere', '大气；气氛', '大气；气氛', ?)
                """,
                (self.first_day_id, json.dumps(["in the atmosphere"])),
            )
            self.atmosphere_id = atmosphere.lastrowid
            connection.execute(
                "UPDATE words SET level = 3, is_favorite = 1 WHERE id = ?",
                (self.atmosphere_id,),
            )
            connection.execute(
                "INSERT INTO learning_records (word_id, result) VALUES (?, 'known')",
                (self.atmosphere_id,),
            )
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, meaning)
                VALUES (?, 'catalog', '目录', '目录')
                """,
                (self.second_day_id,),
            )

    def tearDown(self):
        vocabulary_app.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def test_search_finds_words_across_dates_and_shows_edit_controls(self):
        response = self.client.get("/words/manage?q=atmos")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("atmosphere", html)
        self.assertIn("2099-10-01", html)
        self.assertNotIn("catalog", html)
        self.assertIn("data-word-edit-toggle", html)
        self.assertIn('class="word-edit-form"', html)
        self.assertIn("hidden", html)

    def test_word_manager_orders_results_alphabetically(self):
        with vocabulary_app.get_db() as connection:
            for word in ("Banana", "application", "apple"):
                connection.execute(
                    """
                    INSERT INTO words (study_day_id, word, definition, meaning)
                    VALUES (?, ?, '排序测试', '排序测试')
                    """,
                    (self.first_day_id, word),
                )

        html = self.client.get("/words/manage").get_data(as_text=True)
        positions = [
            html.index(f"<strong>{word}</strong>")
            for word in ("apple", "application", "atmosphere", "Banana", "catalog")
        ]

        self.assertEqual(positions, sorted(positions))

    def test_edit_updates_word_meaning_phrases_and_date(self):
        response = self.client.post(
            f"/words/{self.atmosphere_id}/edit",
            data={
                "q": "atmosphere",
                "word": "atmosphere",
                "meaning": "空气；氛围",
                "phrases": "upper atmosphere\nrelaxed atmosphere\nupper atmosphere",
                "study_day_id": self.second_day_id,
            },
            follow_redirects=True,
        )

        self.assertIn("“atmosphere”已更新", response.get_data(as_text=True))
        with vocabulary_app.get_db() as connection:
            word = connection.execute(
                "SELECT * FROM words WHERE id = ?", (self.atmosphere_id,)
            ).fetchone()
        self.assertEqual(word["study_day_id"], self.second_day_id)
        self.assertEqual(word["definition"], "空气；氛围")
        self.assertEqual(word["meaning"], "空气；氛围")
        self.assertEqual(
            json.loads(word["phrases"]),
            ["upper atmosphere", "relaxed atmosphere"],
        )
        self.assertEqual(word["level"], 3)
        self.assertEqual(word["is_favorite"], 1)
        with vocabulary_app.get_db() as connection:
            record_count = connection.execute(
                "SELECT COUNT(*) FROM learning_records WHERE word_id = ?",
                (self.atmosphere_id,),
            ).fetchone()[0]
        self.assertEqual(record_count, 1)

    def test_edit_blocks_duplicate_on_same_date(self):
        response = self.client.post(
            f"/words/{self.atmosphere_id}/edit",
            data={
                "word": "catalog",
                "meaning": "目录",
                "phrases": "",
                "study_day_id": self.second_day_id,
            },
            follow_redirects=True,
        )

        self.assertIn("已经有单词“catalog”", response.get_data(as_text=True))
        with vocabulary_app.get_db() as connection:
            word = connection.execute(
                "SELECT word, study_day_id FROM words WHERE id = ?",
                (self.atmosphere_id,),
            ).fetchone()
        self.assertEqual(word["word"], "atmosphere")
        self.assertEqual(word["study_day_id"], self.first_day_id)

    def test_duplicate_words_across_dates_are_reported(self):
        with vocabulary_app.get_db() as connection:
            connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, meaning)
                VALUES (?, 'Atmosphere', '氛围', '氛围')
                """,
                (self.second_day_id,),
            )

        html = self.client.get("/words/manage").get_data(as_text=True)

        self.assertIn("发现 1 组跨日期重复单词", html)
        self.assertIn("重复 2 条", html)
        self.assertIn("2099-10-01、2099-10-02", html)

    def test_main_page_links_to_word_manager(self):
        html = self.client.get("/").get_data(as_text=True)

        self.assertIn("单词管理", html)
        self.assertIn("/words/manage", html)


if __name__ == "__main__":
    unittest.main()
