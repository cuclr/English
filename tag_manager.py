"""Tag persistence and shared word-filter helpers."""

from collections.abc import Iterable
import sqlite3


MAX_TAG_NAME_LENGTH = 40


def normalize_tag_name(name: str) -> str:
    """Normalize user-entered tag names while preserving readable casing."""
    return " ".join(name.strip().split())


def validate_tag_name(name: str) -> str:
    normalized = normalize_tag_name(name)
    if not normalized:
        raise ValueError("请输入标签名称。")
    if len(normalized) > MAX_TAG_NAME_LENGTH:
        raise ValueError(f"标签名称不能超过 {MAX_TAG_NAME_LENGTH} 个字符。")
    return normalized


def list_tags(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """List every custom tag with its current word count."""
    return connection.execute(
        """
        SELECT tags.id, tags.name, COUNT(word_tags.word_id) AS word_count
        FROM tags
        LEFT JOIN word_tags ON word_tags.tag_id = tags.id
        GROUP BY tags.id
        ORDER BY tags.name COLLATE NOCASE ASC
        """
    ).fetchall()


def replace_word_tags(
    connection: sqlite3.Connection,
    word_id: int,
    tag_ids: Iterable[int],
) -> None:
    """Replace all tag links for one word after validating every tag."""
    normalized_ids = sorted({int(tag_id) for tag_id in tag_ids if int(tag_id) > 0})
    if normalized_ids:
        placeholders = ", ".join("?" for _ in normalized_ids)
        found_count = connection.execute(
            f"SELECT COUNT(*) FROM tags WHERE id IN ({placeholders})",
            normalized_ids,
        ).fetchone()[0]
        if found_count != len(normalized_ids):
            raise ValueError("所选标签不存在，请刷新页面后重试。")

    connection.execute("DELETE FROM word_tags WHERE word_id = ?", (word_id,))
    connection.executemany(
        "INSERT INTO word_tags (word_id, tag_id) VALUES (?, ?)",
        [(word_id, tag_id) for tag_id in normalized_ids],
    )


def tags_by_word(
    connection: sqlite3.Connection,
    word_ids: Iterable[int],
) -> dict[int, list[dict]]:
    normalized_ids = sorted({int(word_id) for word_id in word_ids})
    if not normalized_ids:
        return {}
    placeholders = ", ".join("?" for _ in normalized_ids)
    rows = connection.execute(
        f"""
        SELECT word_tags.word_id, tags.id, tags.name
        FROM word_tags
        JOIN tags ON tags.id = word_tags.tag_id
        WHERE word_tags.word_id IN ({placeholders})
        ORDER BY tags.name COLLATE NOCASE ASC
        """,
        normalized_ids,
    ).fetchall()
    result: dict[int, list[dict]] = {}
    for row in rows:
        result.setdefault(int(row["word_id"]), []).append(
            {"id": int(row["id"]), "name": row["name"]}
        )
    return result


def build_word_filter(
    study_day_ids: Iterable[int] = (),
    tag_ids: Iterable[int] = (),
    favorite_only: bool = False,
    alias: str = "words",
) -> tuple[str, list[int]]:
    """Build an AND filter; selected tags must all be present on each word."""
    day_ids = sorted({int(day_id) for day_id in study_day_ids if int(day_id) > 0})
    selected_tag_ids = sorted(
        {int(tag_id) for tag_id in tag_ids if int(tag_id) > 0}
    )
    clauses = []
    parameters: list[int] = []

    if day_ids:
        placeholders = ", ".join("?" for _ in day_ids)
        clauses.append(f"{alias}.study_day_id IN ({placeholders})")
        parameters.extend(day_ids)
    if favorite_only:
        clauses.append(f"{alias}.is_favorite = 1")
    if selected_tag_ids:
        placeholders = ", ".join("?" for _ in selected_tag_ids)
        clauses.append(
            f"""
            EXISTS (
                SELECT 1
                FROM word_tags AS selected_word_tags
                WHERE selected_word_tags.word_id = {alias}.id
                  AND selected_word_tags.tag_id IN ({placeholders})
                GROUP BY selected_word_tags.word_id
                HAVING COUNT(DISTINCT selected_word_tags.tag_id) = ?
            )
            """
        )
        parameters.extend(selected_tag_ids)
        parameters.append(len(selected_tag_ids))

    return (" AND ".join(clauses) if clauses else "1 = 1"), parameters
