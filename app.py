from datetime import date, timedelta
from pathlib import Path
import json
import sqlite3

from flask import Flask, flash, redirect, render_template, request, session, url_for

from book_manager import BookManager, BookManagerError
from pdf_search import PdfSearchError, PdfSearcher
from spaced_repetition import (
    calculate_review_update,
    choose_weighted_word,
    level_label,
)
from remote_access import RemoteAccessManager


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
) -> list[dict]:
    """Keep unknown words active until they are marked known in this round."""
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
                AND learning_records.result = 'known'
          )
        ORDER BY words.id ASC
        """,
        (*normalized_ids, after_record_id),
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
    """Return the selected dates and baseline for a custom review round."""
    state = session.get("custom_review")
    if not isinstance(state, dict):
        return None
    try:
        study_day_ids = sorted(
            {
                int(day_id)
                for day_id in state["study_day_ids"]
                if int(day_id) > 0
            }
        )
        after_record_id = int(state["after_record_id"])
    except (KeyError, TypeError, ValueError):
        return None
    if not study_day_ids or after_record_id < 0:
        return None
    return {
        "study_day_ids": study_day_ids,
        "after_record_id": after_record_id,
    }


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
                       wrong_count, last_reviewed, next_review_date
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
    with get_db() as connection:
        word_count = connection.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        review_count = connection.execute(
            "SELECT COUNT(*) FROM learning_records"
        ).fetchone()[0]
    return render_template(
        "settings.html",
        books=books,
        active_book=active_book,
        word_count=word_count,
        review_count=review_count,
    )


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
                   wrong_count, last_reviewed, next_review_date
            FROM words
            ORDER BY id ASC
            """
        ).fetchall()

    words_by_day: dict[int, list[dict]] = {}
    for word in words:
        item = deserialize_word(word)
        words_by_day.setdefault(item["study_day_id"], []).append(item)

    return render_template("dates.html", days=days, words_by_day=words_by_day, active_day=active_day)


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
    total_word_count = sum(int(day["word_count"]) for day in days)
    return render_template(
        "review_range.html",
        days=days,
        total_word_count=total_word_count,
    )


@app.post("/study/range")
def start_custom_review():
    """Start a weighted review round across any user-selected dates."""
    requested_ids = sorted(
        set(request.form.getlist("study_day_ids", type=int))
    )
    if not requested_ids:
        flash("请至少选择一个包含单词的日期。", "error")
        return redirect(url_for("custom_review_setup"))

    placeholders = ", ".join("?" for _ in requested_ids)
    with get_db() as connection:
        selected_days = connection.execute(
            f"""
            SELECT study_days.id, COUNT(words.id) AS word_count
            FROM study_days
            LEFT JOIN words ON words.study_day_id = study_days.id
            WHERE study_days.id IN ({placeholders})
            GROUP BY study_days.id
            HAVING COUNT(words.id) > 0
            """,
            requested_ids,
        ).fetchall()
        latest_record_id = connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM learning_records"
        ).fetchone()[0]

    selected_ids = sorted(int(day["id"]) for day in selected_days)
    selected_word_count = sum(int(day["word_count"]) for day in selected_days)
    if not selected_ids:
        flash("所选日期中没有可以背诵的单词。", "error")
        return redirect(url_for("custom_review_setup"))

    session["custom_review"] = {
        "study_day_ids": selected_ids,
        "after_record_id": latest_record_id,
    }
    flash(
        f"已开始组合背诵：{len(selected_ids)} 个日期，共 {selected_word_count} 个单词。",
        "success",
    )
    return redirect(url_for("custom_review_session"))


@app.get("/study/range/session")
def custom_review_session():
    """Draw from the selected dates until every word is marked known."""
    state = get_custom_review_state()
    if state is None:
        flash("组合背诵范围已失效，请重新选择。", "error")
        return redirect(url_for("custom_review_setup"))

    exclude_word_id = request.args.get("exclude_word_id", type=int)
    day_ids = state["study_day_ids"]
    placeholders = ", ".join("?" for _ in day_ids)
    today = date.today()
    with get_db() as connection:
        selected_days = connection.execute(
            f"""
            SELECT id, study_date
            FROM study_days
            WHERE id IN ({placeholders})
            ORDER BY study_date ASC
            """,
            day_ids,
        ).fetchall()
        selected_word_count = connection.execute(
            f"SELECT COUNT(*) FROM words WHERE study_day_id IN ({placeholders})",
            day_ids,
        ).fetchone()[0]
        candidates = get_custom_review_candidates(
            connection,
            day_ids,
            state["after_record_id"],
        )
        word = choose_weighted_word(
            candidates,
            previous_word_id=exclude_word_id,
            today=today,
        )

    if not selected_days or not selected_word_count:
        session.pop("custom_review", None)
        flash("所选日期已经没有可以背诵的单词。", "error")
        return redirect(url_for("custom_review_setup"))

    if len(selected_days) == 1:
        scope_label = selected_days[0]["study_date"]
    else:
        scope_label = (
            f"{len(selected_days)} 个日期 · "
            f"{selected_days[0]['study_date']} 至 {selected_days[-1]['study_date']}"
        )

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
        custom_mode=True,
        scope_label=scope_label,
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
    placeholders = ", ".join("?" for _ in day_ids)
    with get_db() as connection:
        word = connection.execute(
            f"""
            SELECT * FROM words
            WHERE id = ?
              AND study_day_id IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1
                  FROM learning_records
                  WHERE learning_records.word_id = words.id
                    AND learning_records.id > ?
                    AND learning_records.result = 'known'
              )
            """,
            (word_id, *day_ids, state["after_record_id"]),
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
