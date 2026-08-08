from datetime import date
from pathlib import Path
import tempfile
import unittest

import app as vocabulary_app
from learning_statistics import build_learning_statistics, normalize_period


class LearningStatisticsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database = vocabulary_app.DATABASE
        vocabulary_app.DATABASE = Path(self.temp_dir.name) / "test.db"
        vocabulary_app.init_db()
        vocabulary_app.app.config.update(TESTING=True)
        self.client = vocabulary_app.app.test_client()

        with vocabulary_app.get_db() as connection:
            day_id = connection.execute(
                "INSERT INTO study_days (study_date) VALUES ('2026-08-01')"
            ).lastrowid
            self.word_ids = []
            for level in (1, 3, 3, 5):
                word_id = connection.execute(
                    """
                    INSERT INTO words (study_day_id, word, definition, level)
                    VALUES (?, ?, '释义', ?)
                    """,
                    (day_id, f"word-{level}-{len(self.word_ids)}", level),
                ).lastrowid
                self.word_ids.append(word_id)

    def tearDown(self):
        vocabulary_app.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def add_record(self, word_id: int, result: str, reviewed_at: str) -> None:
        with vocabulary_app.get_db() as connection:
            connection.execute(
                """
                INSERT INTO learning_records (word_id, result, reviewed_at)
                VALUES (?, ?, ?)
                """,
                (word_id, result, reviewed_at),
            )

    def test_statistics_calculate_summary_streak_levels_and_weekly_trend(self):
        records = [
            (self.word_ids[0], "known", "2026-08-08T09:00:00"),
            (self.word_ids[1], "unknown", "2026-08-08T09:01:00"),
            (self.word_ids[2], "known", "2026-08-07T09:00:00"),
            (self.word_ids[3], "unknown", "2026-08-06T09:00:00"),
            (self.word_ids[0], "known", "2026-08-04T09:00:00"),
        ]
        for record in records:
            self.add_record(*record)

        with vocabulary_app.get_db() as connection:
            statistics = build_learning_statistics(
                connection,
                today=date(2026, 8, 8),
                period_days=7,
            )

        self.assertEqual(statistics["today"]["review_count"], 2)
        self.assertEqual(statistics["today"]["accuracy"], 50.0)
        self.assertEqual(statistics["all_time"]["accuracy"], 60.0)
        self.assertEqual(statistics["all_time"]["error_rate"], 40.0)
        self.assertEqual(statistics["streak_days"], 3)
        self.assertEqual(statistics["active_day_count"], 4)
        self.assertEqual(statistics["total_words"], 4)
        self.assertEqual(
            [item["count"] for item in statistics["level_distribution"]],
            [1, 0, 2, 0, 1],
        )
        self.assertEqual(len(statistics["trend"]), 7)
        self.assertEqual(statistics["period"]["review_count"], 5)
        self.assertEqual(statistics["trend"][-1]["review_count"], 2)

    def test_streak_remains_active_before_today_first_review(self):
        self.add_record(self.word_ids[0], "known", "2026-08-07 20:00:00")
        self.add_record(self.word_ids[1], "known", "2026-08-06 20:00:00")

        with vocabulary_app.get_db() as connection:
            statistics = build_learning_statistics(
                connection,
                today=date(2026, 8, 8),
            )

        self.assertEqual(statistics["today"]["review_count"], 0)
        self.assertEqual(statistics["streak_days"], 2)

    def test_empty_statistics_are_zero_filled(self):
        with vocabulary_app.get_db() as connection:
            statistics = build_learning_statistics(
                connection,
                today=date(2026, 8, 8),
                period_days=30,
            )

        self.assertEqual(statistics["all_time"]["review_count"], 0)
        self.assertEqual(statistics["streak_days"], 0)
        self.assertEqual(len(statistics["trend"]), 30)
        self.assertTrue(all(day["review_count"] == 0 for day in statistics["trend"]))

    def test_period_is_limited_to_week_or_month(self):
        self.assertEqual(normalize_period("30"), 30)
        self.assertEqual(normalize_period("14"), 7)
        self.assertEqual(normalize_period("invalid"), 7)

    def test_statistics_page_supports_week_and_month_views(self):
        response = self.client.get("/statistics?period=30")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("学习统计", html)
        self.assertIn("今日复习", html)
        self.assertIn("熟练度等级分布", html)
        self.assertIn("最近 30 天", html)
        self.assertIn('href="/statistics?period=7"', html)
        self.assertIn("复习数量按作答次数统计", html)

    def test_home_page_links_to_statistics(self):
        html = self.client.get("/").get_data(as_text=True)

        self.assertIn('href="/statistics"', html)
        self.assertIn("学习统计", html)


if __name__ == "__main__":
    unittest.main()
