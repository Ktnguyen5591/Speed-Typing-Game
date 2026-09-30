import os
import random
import sqlite3

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "typing.db")

SENTENCES = [
    "the quick brown fox jumps over the lazy dog",
    "practice makes perfect when you type every day",
    "typing fast is good but typing right is better",
    "keep your fingers relaxed and eyes on the screen",
    "slow is smooth and smooth is fast when typing",
]

KB_LAYOUT = ["qwertyuiop", "asdfghjkl", "zxcvbnm", " "]


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wpm REAL, accuracy REAL, total_chars INTEGER, errors INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS keystrokes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER, expected TEXT, typed TEXT,
        correct INTEGER, delay_ms INTEGER)""")
    conn.commit()
    conn.close()


init_db()


@app.route("/")
def index():
    return render_template("index.html", sentence=random.choice(SENTENCES))


@app.route("/api/submit", methods=["POST"])
def submit():
    data = request.get_json()
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO sessions(wpm, accuracy, total_chars, errors) VALUES (?,?,?,?)",
        (data["wpm"], data["accuracy"], data["total_chars"], data["errors"]))
    sid = cur.lastrowid
    for k in data.get("keystrokes", []):
        conn.execute(
            "INSERT INTO keystrokes(session_id, expected, typed, correct, delay_ms) VALUES (?,?,?,?,?)",
            (sid, k["expected"], k["typed"], 1 if k["correct"] else 0, k["delay_ms"]))
    conn.commit()
    conn.close()
    return jsonify(status="ok", session_id=sid)


@app.route("/dashboard")
def dashboard():
    conn = get_db()
    sessions = conn.execute(
        "SELECT * FROM sessions ORDER BY id DESC LIMIT 20").fetchall()
    rows = conn.execute("""SELECT expected, SUM(correct = 0) AS errs,
            COUNT(*) AS hits, ROUND(AVG(delay_ms)) AS avg_delay
        FROM keystrokes GROUP BY expected""").fetchall()
    conn.close()

    heat = {}
    for r in rows:
        errs = r["errs"] or 0
        cls = "k-bad" if errs > 3 else "k-mid" if errs > 0 else "k-ok"
        heat[r["expected"]] = {"cls": cls, "errs": errs, "delay": r["avg_delay"]}

    wpm_history = [s["wpm"] for s in reversed(sessions)]
    return render_template("dashboard.html", sessions=sessions,
                           layout=KB_LAYOUT, heat=heat, wpm_history=wpm_history)


if __name__ == "__main__":
    app.run(debug=True)