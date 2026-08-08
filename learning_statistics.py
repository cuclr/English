"""Read-only learning statistics built from review records and word levels."""

from datetime import date, timedelta
import sqlite3

from spaced_repetition import level_label


SUPPORTED_PERIODS = {7, 30}


def normalize_period(value: object, default: int = 7) -> int:
    """Return a supported trend period without exposing arbitrary query ranges."""
    try:
        period = int(value)
    except (TypeError, ValueError):
        return default
    return period if period in SUPPORTED_PERIODS else default


def _percentage(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total else 0.0


def _calculate_streak(review_dates: set[date], today: date) -> int:
    """Keep an active streak through today, or through yesterday before studying."""
    cursor = today if today in review_dates else today - timedelta(days=1)
    streak = 0
    while cursor in review_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def build_learning_statistics(
    connection: sqlite3.Connection,
    *,
    today: date | None = None,
    period_days: int = 7,
) -> dict:
    """Build all summary, level, and daily-trend values for the statistics page."""
    today = today or date.today()
    period_days = normalize_period(period_days)
    today_text = today.isoformat()
    period_start = today - timedelta(days=period_days - 1)

    totals = connection.execute(
        """
        SELECT COUNT(*) AS review_count,
               SUM(CASE WHEN result = 'known' THEN 1 ELSE 0 END) AS correct_count,
               SUM(CASE WHEN result = 'unknown' THEN 1 ELSE 0 END) AS wrong_count
        FROM learning_records
        """
    ).fetchone()
    total_reviews = int(totals["review_count"] or 0)
    total_correct = int(totals["correct_count"] or 0)
    total_wrong = int(totals["wrong_count"] or 0)

    today_summary = connection.execute(
        """
        SELECT COUNT(*) AS review_count,
               SUM(CASE WHEN result = 'known' THEN 1 ELSE 0 END) AS correct_count,
               SUM(CASE WHEN result = 'unknown' THEN 1 ELSE 0 END) AS wrong_count
        FROM learning_records
        WHERE SUBSTR(reviewed_at, 1, 10) = ?
        """,
        (today_text,),
    ).fetchone()

    active_dates = {
        date.fromisoformat(row["review_date"])
        for row in connection.execute(
            """
            SELECT DISTINCT SUBSTR(reviewed_at, 1, 10) AS review_date
            FROM learning_records
            WHERE SUBSTR(reviewed_at, 1, 10) <= ?
            """,
            (today_text,),
        ).fetchall()
        if row["review_date"]
    }

    level_counts = {
        int(row["level"]): int(row["word_count"])
        for row in connection.execute(
            """
            SELECT level, COUNT(*) AS word_count
            FROM words
            GROUP BY level
            """
        ).fetchall()
    }
    total_words = sum(level_counts.values())
    level_distribution = [
        {
            "level": level,
            "label": level_label(level),
            "count": level_counts.get(level, 0),
            "percent": _percentage(level_counts.get(level, 0), total_words),
        }
        for level in range(1, 6)
    ]

    trend_rows = connection.execute(
        """
        SELECT SUBSTR(reviewed_at, 1, 10) AS review_date,
               COUNT(*) AS review_count,
               SUM(CASE WHEN result = 'known' THEN 1 ELSE 0 END) AS correct_count,
               SUM(CASE WHEN result = 'unknown' THEN 1 ELSE 0 END) AS wrong_count
        FROM learning_records
        WHERE SUBSTR(reviewed_at, 1, 10) BETWEEN ? AND ?
        GROUP BY SUBSTR(reviewed_at, 1, 10)
        ORDER BY review_date ASC
        """,
        (period_start.isoformat(), today_text),
    ).fetchall()
    trend_by_date = {row["review_date"]: row for row in trend_rows}
    max_daily_reviews = max(
        (int(row["review_count"]) for row in trend_rows),
        default=0,
    )
    trend = []
    for offset in range(period_days):
        current = period_start + timedelta(days=offset)
        row = trend_by_date.get(current.isoformat())
        review_count = int(row["review_count"] or 0) if row else 0
        correct_count = int(row["correct_count"] or 0) if row else 0
        wrong_count = int(row["wrong_count"] or 0) if row else 0
        trend.append(
            {
                "date": current.isoformat(),
                "label": current.strftime("%m-%d"),
                "review_count": review_count,
                "correct_count": correct_count,
                "wrong_count": wrong_count,
                "accuracy": _percentage(correct_count, review_count),
                "bar_percent": (
                    round(review_count / max_daily_reviews * 100)
                    if max_daily_reviews
                    else 0
                ),
                "show_label": period_days == 7 or offset in {0, period_days - 1}
                or (offset + 1) % 5 == 0,
            }
        )

    period_reviews = sum(day["review_count"] for day in trend)
    period_correct = sum(day["correct_count"] for day in trend)
    period_wrong = sum(day["wrong_count"] for day in trend)
    today_reviews = int(today_summary["review_count"] or 0)
    today_correct = int(today_summary["correct_count"] or 0)

    return {
        "today": {
            "review_count": today_reviews,
            "correct_count": today_correct,
            "wrong_count": int(today_summary["wrong_count"] or 0),
            "accuracy": _percentage(today_correct, today_reviews),
        },
        "all_time": {
            "review_count": total_reviews,
            "correct_count": total_correct,
            "wrong_count": total_wrong,
            "accuracy": _percentage(total_correct, total_reviews),
            "error_rate": _percentage(total_wrong, total_reviews),
        },
        "streak_days": _calculate_streak(active_dates, today),
        "active_day_count": len(active_dates),
        "total_words": total_words,
        "level_distribution": level_distribution,
        "period_days": period_days,
        "period": {
            "review_count": period_reviews,
            "correct_count": period_correct,
            "wrong_count": period_wrong,
            "accuracy": _percentage(period_correct, period_reviews),
        },
        "trend": trend,
    }
