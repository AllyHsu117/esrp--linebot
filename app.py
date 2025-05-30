# app.py
from flask import Flask, request
import sqlite3
from datetime import datetime, timedelta
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, QuickReply, QuickReplyButton, MessageAction
import schedule
import threading
import time
import os

app = Flask(__name__)

line_bot_api = LineBotApi(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.environ.get("LINE_CHANNEL_SECRET"))

# ===== 資料庫初始化 =====
conn = sqlite3.connect('esrp.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS esrp (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    srpe INTEGER,
    rpe INTEGER,
    duration INTEGER,
    note TEXT,
    timestamp TEXT
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS whitelist (
    user_id TEXT PRIMARY KEY,
    role TEXT
)''')
conn.commit()

# ===== 驗證碼設計 =====
valid_codes = {
    "1111": "球員",
    "0607": "教練"
}

# ===== Webhook 入口點 =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print("Handle Error:", e)
    return 'OK'

# ===== 處理訊息邏輯 =====
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    reply = ""

    # 檢查是否已驗證
    cursor.execute("SELECT role FROM whitelist WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    if not result:
        if msg in valid_codes:
            role = valid_codes[msg]
            cursor.execute("INSERT OR REPLACE INTO whitelist (user_id, role) VALUES (?, ?)", (user_id, role))
            conn.commit()
            reply = f"✅ 驗證成功，確認身份：{role}"
        else:
            reply = "請輸入4碼驗證碼以啟用本機器人"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    role = result[0]

    # Quick Reply
    if msg.lower() == "hi":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="請選擇操作：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="回報 RPE", text="6 60")),
                    QuickReplyButton(action=MessageAction(label="請假", text="請假")),
                    QuickReplyButton(action=MessageAction(label="查詢紀錄", text="查詢")),
                ])
            )
        )
        return

    # 查詢紀錄
    if msg == "查詢":
        cursor.execute("SELECT rpe, duration, srpe, note, timestamp FROM esrp WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))
        records = cursor.fetchall()
        if not records:
            reply = "查無紀錄。"
        else:
            lines = [f"RPE:{r} 時長:{d} = SRPE:{s} ({n}) [{t}]" for r, d, s, n, t in records]
            reply = "\n".join(lines)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # 校正功能
    if msg.startswith("校正"):
        try:
            _, rpe, duration = msg.split()
            rpe = int(rpe)
            duration = int(duration)
            srpe = rpe * duration
            now = datetime.now().strftime("%Y-%m-%d")
            cursor.execute("DELETE FROM esrp WHERE user_id=? AND DATE(timestamp)=?", (user_id, now))
            cursor.execute("INSERT INTO esrp (user_id, srpe, rpe, duration, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                           (user_id, srpe, rpe, duration, "校正", datetime.now().strftime("%Y-%m-%d %H:%M")))
            conn.commit()
            reply = f"✅ 已校正為 SRPE:{srpe} ({rpe}×{duration})"
        except:
            reply = "❌ 請輸入格式：校正 RPE 時長"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # 請假處理
    if "請假" in msg:
        cursor.execute("INSERT INTO esrp (user_id, srpe, rpe, duration, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, 0, 0, 0, msg, datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 備註已記錄：{msg}"))
        return

    # 一般 RPE 回報
    try:
        rpe, duration = map(int, msg.split())
        srpe = rpe * duration
        cursor.execute("INSERT INTO esrp (user_id, srpe, rpe, duration, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, srpe, rpe, duration, "", datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        reply = f"✅ 已記錄 SRPE：{srpe} ({rpe}×{duration})"
    except:
        reply = "❌ 請輸入格式：RPE 時間（例如 6 60）"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# ===== 定時任務 =====
def daily_reminder():
    cursor.execute("SELECT user_id FROM whitelist WHERE role='球員'")
    users = cursor.fetchall()
    for uid, in users:
        line_bot_api.push_message(uid, TextSendMessage(text='🔔 請填寫今日的 RPE 與運動時間'))

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']:
    getattr(schedule.every(), day).at("22:00").do(daily_reminder)

threading.Thread(target=run_schedule).start()

# ===== 教練專屬定時任務 =====
def check_missing():
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT user_id FROM whitelist WHERE role='球員'")
    all_students = set([row[0] for row in cursor.fetchall()])
    cursor.execute("SELECT DISTINCT user_id FROM esrp WHERE DATE(timestamp)=?", (today,))
    submitted = set([row[0] for row in cursor.fetchall()])
    missing = all_students - submitted
    cursor.execute("SELECT user_id FROM whitelist WHERE role='教練'")
    for coach_id in cursor.fetchall():
        msg = "\n".join(missing) if missing else "✅ 所有學生皆已填寫"
        line_bot_api.push_message(coach_id[0], TextSendMessage(text="📋 今日未填名單：\n" + msg))

def daily_summary():
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT user_id, srpe FROM esrp WHERE DATE(timestamp)=?", (today,))
    records = cursor.fetchall()
    lines = [f"{uid[-4:]} SRPE: {srpe}" for uid, srpe in records]
    text = "📊 今日回報總覽：\n" + ("\n".join(lines) if lines else "無資料")
    cursor.execute("SELECT user_id FROM whitelist WHERE role='教練'")
    for coach_id in cursor.fetchall():
        line_bot_api.push_message(coach_id[0], TextSendMessage(text=text))

def weekly_acwr():
    cursor.execute("SELECT user_id FROM whitelist WHERE role='球員'")
    students = [row[0] for row in cursor.fetchall()]
    text = "🔥 ACWR 報告：\n"
    for uid in students:
        cursor.execute("SELECT srpe, timestamp FROM esrp WHERE user_id=? ORDER BY timestamp DESC LIMIT 28", (uid,))
        records = cursor.fetchall()
        if len(records) < 7:
            continue
        srpe_by_day = {}
        for s, t in records:
            day = t.split(" ")[0]
            srpe_by_day.setdefault(day, 0)
            srpe_by_day[day] += s
        sorted_days = sorted(srpe_by_day.items(), reverse=True)
        this_week = [v for d,v in sorted_days[:7]]
        last_4 = [v for d,v in sorted_days[7:28]]
        if not last_4:
            continue
        acwr = sum(this_week)/max(sum(last_4)/4, 1)
        if acwr < 1.3:
            status = "🟢"
        elif acwr <= 1.5:
            status = "🟡"
        else:
            status = "🔴"
        text += f"{uid[-4:]} ACWR: {acwr:.2f} {status}\n"
    cursor.execute("SELECT user_id FROM whitelist WHERE role='教練'")
    for coach_id in cursor.fetchall():
        line_bot_api.push_message(coach_id[0], TextSendMessage(text=text))

# ===== 定時任務設定 =====
schedule.every().monday.at("22:00").do(check_missing)
schedule.every().tuesday.at("22:00").do(check_missing)
schedule.every().wednesday.at("22:00").do(check_missing)
schedule.every().thursday.at("22:00").do(check_missing)
schedule.every().friday.at("22:00").do(check_missing)
schedule.every().monday.at("23:30").do(daily_summary)
schedule.every().tuesday.at("23:30").do(daily_summary)
schedule.every().wednesday.at("23:30").do(daily_summary)
schedule.every().thursday.at("23:30").do(daily_summary)
schedule.every().friday.at("23:30").do(daily_summary)
schedule.every().sunday.at("22:00").do(weekly_acwr)

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_schedule).start()

# ===== 執行主程式 =====

if __name__ == "__main__":
    from os import environ
    port = int(environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=port)

