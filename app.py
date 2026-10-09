"""Speed Typing Game - Flask web app + analytics dashboard.

Phạm vi Thành viên 1: logic game, telemetry nhịp gõ (delay_ms),
ghi log CSV, hai chế độ Normal/Adaptive, dashboard phân tích.
"""

# ==================================================================
# 1. IMPORTS
# ==================================================================
import csv
import os
import random
import secrets
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask, render_template, request, jsonify

# ==================================================================
# 2. CONFIG
# ==================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "typing.db")
SENTENCES_PATH = os.path.join(BASE_DIR, "sentences.txt")
CSV_PATH = os.path.join(BASE_DIR, "keystrokes_log.csv")

UTC_TZ = ZoneInfo("UTC")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

KB_LAYOUT = ["qwertyuiop", "asdfghjkl", "zxcvbnm", " "]

TOKENS = {}           # token -> câu đã phát cho lượt chơi đó (một lần)
LAST_SUBMIT = {}      # chặn spam theo IP

MIN_AVG_DELAY_MS = 60   # trung bình < 60ms/phím => siêu nhân (~200 WPM+)
MAX_WPM = 250
ADAPTIVE_TOP_N = 3      # Adaptive: rơi ngẫu nhiên vào top N câu "khoai" nhất

app = Flask(__name__)


# ==================================================================
# 3. DATABASE HELPERS
# ==================================================================
def get_db():
    conn = sqlite3.connect(DB_PATH)
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


# ==================================================================
# 4. UTILITY HELPERS
# ==================================================================
def load_sentences():
    """Đọc danh sách câu gõ từ file sentences.txt."""
    with open(SENTENCES_PATH, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def to_vn(iso_str):
    """Đổi timestamp UTC (từ SQLite) sang giờ Việt Nam để hiển thị."""
    try:
        dt = datetime.fromisoformat(iso_str).replace(tzinfo=UTC_TZ)
        return dt.astimezone(VN_TZ).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return iso_str


def build_heatmap(rows):
    """Gom thống kê phím thành dict phục vụ bản đồ nhiệt."""
    heat = {}
    for r in rows:
        errs = r["errs"] or 0
        cls = "k-bad" if errs > 3 else "k-mid" if errs > 0 else "k-ok"
        heat[r["expected"]] = {"cls": cls, "errs": errs, "delay": r["avg_delay"]}
    return heat


def log_csv(session_id, sentence, keystrokes):
    """Telemetry: nối từng nhịp gõ vào keystrokes_log.csv (đúng đề bài)."""
    new_file = not os.path.exists(CSV_PATH)
    ts = datetime.now(VN_TZ).isoformat(timespec="milliseconds")
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "session_id", "expected",
                        "typed", "correct", "delay_ms"])
        for i, k in enumerate(keystrokes):
            w.writerow([ts, session_id, sentence[i], k.get("typed", ""),
                        1 if k.get("typed", "") == sentence[i] else 0,
                        int(k.get("delay_ms", 0))])


def pick_adaptive_sentence():
    """Adaptive Mode: ưu tiên câu chứa nhiều phím người chơi hay sai."""
    conn = get_db()
    rows = conn.execute("""SELECT expected, SUM(correct = 0) AS errs
                           FROM keystrokes GROUP BY expected""").fetchall()
    conn.close()
    errs = {r["expected"]: (r["errs"] or 0) for r in rows}
    sentences = load_sentences()
    if not sentences:
        return ""
    if not any(errs.values()):
        return random.choice(sentences)     # chưa có dữ liệu -> chơi thường
    scored = sorted(sentences,
                    key=lambda s: sum(errs.get(ch, 0) for ch in s),
                    reverse=True)
    return random.choice(scored[:ADAPTIVE_TOP_N])


def issue_sentence(mode="normal"):
    """Phát cặp (token một lần, câu gõ) theo chế độ được chọn."""
    token = secrets.token_hex(8)
    sentence = (pick_adaptive_sentence() if mode == "adaptive"
                else random.choice(load_sentences()))
    TOKENS[token] = sentence
    return token, sentence


# ==================================================================
# 5. ROUTES
# ==================================================================
@app.route("/")
def index():
    token, sentence = issue_sentence("normal")
    return render_template("index.html", sentence=sentence, token=token)


@app.route("/api/sentence")
def next_sentence():
    """Cấp câu + token mới cho ván kế tiếp (không cần tải lại trang)."""
    mode = request.args.get("mode", "normal")
    if mode not in ("normal", "adaptive"):
        mode = "normal"
    token, sentence = issue_sentence(mode)
    return jsonify(sentence=sentence, token=token, mode=mode)


@app.route("/api/submit", methods=["POST"])
def submit():
    # Lớp 1: rate limit — mỗi IP tối đa 1 submit / 5 giây
    ip = request.remote_addr or "unknown"
    now = time.time()
    if now - LAST_SUBMIT.get(ip, 0) < 5:
        return jsonify(status="too fast"), 429
    LAST_SUBMIT[ip] = now

    data = request.get_json(silent=True) or {}

    # Lớp 2: token một lần + lấy đúng câu gốc đã phát cho token đó
    token = data.get("token", "")
    sentence = TOKENS.pop(token, None)
    if sentence is None:
        return jsonify(status="invalid token"), 403

    # Lớp 3: server TỰ đối chiếu từng phím với câu gốc, không tin client
    keystrokes = data.get("keystrokes", [])
    if len(keystrokes) != len(sentence):
        return jsonify(status="invalid"), 400

    delays = []
    correct = 0
    for i, k in enumerate(keystrokes):
        if k.get("expected") != sentence[i]:
            return jsonify(status="invalid"), 400      # sai thứ tự = không phải câu của tôi
        if k.get("typed", "") == sentence[i]:          # server tự phán đúng/sai
            correct += 1
        d = k.get("delay_ms", 0)
        if not isinstance(d, (int, float)) or d < 0 or d > 10000:
            return jsonify(status="invalid"), 400
        delays.append(d)

    total = len(keystrokes)
    errors = total - correct
    elapsed = sum(delays) / 1000.0
    if elapsed <= 0:
        return jsonify(status="invalid"), 400
    wpm = round((total / 5.0) / (elapsed / 60.0), 1)
    accuracy = round(correct / total * 100, 1)

    # Lớp 4: heuristic "có phải người thật không?"
    if wpm > MAX_WPM:
        return jsonify(status="invalid"), 400
    if sum(delays) / total < MIN_AVG_DELAY_MS:
        return jsonify(status="superhuman"), 400       # phản xạ nhanh hơn người thật
    if len(set(delays)) <= 2:
        return jsonify(status="robotic"), 400          # delay đều tăm tắp = máy gõ

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO sessions(wpm, accuracy, total_chars, errors) VALUES (?,?,?,?)",
        (wpm, accuracy, total, errors))
    sid = cur.lastrowid
    for i, k in enumerate(keystrokes):
        conn.execute(
            "INSERT INTO keystrokes(session_id, expected, typed, correct, delay_ms) VALUES (?,?,?,?,?)",
            (sid, sentence[i], k.get("typed", ""),
             1 if k.get("typed", "") == sentence[i] else 0, int(delays[i])))
    conn.commit()
    conn.close()

    # Telemetry song song: ghi file CSV phục vụ phân tích offline
    try:
        log_csv(sid, sentence, keystrokes)
    except OSError:
        pass    # file log hỏng cũng không được làm hỏng lượt chơi

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

    # Lịch sử lượt chơi kèm thời gian hiển thị giờ Việt Nam
    session_list = []
    for s in sessions:
        d = dict(s)
        d["created_at_vn"] = to_vn(s["created_at"])
        session_list.append(d)

    heat = build_heatmap(rows)
    wpm_history = [s["wpm"] for s in reversed(sessions)]
    acc_history = [s["accuracy"] for s in reversed(sessions)]

    return render_template("dashboard.html",
                           sessions=session_list,
                           layout=KB_LAYOUT,
                           heat=heat,
                           wpm_history=wpm_history,
                           acc_history=acc_history)


# ==================================================================
# 6. ENTRY POINT
# ==================================================================
init_db()

if __name__ == "__main__":
    app.run(debug=True)