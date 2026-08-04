from datetime import date, datetime, timedelta
import random
import unittest

from spaced_repetition import (
    calculate_review_update,
    calculate_weight,
    choose_weighted_word,
)


class SpacedRepetitionRulesTest(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 8, 4)
        self.base_word = {
            "id": 1,
            "level": 1,
            "correct_count": 0,
            "wrong_count": 0,
            "last_reviewed": None,
            "next_review_date": self.today.isoformat(),
        }

    def test_new_word_has_weight_five(self):
        self.assertEqual(calculate_weight(self.base_word, self.today), 5.0)

    def test_wrong_and_overdue_word_receives_priority(self):
        word = {
            **self.base_word,
            "level": 3,
            "correct_count": 2,
            "wrong_count": 2,
            "last_reviewed": "2026-07-20T10:00:00",
            "next_review_date": "2026-08-01",
        }
        self.assertEqual(calculate_weight(word, self.today), 9.0)

    def test_selection_does_not_repeat_when_another_word_exists(self):
        words = [self.base_word, {**self.base_word, "id": 2}]
        chosen = choose_weighted_word(
            words,
            previous_word_id=1,
            today=self.today,
            rng=random.Random(7),
        )
        self.assertEqual(chosen["id"], 2)

    def test_again_resets_level_and_keeps_word_due_today(self):
        word = {**self.base_word, "level": 4, "wrong_count": 2}
        update = calculate_review_update(
            word,
            "again",
            today=self.today,
            reviewed_at=datetime(2026, 8, 4, 9, 30),
        )
        self.assertEqual(update.level, 1)
        self.assertEqual(update.wrong_count, 3)
        self.assertEqual(update.next_review_date, self.today.isoformat())

    def test_vague_keeps_level_and_schedules_tomorrow(self):
        word = {**self.base_word, "level": 3}
        update = calculate_review_update(word, "vague", today=self.today)
        self.assertEqual(update.level, 3)
        self.assertEqual(
            update.next_review_date, (self.today + timedelta(days=1)).isoformat()
        )

    def test_known_and_easy_use_level_intervals(self):
        known = calculate_review_update(
            {**self.base_word, "level": 4}, "known", today=self.today
        )
        easy = calculate_review_update(
            {**self.base_word, "level": 1}, "easy", today=self.today
        )
        self.assertEqual(known.level, 5)
        self.assertEqual(
            known.next_review_date, (self.today + timedelta(days=15)).isoformat()
        )
        self.assertEqual(easy.level, 3)
        self.assertEqual(
            easy.next_review_date, (self.today + timedelta(days=8)).isoformat()
        )


if __name__ == "__main__":
    unittest.main()
