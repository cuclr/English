from pathlib import Path
import sqlite3

from flask import Flask, flash, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "instance" / "vocabulary.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-only-change-me"


def get_db() -> sqlite3.Connection:
    """Create a database connection whose rows can be accessed by column name."""
    DATABASE.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DATABASE)
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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (study_day_id) REFERENCES study_days (id)
                    ON DELETE CASCADE
            );
            """
        )


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
            SELECT id, study_day_id, word
            FROM words
            ORDER BY id ASC
            """
        ).fetchall()

    words_by_day: dict[int, list[sqlite3.Row]] = {}
    for word in words:
        words_by_day.setdefault(word["study_day_id"], []).append(word)

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
        with get_db() as connection:
            connection.execute(
                "INSERT INTO words (study_day_id, word) VALUES (?, ?)",
                (study_day_id, word),
            )
    except sqlite3.IntegrityError:
        flash("所选学习日期不存在。", "error")
    else:
        flash(f"已添加单词：{word}", "success")

    return redirect(url_for("index"))


init_db()


if __name__ == "__main__":
    app.run(debug=True)
