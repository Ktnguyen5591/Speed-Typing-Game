import os
import random
import secrets
import sqlite3
import time

from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "typing.db")
SENTENCES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sentences.txt")

TOKENS = set()        # token một lần cho mỗi lượt chơi
LAST_SUBMIT = {}      # chặn spam theo IP

KB_LAYOUT = ["qwertyuiop", "asdfghjkl", "zxcvbnm", " "]


def load_sentences():
    with open(SENTENCES_PATH, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


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
    token = secrets.token_hex(8)
    TOKENS.add(token)
    return render_template("index.html",
                           sentence=random.choice(load_sentences()),
                           token=token)


@app.route("/api/submit", methods=["POST"])
def submit():
    # Lớp 1: mỗi IP tối đa 1 submit / 5 giây
    ip = request.remote_addr or "unknown"
    now = time.time()
    if now - LAST_SUBMIT.get(ip, 0) < 5:
        return jsonify(status="too fast"), 429
    LAST_SUBMIT[ip] = now

    data = request.get_json(silent=True) or {}

    # Lớp 2: token một lần (bot không tải trang game thì không có token)
    token = data.get("token", "")
    if token not in TOKENS:
        return jsonify(status="invalid token"), 403
    TOKENS.discard(token)

    # Lớp 3: server tự tính mọi chỉ số, không tin client
    keystrokes = data.get("keystrokes", [])
    if not keystrokes or len(keystrokes) > 2000:
        return jsonify(status="invalid"), 400
    total = len(keystrokes)
    correct = sum(1 for k in keystrokes if k.get("correct"))
    errors = total - correct
    elapsed = sum(k.get("delay_ms", 0) for k in keystrokes) / 1000.0
    if elapsed <= 0:
        return jsonify(status="invalid"), 400
    wpm = round((total / 5.0) / (elapsed / 60.0), 1)
    accuracy = round(correct / total * 100, 1)
    if wpm > 250:
        return jsonify(status="invalid"), 400

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO sessions(wpm, accuracy, total_chars, errors) VALUES (?,?,?,?)",
        (wpm, accuracy, total, errors))
    sid = cur.lastrowid
    for k in keystrokes:
        conn.execute(
            "INSERT INTO keystrokes(session_id, expected, typed, correct, delay_ms) VALUES (?,?,?,?,?)",
            (sid, k.get("expected", ""), k.get("typed", ""),
             1 if k.get("correct") else 0, k.get("delay_ms", 0)))
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