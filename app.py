from datetime import date, timedelta
from pathlib import Path
import json
import sqlite3

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for

from backup_manager import BackupError, DatabaseBackupManager
from book_manager import BookManager, BookManagerError
from pdf_search import PdfSearchError, PdfSearcher
from spaced_repetition import (
    calculate_review_update,
    choose_weighted_word,
    level_label,
)
from tag_manager import (
    build_word_filter,
    list_tags,
    replace_word_tags,
    tags_by_word,
    validate_tag_name,
)
from remote_access import RemoteAccessManager
from word_management import (
    find_duplicate_groups,
    find_same_day_conflict,
    parse_phrases,
    search_words,
)


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "instance" / "vocabulary.db"

app = Flask(__name__)
remote_access_manager = RemoteAccessManager(
    BASE_DIR / "instance" / "remote_access.json"
)
app.config["SECRET_KEY"] = remote_access_manager.secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
book_manager = BookManager(BASE_DIR)
_pdf_searcher: PdfSearcher | None = None
_pdf_searcher_path: Path | None = None
RATING_LABELS = {
    "again": "不会",
    "vague": "模糊",
    "known": "认识",
    "easy": "熟练",
}
WORD_SEARCH_LIMIT = 200


class DatabaseConnection(sqlite3.Connection):
    """Commit or roll back a context-managed connection, then always close it."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


@app.before_request
def require_access_password():
    """Require a password whenever remote access has been configured."""
    if app.config.get("TESTING") and not app.config.get("FORCE_REMOTE_AUTH"):
        return None
    if request.endpoint in {"login", "health_check", "static"}:
        return None
    if not remote_access_manager.is_configured():
        return None
    if session.get("access_authenticated") is True:
        return None
    return redirect(url_for("login", next=request.full_path.rstrip("?")))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate browsers before allowing access to personal study data."""
    if not remote_access_manager.is_configured():
        return render_template("login.html", configuration_missing=True), 503

    error = None
    if request.method == "POST":
        if remote_access_manager.verify_password(request.form.get("password", "")):
            session.clear()
            session["access_authenticated"] = True
            session.permanent = True
            destination = request.form.get("next", "")
            if not destination.startswith("/") or destination.startswith("//"):
                destination = url_for("index")
            return redirect(destination)
        error = "密码不正确，请重新输入。"

    return render_template(
        "login.html",
        configuration_missing=False,
        error=error,
        next_url=request.form.get("next")
        or request.args.get("next", url_for("index")),
    )


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def get_db() -> sqlite3.Connection:
    """Create a database connection whose rows can be accessed by column name."""
    DATABASE.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DATABASE, factory=DatabaseConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    """Create the MVP tables when they do not exist yet."""
    with get_db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS study_days (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                study_date TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                study_day_id INTEGER NOT NULL,
                word TEXT NOT NULL,
                definition TEXT NOT NULL DEFAULT '',
                meaning TEXT NOT NULL DEFAULT '',
                phrases TEXT NOT NULL DEFAULT '[]',
                source_page INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_date TEXT,
                level INTEGER NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 5),
                correct_count INTEGER NOT NULL DEFAULT 0,
                wrong_count INTEGER NOT NULL DEFAULT 0,
                last_reviewed TEXT,
                next_review_date TEXT,
                is_favorite INTEGER NOT NULL DEFAULT 0
                    CHECK (is_favorite IN (0, 1)),
                FOREIGN KEY (study_day_id) REFERENCES study_days (id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS learning_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word_id INTEGER NOT NULL,
                result TEXT NOT NULL CHECK (result IN ('known', 'unknown')),
                reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (word_id) REFERENCES words (id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS word_tags (
                word_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (word_id, tag_id),
                FOREIGN KEY (word_id) REFERENCES words (id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags (id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_word_tags_tag_id
                ON word_tags (tag_id);
            """
        )
        # Keep databases created by the first MVP compatible with this version.
        existing_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(words)")
        }
        migrations = {
            "definition": "ALTER TABLE words ADD COLUMN definition TEXT NOT NULL DEFAULT ''",
            "meaning": "ALTER TABLE words ADD COLUMN meaning TEXT NOT NULL DEFAULT ''",
            "phrases": "ALTER TABLE words ADD COLUMN phrases TEXT NOT NULL DEFAULT '[]'",
            "source_page": "ALTER TABLE words ADD COLUMN source_page INTEGER",
            "created_date": "ALTER TABLE words ADD COLUMN created_date TEXT",
            "level": "ALTER TABLE words ADD COLUMN level INTEGER NOT NULL DEFAULT 1",
            "correct_count": "ALTER TABLE words ADD COLUMN correct_count INTEGER NOT NULL DEFAULT 0",
            "wrong_count": "ALTER TABLE words ADD COLUMN wrong_count INTEGER NOT NULL DEFAULT 0",
            "last_reviewed": "ALTER TABLE words ADD COLUMN last_reviewed TEXT",
            "next_review_date": "ALTER TABLE words ADD COLUMN next_review_date TEXT",
            "is_favorite": "ALTER TABLE words ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0, 1))",
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                connection.execute(statement)
        connection.execute(
            "UPDATE words SET meaning = definition WHERE meaning = ''"
        )
        connection.execute(
            """
            UPDATE words
            SET created_date = COALESCE(NULLIF(created_date, ''), DATE(created_at))
            """
        )
        connection.execute(
            """
            UPDATE words
            SET next_review_date = COALESCE(
                NULLIF(next_review_date, ''), DATE('now', 'localtime')
            )
            """
        )


def get_pdf_searcher() -> PdfSearcher:
    global _pdf_searcher, _pdf_searcher_path
    active_book = book_manager.active_book()
    if active_book is None:
        raise PdfSearchError("当前没有可用词书，请先在设置中添加 PDF 词书。")
    if _pdf_searcher is None or _pdf_searcher_path != active_book.path:
        _pdf_searcher = PdfSearcher(active_book.path)
        _pdf_searcher_path = active_book.path
    return _pdf_searcher


def deserialize_word(row: sqlite3.Row) -> dict:
    """Convert a stored word row into template-friendly data."""
    item = dict(row)
    try:
        item["phrases"] = json.loads(item["phrases"])
    except (TypeError, json.JSONDecodeError):
        item["phrases"] = []
    item["meaning"] = item.get("meaning") or item.get("definition") or ""
    item["definition"] = item["meaning"]
    return item


def get_review_candidates(
    connection: sqlite3.Connection, selected_day_id: int, today: date
) -> list[dict]:
    """Load new words from the selected date plus all words currently due."""
    rows = connection.execute(
        """
        SELECT words.*, study_days.study_date AS source_study_date
        FROM words
        JOIN study_days ON study_days.id = words.study_day_id
        WHERE (words.study_day_id = ? AND words.last_reviewed IS NULL)
           OR COALESCE(NULLIF(words.next_review_date, ''), ?) <= ?
        ORDER BY words.id ASC
        """,
        (selected_day_id, today.isoformat(), today.isoformat()),
    ).fetchall()
    return [deserialize_word(row) for row in rows]


def get_repeat_review_candidates(
    connection: sqlite3.Connection, selected_day_id: int, after_record_id: int
) -> list[dict]:
    """Load each word from one date once for an explicit repeat round."""
    return get_review_round_candidates(
        connection,
        [selected_day_id],
        after_record_id,
    )


def get_review_round_candidates(
    connection: sqlite3.Connection,
    study_day_ids: list[int],
    after_record_id: int,
) -> list[dict]:
    """Load unreviewed words from a one-pass round covering selected dates."""
    normalized_ids = sorted({int(day_id) for day_id in study_day_ids if day_id})
    if not normalized_ids:
        return []
    placeholders = ", ".join("?" for _ in normalized_ids)
    rows = connection.execute(
        f"""
        SELECT words.*, study_days.study_date AS source_study_date
        FROM words
        JOIN study_days ON study_days.id = words.study_day_id
        WHERE words.study_day_id IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1
              FROM learning_records
              WHERE learning_records.word_id = words.id
                AND learning_records.id > ?
        )
        ORDER BY words.id ASC
        """,
        (*normalized_ids, after_record_id),
    ).fetchall()
    return [deserialize_word(row) for row in rows]


def get_custom_review_candidates(
    connection: sqlite3.Connection,
    study_day_ids: list[int],
    after_record_id: int,
    tag_ids: list[int] | None = None,
    favorite_only: bool = False,
) -> list[dict]:
    """Keep unknown words active until they are marked known in this round."""
    normalized_ids = sorted({int(day_id) for day_id in study_day_ids if day_id})
    normalized_tag_ids = sorted({int(tag_id) for tag_id in (tag_ids or []) if tag_id})
    filter_sql, filter_parameters = build_word_filter(
        normalized_ids,
        normalized_tag_ids,
        favorite_only,
    )
    rows = connection.execute(
        f"""
        SELECT words.*, study_days.study_date AS source_study_date
        FROM words
        JOIN study_days ON study_days.id = words.study_day_id
        WHERE {filter_sql}
          AND NOT EXISTS (
              SELECT 1
              FROM learning_records
              WHERE learning_records.word_id = words.id
                AND learning_records.id > ?
                AND learning_records.result = 'known'
          )
        ORDER BY words.id ASC
        """,
        (*filter_parameters, after_record_id),
    ).fetchall()
    return [deserialize_word(row) for row in rows]


def get_favorite_review_candidates(
    connection: sqlite3.Connection,
    after_record_id: int,
) -> list[dict]:
    """Keep favorite unknown words active until marked known in this round."""
    rows = connection.execute(
        """
        SELECT words.*, study_days.study_date AS source_study_date
        FROM words
        JOIN study_days ON study_days.id = words.study_day_id
        WHERE words.is_favorite = 1
          AND NOT EXISTS (
              SELECT 1
              FROM learning_records
              WHERE learning_records.word_id = words.id
                AND learning_records.id > ?
                AND learning_records.result = 'known'
          )
        ORDER BY words.id ASC
        """,
        (after_record_id,),
    ).fetchall()
    return [deserialize_word(row) for row in rows]


def get_repeat_review_state(study_day_id: int) -> dict | None:
    """Return a validated repeat-round state stored in the signed session."""
    state = session.get("repeat_review")
    if not isinstance(state, dict):
        return None
    try:
        state_day_id = int(state["study_day_id"])
        after_record_id = int(state["after_record_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if state_day_id != study_day_id or after_record_id < 0:
        return None
    return {
        "study_day_id": state_day_id,
        "after_record_id": after_record_id,
    }


def get_custom_review_state() -> dict | None:
    """Return the combined dates, tags, favorite filter, and round baseline."""
    state = session.get("custom_review")
    if not isinstance(state, dict):
        return None
    try:
        study_day_ids = sorted(
            {int(day_id) for day_id in state.get("study_day_ids", []) if int(day_id) > 0}
        )
        tag_ids = sorted(
            {int(tag_id) for tag_id in state.get("tag_ids", []) if int(tag_id) > 0}
        )
        favorite_only = bool(state.get("favorite_only", False))
        after_record_id = int(state["after_record_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (study_day_ids or tag_ids or favorite_only) or after_record_id < 0:
        return None
    return {
        "study_day_ids": study_day_ids,
        "tag_ids": tag_ids,
        "favorite_only": favorite_only,
        "after_record_id": after_record_id,
    }


def get_favorite_review_state() -> dict | None:
    """Return the baseline for the active favorite review round."""
    state = session.get("favorite_review")
    if not isinstance(state, dict):
        return None
    try:
        after_record_id = int(state["after_record_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if after_record_id < 0:
        return None
    return {"after_record_id": after_record_id}


def apply_review_result(
    connection: sqlite3.Connection,
    word: sqlite3.Row,
    rating: str,
    today: date,
) -> None:
    """Update one word and append its learning record in one transaction."""
    update = calculate_review_update(dict(word), rating, today=today)
    connection.execute(
        """
        UPDATE words
        SET level = ?, correct_count = ?, wrong_count = ?,
            last_reviewed = ?, next_review_date = ?
        WHERE id = ?
        """,
        (
            update.level,
            update.correct_count,
            update.wrong_count,
            update.last_reviewed,
            update.next_review_date,
            word["id"],
        ),
    )
    record_result = "unknown" if rating in {"again", "vague"} else "known"
    connection.execute(
        """
        INSERT INTO learning_records (word_id, result, reviewed_at)
        VALUES (?, ?, ?)
        """,
        (word["id"], record_result, update.last_reviewed),
    )


def render_mastery_review(
    word: dict | None,
    candidates: list[dict],
    selected_word_count: int,
    scope_label: str,
    mode: str,
):
    """Render a mastery-style round shared by date ranges and favorites."""
    item = word if word is not None else None
    if item:
        item["level_label"] = level_label(int(item["level"]))
    remaining_count = len(candidates)
    mastered_count = selected_word_count - remaining_count
    progress_percent = (
        round(mastered_count / selected_word_count * 100)
        if selected_word_count
        else 100
    )
    return render_template(
        "study.html",
        day=None,
        word=item,
        selected_word_count=selected_word_count,
        reviewed_count=mastered_count,
        remaining_count=remaining_count,
        progress_percent=progress_percent,
        repeat_mode=False,
        custom_mode=mode == "dates",
        favorite_mode=mode == "favorites",
        scope_label=scope_label,
    )


def get_selected_day(connection: sqlite3.Connection) -> sqlite3.Row | None:
    """Return the selected day, falling back to the most recent day."""
    selected_day_id = session.get("selected_day_id")
    day = None
    if selected_day_id:
        day = connection.execute(
            "SELECT id, study_date FROM study_days WHERE id = ?",
            (selected_day_id,),
        ).fetchone()
    if day is None:
        day = connection.execute(
            "SELECT id, study_date FROM study_days ORDER BY study_date DESC LIMIT 1"
        ).fetchone()
        session["selected_day_id"] = day["id"] if day else None
    return day


@app.route("/")
def index():
    with get_db() as connection:
        active_day = get_selected_day(connection)
        days_count = connection.execute("SELECT COUNT(*) FROM study_days").fetchone()[0]
        words = []
        if active_day:
            words = connection.execute(
                """
                SELECT id, study_day_id, word, definition, meaning, phrases,
                       source_page, created_date, level, correct_count,
                       wrong_count, last_reviewed, next_review_date, is_favorite
                FROM words
                WHERE study_day_id = ?
                ORDER BY id ASC
                """,
                (active_day["id"],),
            ).fetchall()

    active_words = [deserialize_word(word) for word in words]
    return render_template(
        "index.html",
        active_day=active_day,
        active_words=active_words,
        days_count=days_count,
        active_book=book_manager.active_book(),
    )


@app.get("/health")
def health_check():
    """Identify the local app so the Windows launcher avoids duplicate servers."""
    return {"app": "english-vocabulary", "status": "ok"}


@app.route("/settings")
def settings_page():
    books = book_manager.list_books()
    active_book = book_manager.active_book()
    backups = DatabaseBackupManager(DATABASE).list_backups()
    with get_db() as connection:
        word_count = connection.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        review_count = connection.execute(
            "SELECT COUNT(*) FROM learning_records"
        ).fetchone()[0]
    return render_template(
        "settings.html",
        books=books,
        active_book=active_book,
        backups=backups,
        word_count=word_count,
        review_count=review_count,
    )


@app.post("/settings/backups")
def create_database_backup():
    try:
        backup = DatabaseBackupManager(DATABASE).create_backup()
    except BackupError as exc:
        flash(str(exc), "error")
    else:
        flash(f"备份已创建：{backup.name}", "success")
    return redirect(url_for("settings_page"))


@app.post("/settings/backups/<backup_name>/restore")
def restore_database_backup(backup_name: str):
    try:
        safety_backup = DatabaseBackupManager(DATABASE).restore_backup(backup_name)
        init_db()
    except BackupError as exc:
        flash(str(exc), "error")
    except sqlite3.Error as exc:
        flash(f"数据库已经恢复，但结构升级失败：{exc}", "error")
    else:
        for key in (
            "selected_day_id",
            "repeat_review",
            "custom_review",
            "favorite_review",
        ):
            session.pop(key, None)
        flash(
            f"数据库已恢复。操作前的数据已保存为：{safety_backup.name}",
            "success",
        )
    return redirect(url_for("settings_page"))


@app.post("/settings/book")
def select_book():
    global _pdf_searcher, _pdf_searcher_path
    book_key = request.form.get("book_key", "")
    try:
        selected = book_manager.select_book(book_key)
    except BookManagerError as exc:
        flash(str(exc), "error")
    else:
        _pdf_searcher = None
        _pdf_searcher_path = None
        flash(f"当前词书已切换为：{selected.name}", "success")
    return redirect(url_for("settings_page"))


@app.post("/review/reset")
def reset_review_progress():
    today = date.today().isoformat()
    with get_db() as connection:
        connection.execute("DELETE FROM learning_records")
        connection.execute(
            """
            UPDATE words
            SET level = 1,
                correct_count = 0,
                wrong_count = 0,
                last_reviewed = NULL,
                next_review_date = ?
            """,
            (today,),
        )
    flash("全部复习进度已重置，单词和日期仍然保留。", "success")
    return redirect(url_for("settings_page"))


@app.route("/dates")
def date_library():
    with get_db() as connection:
        active_day = get_selected_day(connection)
        days = connection.execute(
            """
            SELECT study_days.id, study_days.study_date,
                   COUNT(words.id) AS word_count
            FROM study_days
            LEFT JOIN words ON words.study_day_id = study_days.id
            GROUP BY study_days.id
            ORDER BY study_days.study_date DESC
            """
        ).fetchall()
        words = connection.execute(
            """
            SELECT id, study_day_id, word, definition, meaning, phrases,
                   source_page, created_date, level, correct_count,
                   wrong_count, last_reviewed, next_review_date, is_favorite
            FROM words
            ORDER BY id ASC
            """
        ).fetchall()

    words_by_day: dict[int, list[dict]] = {}
    for word in words:
        item = deserialize_word(word)
        words_by_day.setdefault(item["study_day_id"], []).append(item)

    return render_template("dates.html", days=days, words_by_day=words_by_day, active_day=active_day)


@app.get("/words/manage")
def word_manager():
    query = request.args.get("q", "").strip()
    selected_day_id = request.args.get("study_day_id", type=int)
    selected_tag_id = request.args.get("tag_id", type=int)
    favorite_only = request.args.get("favorite") == "1"
    day_filter = (selected_day_id,) if selected_day_id else ()
    tag_filter = (selected_tag_id,) if selected_tag_id else ()
    with get_db() as connection:
        days = connection.execute(
            "SELECT id, study_date FROM study_days ORDER BY study_date DESC"
        ).fetchall()
        tags = list_tags(connection)
        rows, total_results = search_words(
            connection,
            query,
            WORD_SEARCH_LIMIT,
            day_filter,
            tag_filter,
            favorite_only,
        )
        duplicate_groups = find_duplicate_groups(connection)
        word_tags = tags_by_word(connection, [row["id"] for row in rows])

    words = [deserialize_word(row) for row in rows]
    for item in words:
        item["tags"] = word_tags.get(item["id"], [])
        item["tag_ids"] = {tag["id"] for tag in item["tags"]}
    return render_template(
        "words_manage.html",
        query=query,
        words=words,
        total_results=total_results,
        result_limit=WORD_SEARCH_LIMIT,
        days=days,
        tags=tags,
        selected_day_id=selected_day_id,
        selected_tag_id=selected_tag_id,
        favorite_only=favorite_only,
        duplicate_groups=duplicate_groups,
    )


def word_manager_redirect_from_form():
    """Preserve active search filters after editing one word."""
    arguments = {"q": request.form.get("q", "").strip()}
    study_day_id = request.form.get("filter_study_day_id", type=int)
    tag_id = request.form.get("filter_tag_id", type=int)
    if study_day_id:
        arguments["study_day_id"] = study_day_id
    if tag_id:
        arguments["tag_id"] = tag_id
    if request.form.get("filter_favorite") == "1":
        arguments["favorite"] = "1"
    return url_for("word_manager", **arguments)


@app.post("/words/<int:word_id>/edit")
def edit_word(word_id: int):
    redirect_url = word_manager_redirect_from_form()
    word = request.form.get("word", "").strip()
    meaning = request.form.get("meaning", "").strip()
    phrases = parse_phrases(request.form.get("phrases", ""))
    study_day_id = request.form.get("study_day_id", type=int)
    tag_ids = request.form.getlist("tag_ids", type=int)

    if not word or not meaning or not study_day_id:
        flash("请填写单词、释义并选择学习日期。", "error")
        return redirect(redirect_url)

    with get_db() as connection:
        existing_word = connection.execute(
            "SELECT id FROM words WHERE id = ?", (word_id,)
        ).fetchone()
        if existing_word is None:
            flash("要编辑的单词不存在。", "error")
            return redirect(redirect_url)

        target_day = connection.execute(
            "SELECT study_date FROM study_days WHERE id = ?", (study_day_id,)
        ).fetchone()
        if target_day is None:
            flash("所选学习日期不存在。", "error")
            return redirect(redirect_url)

        conflict = find_same_day_conflict(connection, word_id, study_day_id, word)
        if conflict is not None:
            flash(
                f"{target_day['study_date']} 已经有单词“{word}”，请避免重复保存。",
                "error",
            )
            return redirect(redirect_url)

        try:
            replace_word_tags(connection, word_id, tag_ids)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(redirect_url)

        connection.execute(
            """
            UPDATE words
            SET study_day_id = ?, word = ?, definition = ?, meaning = ?, phrases = ?
            WHERE id = ?
            """,
            (
                study_day_id,
                word,
                meaning,
                meaning,
                json.dumps(phrases, ensure_ascii=False),
                word_id,
            ),
        )

    flash(f"“{word}”已更新。", "success")
    return redirect(redirect_url)


@app.post("/tags")
def create_tag():
    try:
        name = validate_tag_name(request.form.get("name", ""))
        with get_db() as connection:
            connection.execute("INSERT INTO tags (name) VALUES (?)", (name,))
    except ValueError as exc:
        flash(str(exc), "error")
    except sqlite3.IntegrityError:
        flash("这个标签已经存在。", "error")
    else:
        flash(f"标签“{name}”已创建。", "success")
    return redirect(url_for("word_manager"))


@app.post("/tags/<int:tag_id>/edit")
def edit_tag(tag_id: int):
    try:
        name = validate_tag_name(request.form.get("name", ""))
        with get_db() as connection:
            tag = connection.execute(
                "SELECT name FROM tags WHERE id = ?", (tag_id,)
            ).fetchone()
            if tag is None:
                flash("要修改的标签不存在。", "error")
                return redirect(url_for("word_manager"))
            connection.execute(
                "UPDATE tags SET name = ? WHERE id = ?", (name, tag_id)
            )
    except ValueError as exc:
        flash(str(exc), "error")
    except sqlite3.IntegrityError:
        flash("这个标签名称已经存在。", "error")
    else:
        flash(f"标签已修改为“{name}”。", "success")
    return redirect(url_for("word_manager"))


@app.post("/tags/<int:tag_id>/delete")
def delete_tag(tag_id: int):
    with get_db() as connection:
        tag = connection.execute(
            """
            SELECT tags.name, COUNT(word_tags.word_id) AS word_count
            FROM tags
            LEFT JOIN word_tags ON word_tags.tag_id = tags.id
            WHERE tags.id = ?
            GROUP BY tags.id
            """,
            (tag_id,),
        ).fetchone()
        if tag is None:
            flash("要删除的标签不存在。", "error")
            return redirect(url_for("word_manager"))
        connection.execute("DELETE FROM tags WHERE id = ?", (tag_id,))

    flash(
        f"标签“{tag['name']}”已删除，{tag['word_count']} 个单词仍然保留。",
        "success",
    )
    return redirect(url_for("word_manager"))


@app.post("/days")
def create_day():
    study_date = request.form.get("study_date", "").strip()
    if not study_date:
        flash("请选择学习日期。", "error")
        return redirect(url_for("index"))

    try:
        with get_db() as connection:
            cursor = connection.execute(
                "INSERT INTO study_days (study_date) VALUES (?)", (study_date,)
            )
            session["selected_day_id"] = cursor.lastrowid
    except sqlite3.IntegrityError:
        flash("这个学习日期已经存在。", "error")
    else:
        flash("学习日期已创建。", "success")

    return redirect(url_for("index"))


@app.post("/days/<int:study_day_id>/edit")
def edit_study_day(study_day_id: int):
    """Update a study date without changing the words assigned to it."""
    study_date = request.form.get("study_date", "").strip()
    if not study_date:
        flash("请选择新的学习日期。", "error")
        return redirect(url_for("date_library"))

    try:
        date.fromisoformat(study_date)
    except ValueError:
        flash("日期格式不正确，请重新选择。", "error")
        return redirect(url_for("date_library"))

    try:
        with get_db() as connection:
            study_day = connection.execute(
                "SELECT id FROM study_days WHERE id = ?", (study_day_id,)
            ).fetchone()
            if study_day is None:
                flash("要编辑的学习日期不存在。", "error")
                return redirect(url_for("date_library"))

            connection.execute(
                "UPDATE study_days SET study_date = ? WHERE id = ?",
                (study_date, study_day_id),
            )
            session["selected_day_id"] = study_day_id
    except sqlite3.IntegrityError:
        flash("这个学习日期已经存在。", "error")
    else:
        flash(f"学习日期已修改为 {study_date}。", "success")

    return redirect(url_for("date_library"))


@app.post("/days/<int:study_day_id>/delete")
def delete_study_day(study_day_id: int):
    """Delete a study date; SQLite cascades to its words and review records."""
    with get_db() as connection:
        study_day = connection.execute(
            "SELECT study_date FROM study_days WHERE id = ?", (study_day_id,)
        ).fetchone()
        if study_day is None:
            flash("要删除的学习日期不存在。", "error")
            return redirect(url_for("date_library"))

        deleted_date = study_day["study_date"]
        connection.execute("DELETE FROM study_days WHERE id = ?", (study_day_id,))

        if session.get("selected_day_id") == study_day_id:
            replacement_day = connection.execute(
                "SELECT id FROM study_days ORDER BY study_date DESC LIMIT 1"
            ).fetchone()
            session["selected_day_id"] = (
                replacement_day["id"] if replacement_day else None
            )

    flash(f"已删除学习日期 {deleted_date}，该日期下的单词也已删除。", "success")
    return redirect(url_for("date_library"))


@app.post("/words")
def add_word():
    study_day_id = request.form.get("study_day_id", type=int)
    word = request.form.get("word", "").strip()

    if not study_day_id or not word:
        flash("请选择日期并填写单词。", "error")
        return redirect(url_for("index"))

    session["selected_day_id"] = study_day_id

    try:
        entry = get_pdf_searcher().search(word)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))
    except PdfSearchError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))

    if entry is None:
        flash(f"没有在词库中找到“{word}”，请检查拼写后重试。", "error")
        return redirect(url_for("index"))

    try:
        with get_db() as connection:
            saved_phrases = json.dumps(entry.phrases, ensure_ascii=False)
            created_date = date.today().isoformat()
            existing = connection.execute(
                """
                SELECT id FROM words
                WHERE study_day_id = ? AND LOWER(word) = LOWER(?)
                LIMIT 1
                """,
                (study_day_id, entry.word),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE words
                    SET word = ?, definition = ?, meaning = ?, phrases = ?,
                        source_page = ?
                    WHERE id = ?
                    """,
                    (
                        entry.word,
                        entry.definition,
                        entry.definition,
                        saved_phrases,
                        entry.page_number,
                        existing["id"],
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO words
                        (study_day_id, word, definition, meaning, phrases,
                         source_page, created_date, next_review_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        study_day_id,
                        entry.word,
                        entry.definition,
                        entry.definition,
                        saved_phrases,
                        entry.page_number,
                        created_date,
                        created_date,
                    ),
                )
    except sqlite3.IntegrityError:
        flash("所选学习日期不存在。", "error")
    else:
        flash(f"{entry.word} 添加成功\n{entry.definition}", "success")

    return redirect(url_for("index"))


@app.post("/preferences/study-day")
def select_study_day():
    study_day_id = request.form.get("study_day_id", type=int)
    if not study_day_id:
        return "学习日期无效。", 400

    with get_db() as connection:
        exists = connection.execute(
            "SELECT 1 FROM study_days WHERE id = ?", (study_day_id,)
        ).fetchone()
    if exists is None:
        return "学习日期不存在。", 404

    session["selected_day_id"] = study_day_id
    if request.form.get("redirect_to") == "index":
        flash("已切换学习日期。", "success")
        return redirect(url_for("index"))
    return "", 204


@app.post("/words/<int:word_id>/delete")
def delete_word(word_id: int):
    next_endpoint = (
        "date_library" if request.form.get("next") == "date_library" else "index"
    )
    with get_db() as connection:
        word = connection.execute(
            "SELECT word, study_day_id FROM words WHERE id = ?", (word_id,)
        ).fetchone()
        if word is None:
            flash("要删除的单词不存在。", "error")
            return redirect(url_for(next_endpoint))

        connection.execute("DELETE FROM words WHERE id = ?", (word_id,))

    session["selected_day_id"] = word["study_day_id"]
    flash(f"已删除：{word['word']}", "success")
    return redirect(url_for(next_endpoint))


@app.post("/words/<int:word_id>/favorite")
def toggle_favorite(word_id: int):
    """Toggle a word's persistent vocabulary-book membership."""
    wants_json = request.accept_mimetypes.best == "application/json"
    next_endpoints = {
        "date_library": "date_library",
        "custom_review_setup": "custom_review_setup",
    }
    next_endpoint = next_endpoints.get(request.form.get("next"), "index")
    with get_db() as connection:
        word = connection.execute(
            "SELECT word, study_day_id, is_favorite FROM words WHERE id = ?",
            (word_id,),
        ).fetchone()
        if word is None:
            if wants_json:
                return jsonify({"error": "要收藏的单词不存在。"}), 404
            flash("要收藏的单词不存在。", "error")
            return redirect(url_for(next_endpoint))
        is_favorite = 0 if int(word["is_favorite"]) else 1
        connection.execute(
            "UPDATE words SET is_favorite = ? WHERE id = ?",
            (is_favorite, word_id),
        )

    session["selected_day_id"] = word["study_day_id"]
    action = "已加入生词簿" if is_favorite else "已移出生词簿"
    message = f"{word['word']} {action}"
    if wants_json:
        return jsonify(
            {
                "word_id": word_id,
                "word": word["word"],
                "is_favorite": bool(is_favorite),
                "message": message,
            }
        )
    flash(message, "success")
    return redirect(url_for(next_endpoint))


@app.get("/study/range")
def custom_review_setup():
    """Show the flexible date-range selector for a custom review round."""
    with get_db() as connection:
        days = connection.execute(
            """
            SELECT study_days.id, study_days.study_date,
                   COUNT(words.id) AS word_count
            FROM study_days
            LEFT JOIN words ON words.study_day_id = study_days.id
            GROUP BY study_days.id
            ORDER BY study_days.study_date DESC
            """
        ).fetchall()
        favorite_count = connection.execute(
            "SELECT COUNT(*) FROM words WHERE is_favorite = 1"
        ).fetchone()[0]
        favorite_words = connection.execute(
            """
            SELECT words.id, words.word, words.definition, words.is_favorite,
                   study_days.study_date AS source_study_date
            FROM words
            JOIN study_days ON study_days.id = words.study_day_id
            WHERE words.is_favorite = 1
            ORDER BY study_days.study_date DESC, words.id ASC
            """
        ).fetchall()
        tags = list_tags(connection)
    total_word_count = sum(int(day["word_count"]) for day in days)
    return render_template(
        "review_range.html",
        days=days,
        total_word_count=total_word_count,
        favorite_count=favorite_count,
        favorite_words=favorite_words,
        tags=tags,
    )


@app.post("/study/range")
def start_custom_review():
    """Start a weighted round using intersected date, tag, and favorite filters."""
    requested_ids = sorted(set(request.form.getlist("study_day_ids", type=int)))
    requested_tag_ids = sorted(set(request.form.getlist("tag_ids", type=int)))
    favorite_only = request.form.get("favorite_only") == "1"
    if not (requested_ids or requested_tag_ids or favorite_only):
        flash("请至少选择一个日期、标签或生词簿筛选条件。", "error")
        return redirect(url_for("custom_review_setup"))

    with get_db() as connection:
        selected_days = []
        if requested_ids:
            placeholders = ", ".join("?" for _ in requested_ids)
            selected_days = connection.execute(
                f"SELECT id, study_date FROM study_days WHERE id IN ({placeholders})",
                requested_ids,
            ).fetchall()
        selected_tags = []
        if requested_tag_ids:
            placeholders = ", ".join("?" for _ in requested_tag_ids)
            selected_tags = connection.execute(
                f"SELECT id, name FROM tags WHERE id IN ({placeholders})",
                requested_tag_ids,
            ).fetchall()

        selected_ids = sorted(int(day["id"]) for day in selected_days)
        selected_tag_ids = sorted(int(tag["id"]) for tag in selected_tags)
        if len(selected_ids) != len(requested_ids) or len(selected_tag_ids) != len(
            requested_tag_ids
        ):
            flash("所选日期或标签已经不存在，请刷新后重试。", "error")
            return redirect(url_for("custom_review_setup"))

        filter_sql, filter_parameters = build_word_filter(
            selected_ids,
            selected_tag_ids,
            favorite_only,
        )
        selected_word_count = connection.execute(
            f"SELECT COUNT(*) FROM words WHERE {filter_sql}", filter_parameters
        ).fetchone()[0]
        latest_record_id = connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM learning_records"
        ).fetchone()[0]

    if not selected_word_count:
        flash("当前组合条件下没有可以背诵的单词。", "error")
        return redirect(url_for("custom_review_setup"))

    session["custom_review"] = {
        "study_day_ids": selected_ids,
        "tag_ids": selected_tag_ids,
        "favorite_only": favorite_only,
        "after_record_id": latest_record_id,
    }
    flash(
        f"已开始组合筛选背诵，本轮共 {selected_word_count} 个单词。",
        "success",
    )
    return redirect(url_for("custom_review_session"))


@app.get("/study/range/session")
def custom_review_session():
    """Draw from the active combined filter until every word is marked known."""
    state = get_custom_review_state()
    if state is None:
        flash("组合背诵范围已失效，请重新选择。", "error")
        return redirect(url_for("custom_review_setup"))

    exclude_word_id = request.args.get("exclude_word_id", type=int)
    day_ids = state["study_day_ids"]
    tag_ids = state["tag_ids"]
    favorite_only = state["favorite_only"]
    today = date.today()
    with get_db() as connection:
        selected_days = []
        if day_ids:
            placeholders = ", ".join("?" for _ in day_ids)
            selected_days = connection.execute(
                f"""
                SELECT id, study_date
                FROM study_days
                WHERE id IN ({placeholders})
                ORDER BY study_date ASC
                """,
                day_ids,
            ).fetchall()
        selected_tags = []
        if tag_ids:
            placeholders = ", ".join("?" for _ in tag_ids)
            selected_tags = connection.execute(
                f"""
                SELECT id, name
                FROM tags
                WHERE id IN ({placeholders})
                ORDER BY name COLLATE NOCASE ASC
                """,
                tag_ids,
            ).fetchall()
        filter_sql, filter_parameters = build_word_filter(
            day_ids,
            tag_ids,
            favorite_only,
        )
        selected_word_count = connection.execute(
            f"SELECT COUNT(*) FROM words WHERE {filter_sql}",
            filter_parameters,
        ).fetchone()[0]
        candidates = get_custom_review_candidates(
            connection,
            day_ids,
            state["after_record_id"],
            tag_ids,
            favorite_only,
        )
        word = choose_weighted_word(
            candidates,
            previous_word_id=exclude_word_id,
            today=today,
        )

    filters_still_exist = (
        len(selected_days) == len(day_ids) and len(selected_tags) == len(tag_ids)
    )
    if not filters_still_exist or not selected_word_count:
        session.pop("custom_review", None)
        flash("所选组合条件已经没有可以背诵的单词。", "error")
        return redirect(url_for("custom_review_setup"))

    scope_parts = []
    if len(selected_days) == 1:
        scope_parts.append(selected_days[0]["study_date"])
    elif selected_days:
        scope_parts.append(
            f"{len(selected_days)} 个日期 · "
            f"{selected_days[0]['study_date']} 至 {selected_days[-1]['study_date']}"
        )
    if selected_tags:
        scope_parts.append("标签：" + " + ".join(tag["name"] for tag in selected_tags))
    if favorite_only:
        scope_parts.append("仅生词簿")
    scope_label = " · ".join(scope_parts)

    return render_mastery_review(
        word,
        candidates,
        selected_word_count,
        scope_label,
        mode="dates",
    )


@app.post("/study/range/review")
def save_custom_review():
    """Save one custom-range answer while keeping unknown words in the queue."""
    state = get_custom_review_state()
    if state is None:
        flash("组合背诵范围已失效，请重新选择。", "error")
        return redirect(url_for("custom_review_setup"))

    word_id = request.form.get("word_id", type=int)
    rating = request.form.get("rating", "")
    if not word_id:
        flash("学习记录无效，请重新选择。", "error")
        return redirect(url_for("custom_review_session"))

    day_ids = state["study_day_ids"]
    tag_ids = state["tag_ids"]
    favorite_only = state["favorite_only"]
    filter_sql, filter_parameters = build_word_filter(
        day_ids,
        tag_ids,
        favorite_only,
    )
    with get_db() as connection:
        word = connection.execute(
            f"""
            SELECT * FROM words
            WHERE id = ?
              AND {filter_sql}
              AND NOT EXISTS (
                  SELECT 1
                  FROM learning_records
                  WHERE learning_records.word_id = words.id
                    AND learning_records.id > ?
                    AND learning_records.result = 'known'
            )
            """,
            (word_id, *filter_parameters, state["after_record_id"]),
        ).fetchone()
        if word is None:
            flash("该单词当前不在组合背诵队列中。", "error")
            return redirect(url_for("custom_review_session"))
        try:
            apply_review_result(connection, word, rating, date.today())
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("custom_review_session"))

    if rating in {"again", "vague"}:
        flash(f"已记录：{RATING_LABELS[rating]}，这个单词稍后还会出现。", "success")
    else:
        flash(f"已记录：{RATING_LABELS[rating]}", "success")
    return redirect(
        url_for("custom_review_session", exclude_word_id=word_id)
    )


@app.post("/study/favorites")
def start_favorite_review():
    """Start or restart a mastery round containing every favorite word."""
    with get_db() as connection:
        favorite_count = connection.execute(
            "SELECT COUNT(*) FROM words WHERE is_favorite = 1"
        ).fetchone()[0]
        latest_record_id = connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM learning_records"
        ).fetchone()[0]
    if not favorite_count:
        flash("生词簿还是空的，请先收藏一些单词。", "error")
        return redirect(url_for("custom_review_setup"))

    session["favorite_review"] = {"after_record_id": latest_record_id}
    flash(f"已开始生词簿背诵，本轮共 {favorite_count} 个单词。", "success")
    return redirect(url_for("favorite_review_session"))


@app.get("/study/favorites/session")
def favorite_review_session():
    """Draw favorite words until each is marked known in this round."""
    state = get_favorite_review_state()
    if state is None:
        flash("生词簿背诵已失效，请重新开始。", "error")
        return redirect(url_for("custom_review_setup"))

    exclude_word_id = request.args.get("exclude_word_id", type=int)
    today = date.today()
    with get_db() as connection:
        favorite_count = connection.execute(
            "SELECT COUNT(*) FROM words WHERE is_favorite = 1"
        ).fetchone()[0]
        candidates = get_favorite_review_candidates(
            connection,
            state["after_record_id"],
        )
        word = choose_weighted_word(
            candidates,
            previous_word_id=exclude_word_id,
            today=today,
        )

    if not favorite_count:
        session.pop("favorite_review", None)
        flash("生词簿还是空的，请先收藏一些单词。", "error")
        return redirect(url_for("custom_review_setup"))

    return render_mastery_review(
        word,
        candidates,
        favorite_count,
        f"生词簿 · {favorite_count} 个单词",
        mode="favorites",
    )


@app.post("/study/favorites/review")
def save_favorite_review():
    """Save a favorite answer and retain unknown words for another draw."""
    state = get_favorite_review_state()
    if state is None:
        flash("生词簿背诵已失效，请重新开始。", "error")
        return redirect(url_for("custom_review_setup"))

    word_id = request.form.get("word_id", type=int)
    rating = request.form.get("rating", "")
    if not word_id:
        flash("学习记录无效，请重新选择。", "error")
        return redirect(url_for("favorite_review_session"))

    with get_db() as connection:
        word = connection.execute(
            """
            SELECT * FROM words
            WHERE id = ?
              AND is_favorite = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM learning_records
                  WHERE learning_records.word_id = words.id
                    AND learning_records.id > ?
                    AND learning_records.result = 'known'
              )
            """,
            (word_id, state["after_record_id"]),
        ).fetchone()
        if word is None:
            flash("该单词当前不在生词簿背诵队列中。", "error")
            return redirect(url_for("favorite_review_session"))
        try:
            apply_review_result(connection, word, rating, date.today())
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("favorite_review_session"))

    if rating in {"again", "vague"}:
        flash(f"已记录：{RATING_LABELS[rating]}，这个单词稍后还会出现。", "success")
    else:
        flash(f"已记录：{RATING_LABELS[rating]}", "success")
    return redirect(
        url_for("favorite_review_session", exclude_word_id=word_id)
    )


@app.route("/study/<int:study_day_id>")
def study(study_day_id: int):
    exclude_word_id = request.args.get("exclude_word_id", type=int)
    repeat_mode = request.args.get("mode") == "repeat"
    today = date.today()

    with get_db() as connection:
        day = connection.execute(
            "SELECT id, study_date FROM study_days WHERE id = ?", (study_day_id,)
        ).fetchone()
        if day is None:
            flash("学习日期不存在。", "error")
            return redirect(url_for("index"))

        selected_word_count = connection.execute(
            "SELECT COUNT(*) FROM words WHERE study_day_id = ?", (study_day_id,)
        ).fetchone()[0]
        repeat_state = get_repeat_review_state(study_day_id) if repeat_mode else None
        if repeat_mode and repeat_state is None:
            flash("重新背诵已结束或失效，请重新开始一轮。", "error")
            return redirect(url_for("study", study_day_id=study_day_id))

        if repeat_state:
            candidates = get_repeat_review_candidates(
                connection,
                study_day_id,
                repeat_state["after_record_id"],
            )
        else:
            candidates = get_review_candidates(connection, study_day_id, today)
        word = choose_weighted_word(
            candidates,
            previous_word_id=exclude_word_id,
            today=today,
        )
        reviewed_today = connection.execute(
            """
            SELECT COUNT(*) FROM learning_records
            WHERE SUBSTR(reviewed_at, 1, 10) = ?
            """,
            (today.isoformat(),),
        ).fetchone()[0]

    item = word if word is not None else None
    if item:
        item["level_label"] = level_label(int(item["level"]))
    remaining_count = len(candidates)
    reviewed_count = (
        selected_word_count - remaining_count if repeat_mode else reviewed_today
    )
    progress_total = (
        selected_word_count if repeat_mode else reviewed_count + remaining_count
    )
    progress_percent = (
        round(reviewed_count / progress_total * 100) if progress_total else 100
    )

    return render_template(
        "study.html",
        day=day,
        word=item,
        selected_word_count=selected_word_count,
        reviewed_count=reviewed_count,
        remaining_count=remaining_count,
        progress_percent=progress_percent,
        repeat_mode=repeat_mode,
        custom_mode=False,
        favorite_mode=False,
    )


@app.post("/study/<int:study_day_id>/repeat")
def start_repeat_review(study_day_id: int):
    """Start a one-pass review containing every word from the selected date."""
    with get_db() as connection:
        day = connection.execute(
            "SELECT 1 FROM study_days WHERE id = ?", (study_day_id,)
        ).fetchone()
        word_count = connection.execute(
            "SELECT COUNT(*) FROM words WHERE study_day_id = ?", (study_day_id,)
        ).fetchone()[0]
        latest_record_id = connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM learning_records"
        ).fetchone()[0]

    if day is None:
        flash("学习日期不存在。", "error")
        return redirect(url_for("index"))
    if not word_count:
        flash("这个日期下还没有可以重新背诵的单词。", "error")
        return redirect(url_for("study", study_day_id=study_day_id))

    session["repeat_review"] = {
        "study_day_id": study_day_id,
        "after_record_id": latest_record_id,
    }
    session["selected_day_id"] = study_day_id
    flash(f"已开始重新背诵，本轮共 {word_count} 个单词。", "success")
    return redirect(url_for("study", study_day_id=study_day_id, mode="repeat"))


@app.post("/study/<int:study_day_id>/review")
def save_review(study_day_id: int):
    word_id = request.form.get("word_id", type=int)
    rating = request.form.get("rating", "")
    repeat_mode = request.form.get("review_mode") == "repeat"
    repeat_state = get_repeat_review_state(study_day_id) if repeat_mode else None
    today = date.today()

    redirect_arguments = {"study_day_id": study_day_id}
    if repeat_state:
        redirect_arguments["mode"] = "repeat"

    if not word_id:
        flash("学习记录无效，请重新选择。", "error")
        return redirect(url_for("study", **redirect_arguments))

    if repeat_mode and repeat_state is None:
        flash("重新背诵已结束或失效，请重新开始一轮。", "error")
        return redirect(url_for("study", study_day_id=study_day_id))

    with get_db() as connection:
        if repeat_state:
            word = connection.execute(
                """
                SELECT * FROM words
                WHERE id = ?
                  AND study_day_id = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM learning_records
                      WHERE learning_records.word_id = words.id
                        AND learning_records.id > ?
                  )
                """,
                (word_id, study_day_id, repeat_state["after_record_id"]),
            ).fetchone()
        else:
            word = connection.execute(
                """
                SELECT * FROM words
                WHERE id = ?
                  AND (
                        (study_day_id = ? AND last_reviewed IS NULL)
                        OR COALESCE(NULLIF(next_review_date, ''), ?) <= ?
                      )
                """,
                (word_id, study_day_id, today.isoformat(), today.isoformat()),
            ).fetchone()
        if word is None:
            flash("该单词当前不在复习队列中。", "error")
            return redirect(url_for("study", **redirect_arguments))

        try:
            apply_review_result(connection, word, rating, today)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("study", **redirect_arguments))

    flash(f"已记录：{RATING_LABELS[rating]}", "success")
    return redirect(
        url_for(
            "study",
            **redirect_arguments,
            exclude_word_id=word_id,
        )
    )


init_db()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
