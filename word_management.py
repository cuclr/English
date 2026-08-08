"""Search, duplicate detection, and input helpers for vocabulary management."""

import sqlite3


def parse_phrases(text: str) -> list[str]:
    """Convert one-phrase-per-line input into a clean, stable list."""
    phrases = []
    seen = set()
    for line in text.splitlines():
        phrase = line.strip()
        normalized = phrase.casefold()
        if phrase and normalized not in seen:
            phrases.append(phrase)
            seen.add(normalized)
    return phrases


def search_words(
    connection: sqlite3.Connection,
    query: str,
    limit: int = 200,
) -> tuple[list[sqlite3.Row], int]:
    """Search every study date and include a global duplicate count."""
    escaped_query = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    pattern = f"%{escaped_query}%"
    total = connection.execute(
        """
        SELECT COUNT(*)
        FROM words
        WHERE word LIKE ? ESCAPE '\\' COLLATE NOCASE
        """,
        (pattern,),
    ).fetchone()[0]
    rows = connection.execute(
        """
        SELECT words.*, study_days.study_date,
               (
                   SELECT COUNT(*)
                   FROM words AS duplicates
                   WHERE LOWER(duplicates.word) = LOWER(words.word)
               ) AS duplicate_count
        FROM words
        JOIN study_days ON study_days.id = words.study_day_id
        WHERE words.word LIKE ? ESCAPE '\\' COLLATE NOCASE
        ORDER BY study_days.study_date DESC, words.word COLLATE NOCASE, words.id DESC
        LIMIT ?
        """,
        (pattern, limit),
    ).fetchall()
    return rows, total


def find_duplicate_groups(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return words saved more than once across all study dates."""
    return connection.execute(
        """
        SELECT MIN(words.word) AS word,
               COUNT(*) AS duplicate_count,
               GROUP_CONCAT(DISTINCT study_days.study_date) AS study_dates
        FROM words
        JOIN study_days ON study_days.id = words.study_day_id
        GROUP BY LOWER(words.word)
        HAVING COUNT(*) > 1
        ORDER BY duplicate_count DESC, word COLLATE NOCASE
        """
    ).fetchall()


def find_same_day_conflict(
    connection: sqlite3.Connection,
    word_id: int,
    study_day_id: int,
    word: str,
) -> sqlite3.Row | None:
    """Find another record with the same spelling on the target date."""
    return connection.execute(
        """
        SELECT words.id, study_days.study_date
        FROM words
        JOIN study_days ON study_days.id = words.study_day_id
        WHERE words.id != ?
          AND words.study_day_id = ?
          AND LOWER(words.word) = LOWER(?)
        LIMIT 1
        """,
        (word_id, study_day_id, word),
    ).fetchone()
