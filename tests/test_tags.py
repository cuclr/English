from pathlib import Path
import tempfile
import unittest

import app as vocabulary_app


class TagManagementTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = vocabulary_app.DATABASE
        vocabulary_app.DATABASE = Path(self.temp_dir.name) / "test.db"
        vocabulary_app.init_db()
        vocabulary_app.app.config.update(TESTING=True)
        self.client = vocabulary_app.app.test_client()

        with vocabulary_app.get_db() as connection:
            first = connection.execute(
                "INSERT INTO study_days (study_date) VALUES ('2099-08-01')"
            )
            self.first_day_id = first.lastrowid
            second = connection.execute(
                "INSERT INTO study_days (study_date) VALUES ('2099-08-02')"
            )
            self.second_day_id = second.lastrowid
            alpha = connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, meaning, is_favorite)
                VALUES (?, 'alpha', '第一', '第一', 1)
                """,
                (self.first_day_id,),
            )
            self.alpha_id = alpha.lastrowid
            beta = connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, meaning)
                VALUES (?, 'beta', '第二', '第二')
                """,
                (self.first_day_id,),
            )
            self.beta_id = beta.lastrowid
            gamma = connection.execute(
                """
                INSERT INTO words (study_day_id, word, definition, meaning, is_favorite)
                VALUES (?, 'gamma', '第三', '第三', 1)
                """,
                (self.second_day_id,),
            )
            self.gamma_id = gamma.lastrowid

    def tearDown(self):
        vocabulary_app.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def create_tag(self, name="写作词汇"):
        self.client.post("/tags", data={"name": name})
        with vocabulary_app.get_db() as connection:
            return connection.execute(
                "SELECT id FROM tags WHERE name = ?", (name,)
            ).fetchone()["id"]

    def assign_tags(self, word_id, *tag_ids):
        with vocabulary_app.get_db() as connection:
            connection.executemany(
                "INSERT INTO word_tags (word_id, tag_id) VALUES (?, ?)",
                [(word_id, tag_id) for tag_id in tag_ids],
            )

    def test_schema_upgrade_creates_empty_tag_tables_without_presets(self):
        with vocabulary_app.get_db() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            tag_count = connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

        self.assertIn("tags", tables)
        self.assertIn("word_tags", tables)
        self.assertEqual(tag_count, 0)

    def test_custom_tag_can_be_created_renamed_and_duplicate_is_rejected(self):
        tag_id = self.create_tag()

        duplicate = self.client.post(
            "/tags", data={"name": "写作词汇"}, follow_redirects=True
        )
        self.assertIn("这个标签已经存在", duplicate.get_data(as_text=True))

        renamed = self.client.post(
            f"/tags/{tag_id}/edit",
            data={"name": "作文重点"},
            follow_redirects=True,
        )
        self.assertIn("标签已修改为“作文重点”", renamed.get_data(as_text=True))

    def test_editing_word_assigns_multiple_tags(self):
        writing_id = self.create_tag()
        exam_id = self.create_tag("考前复习")

        response = self.client.post(
            f"/words/{self.alpha_id}/edit",
            data={
                "word": "alpha",
                "meaning": "第一",
                "phrases": "",
                "study_day_id": self.first_day_id,
                "tag_ids": [writing_id, exam_id],
            },
            follow_redirects=True,
        )

        self.assertIn("“alpha”已更新", response.get_data(as_text=True))
        with vocabulary_app.get_db() as connection:
            saved_ids = {
                row["tag_id"]
                for row in connection.execute(
                    "SELECT tag_id FROM word_tags WHERE word_id = ?", (self.alpha_id,)
                )
            }
        self.assertEqual(saved_ids, {writing_id, exam_id})

    def test_word_manager_combines_date_tag_and_favorite_filters(self):
        writing_id = self.create_tag()
        self.assign_tags(self.alpha_id, writing_id)
        self.assign_tags(self.gamma_id, writing_id)

        html = self.client.get(
            f"/words/manage?study_day_id={self.first_day_id}&tag_id={writing_id}&favorite=1"
        ).get_data(as_text=True)

        self.assertIn("alpha", html)
        self.assertNotIn("<strong>beta</strong>", html)
        self.assertNotIn("<strong>gamma</strong>", html)
        self.assertIn("写作词汇", html)

    def test_deleting_tag_keeps_words_and_removes_only_links(self):
        tag_id = self.create_tag()
        self.assign_tags(self.alpha_id, tag_id)

        response = self.client.post(
            f"/tags/{tag_id}/delete", follow_redirects=True
        )

        self.assertIn("1 个单词仍然保留", response.get_data(as_text=True))
        with vocabulary_app.get_db() as connection:
            word_count = connection.execute(
                "SELECT COUNT(*) FROM words WHERE id = ?", (self.alpha_id,)
            ).fetchone()[0]
            link_count = connection.execute(
                "SELECT COUNT(*) FROM word_tags WHERE tag_id = ?", (tag_id,)
            ).fetchone()[0]
        self.assertEqual(word_count, 1)
        self.assertEqual(link_count, 0)


if __name__ == "__main__":
    unittest.main()
