"""Pure scheduling and weighted selection rules for vocabulary reviews."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import random
from typing import Mapping, Sequence


LEVEL_INTERVALS = {
    1: 1,
    2: 2,
    3: 4,
    4: 7,
    5: 15,
}

LEVEL_LABELS = {
    1: "完全不会",
    2: "有印象",
    3: "基本认识",
    4: "较熟练",
    5: "已经掌握",
}

VALID_RATINGS = {"again", "vague", "known", "easy"}


@dataclass(frozen=True)
class ReviewUpdate:
    level: int
    correct_count: int
    wrong_count: int
    last_reviewed: str
    next_review_date: str


def calculate_weight(word: Mapping, today: date | None = None) -> float:
    """Calculate a review weight, clamped to a minimum of one."""
    today = today or date.today()
    level = _clamp_level(int(word.get("level") or 1))
    correct_count = int(word.get("correct_count") or 0)
    wrong_count = int(word.get("wrong_count") or 0)
    last_reviewed = word.get("last_reviewed")

    is_new = not last_reviewed and correct_count == 0 and wrong_count == 0
    weight = 5.0 if is_new else 3.0
    weight += wrong_count * 2
    weight -= (level - 1) * 0.5

    next_review_date = _parse_date(word.get("next_review_date"))
    if next_review_date and next_review_date < today:
        weight += 3

    return max(1.0, weight)


def choose_weighted_word(
    words: Sequence[Mapping],
    previous_word_id: int | None = None,
    *,
    today: date | None = None,
    rng: random.Random | None = None,
) -> Mapping | None:
    """Choose by weight without immediately repeating a word when possible."""
    if not words:
        return None

    candidates = list(words)
    if previous_word_id is not None and len(candidates) > 1:
        without_previous = [
            word for word in candidates if int(word["id"]) != previous_word_id
        ]
        if without_previous:
            candidates = without_previous

    generator = rng or random
    weights = [calculate_weight(word, today=today) for word in candidates]
    return generator.choices(candidates, weights=weights, k=1)[0]


def calculate_review_update(
    word: Mapping,
    rating: str,
    *,
    today: date | None = None,
    reviewed_at: datetime | None = None,
) -> ReviewUpdate:
    """Apply one of the four review outcomes without touching the database."""
    if rating not in VALID_RATINGS:
        raise ValueError("无效的复习结果。")

    today = today or date.today()
    reviewed_at = reviewed_at or datetime.now()
    level = _clamp_level(int(word.get("level") or 1))
    correct_count = int(word.get("correct_count") or 0)
    wrong_count = int(word.get("wrong_count") or 0)

    if rating == "again":
        level = 1
        wrong_count += 1
        next_date = today
    elif rating == "vague":
        next_date = today + timedelta(days=1)
    elif rating == "known":
        level = min(5, level + 1)
        correct_count += 1
        next_date = today + timedelta(days=LEVEL_INTERVALS[level])
    else:
        level = min(5, level + 2)
        correct_count += 1
        next_date = today + timedelta(days=LEVEL_INTERVALS[level] * 2)

    return ReviewUpdate(
        level=level,
        correct_count=correct_count,
        wrong_count=wrong_count,
        last_reviewed=reviewed_at.isoformat(timespec="seconds"),
        next_review_date=next_date.isoformat(),
    )


def level_label(level: int) -> str:
    return LEVEL_LABELS[_clamp_level(level)]


def _clamp_level(level: int) -> int:
    return min(5, max(1, level))


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
