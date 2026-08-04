from pathlib import Path
import json
import sqlite3

from flask import Flask, flash, redirect, render_template, request, url_for

from pdf_search import PdfSearchError, PdfSearcher, find_vocabulary_pdf


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "instance" / "vocabulary.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-only-change-me"
_pdf_searcher: PdfSearcher | None = None


class DatabaseConnection(sqlite3.Connection):
    """Commit or roll back a context-managed connection, then always close it."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


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
                phrases TEXT NOT NULL DEFAULT '[]',
                source_page INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
            "phrases": "ALTER TABLE words ADD COLUMN phrases TEXT NOT NULL DEFAULT '[]'",
            "source_page": "ALTER TABLE words ADD COLUMN source_page INTEGER",
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                connection.execute(statement)


def get_pdf_searcher() -> PdfSearcher:
    global _pdf_searcher
    if _pdf_searcher is None:
        _pdf_searcher = PdfSearcher(find_vocabulary_pdf(BASE_DIR))
    return _pdf_searcher


@app.route("/")
def index():
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

        words = connection.execute(
            """
            SELECT id, study_day_id, word, definition, phrases, source_page
            FROM words
            ORDER BY id ASC
            """
        ).fetchall()

    words_by_day: dict[int, list[sqlite3.Row]] = {}
    for word in words:
        item = dict(word)
        try:
            item["phrases"] = json.loads(item["phrases"])
        except (TypeError, json.JSONDecodeError):
            item["phrases"] = []
        words_by_day.setdefault(item["study_day_id"], []).append(item)

    return render_template("index.html", days=days, words_by_day=words_by_day)


@app.post("/days")
def create_day():
    study_date = request.form.get("study_date", "").strip()
    if not study_date:
        flash("请选择学习日期。", "error")
        return redirect(url_for("index"))

    try:
        with get_db() as connection:
            connection.execute(
                "INSERT INTO study_days (study_date) VALUES (?)", (study_date,)
            )
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

    try:
        entry = get_pdf_searcher().search(word)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))
    except PdfSearchError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))

    if entry is None:
        flash(f"词书中没有找到：{word}", "error")
        return redirect(url_for("index"))

    try:
        with get_db() as connection:
            saved_phrases = json.dumps(entry.phrases, ensure_ascii=False)
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
                    SET word = ?, definition = ?, phrases = ?, source_page = ?
                    WHERE id = ?
                    """,
                    (
                        entry.word,
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
                        (study_day_id, word, definition, phrases, source_page)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        study_day_id,
                        entry.word,
                        entry.definition,
                        saved_phrases,
                        entry.page_number,
                    ),
                )
    except sqlite3.IntegrityError:
        flash("所选学习日期不存在。", "error")
    else:
        flash(f"已保存：{entry.word}", "success")

    return redirect(url_for("index"))


@app.route("/study/<int:study_day_id>")
def study(study_day_id: int):
    exclude_word_id = request.args.get("exclude_word_id", type=int)

    with get_db() as connection:
        day = connection.execute(
            "SELECT id, study_date FROM study_days WHERE id = ?", (study_day_id,)
        ).fetchone()
        if day is None:
            flash("学习日期不存在。", "error")
            return redirect(url_for("index"))

        word_count = connection.execute(
            "SELECT COUNT(*) FROM words WHERE study_day_id = ?", (study_day_id,)
        ).fetchone()[0]

        if exclude_word_id and word_count > 1:
            word = connection.execute(
                """
                SELECT id, word, definition, phrases
                FROM words
                WHERE study_day_id = ? AND id != ?
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (study_day_id, exclude_word_id),
            ).fetchone()
        else:
            word = connection.execute(
                """
                SELECT id, word, definition, phrases
                FROM words
                WHERE study_day_id = ?
                ORDER BY RANDOM()
                LIMIT 1
                """,
                (study_day_id,),
            ).fetchone()

        stats = connection.execute(
            """
            SELECT COUNT(learning_records.id) AS total,
                   COALESCE(SUM(learning_records.result = 'known'), 0) AS known,
                   COALESCE(SUM(learning_records.result = 'unknown'), 0) AS unknown
            FROM learning_records
            JOIN words ON words.id = learning_records.word_id
            WHERE words.study_day_id = ?
            """,
            (study_day_id,),
        ).fetchone()

    item = dict(word) if word is not None else None
    if item is not None:
        try:
            item["phrases"] = json.loads(item["phrases"])
        except (TypeError, json.JSONDecodeError):
            item["phrases"] = []

    return render_template(
        "study.html",
        day=day,
        word=item,
        word_count=word_count,
        stats=stats,
    )


@app.post("/study/<int:study_day_id>/review")
def save_review(study_day_id: int):
    word_id = request.form.get("word_id", type=int)
    result = request.form.get("result", "")

    if not word_id or result not in {"known", "unknown"}:
        flash("学习记录无效，请重新选择。", "error")
        return redirect(url_for("study", study_day_id=study_day_id))

    with get_db() as connection:
        word_exists = connection.execute(
            "SELECT 1 FROM words WHERE id = ? AND study_day_id = ?",
            (word_id, study_day_id),
        ).fetchone()
        if word_exists is None:
            flash("该单词不属于当前学习日期。", "error")
            return redirect(url_for("study", study_day_id=study_day_id))

        connection.execute(
            "INSERT INTO learning_records (word_id, result) VALUES (?, ?)",
            (word_id, result),
        )

    message = "已记录：认识" if result == "known" else "已记录：不认识"
    flash(message, "success")
    return redirect(
        url_for(
            "study",
            study_day_id=study_day_id,
            exclude_word_id=word_id,
        )
    )


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
