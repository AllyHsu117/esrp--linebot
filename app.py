# app.py
from flask import Flask, request
import sqlite3
from datetime import datetime, timedelta, date
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, QuickReply, QuickReplyButton, MessageAction
import schedule
import threading
import time
import os

app = Flask(__name__)

# ===== LINE API 初始化 =====
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
assert LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET, "LINE API 環境變數未設定"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

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

#------ 判斷今天以已填寫的函數 -------
def has_submitted_today(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT COUNT(*) FROM esrp WHERE user_id=? AND timestamp LIKE ?", (user_id, today + "%"))
    return cursor.fetchone()[0] > 0
# ===== 主訊息邏輯 =====
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    today = date.today().strftime("%Y-%m-%d")
    reply = ""

    # 查身份
    cursor.execute("SELECT role FROM whitelist WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    if not row:
        code = msg.strip()
        if code in valid_codes:  # 🔧 直接判斷 msg 本身是不是驗證碼
            role = valid_codes[code]
            cursor.execute("INSERT OR REPLACE INTO whitelist (user_id, role) VALUES (?, ?)", (user_id, role))
            conn.commit()
            reply = f"✅ 驗證成功，您的身份是：{role}，歡迎使用！"
        else:
            reply = "🚫 請先輸入 4 碼驗證碼（例如：1111）"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return


    role = row[0]


    # 教練 quick reply
    if msg.lower() in ["hi", "嘿", "欸", "誒", "hey"] and role == "教練":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="教練您好，請選擇操作：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="查詢未填學生", text="查詢未填")),
                    QuickReplyButton(action=MessageAction(label="查詢今日回報", text="查詢今日回報")),
                    QuickReplyButton(action=MessageAction(label="查詢 ACWR", text="查詢 ACWR")),
                ])
            )
        )
        return
    if msg == "查詢未填":
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("SELECT user_id FROM whitelist WHERE role='學生'")
        all_students = [r[0] for r in cursor.fetchall()]
        cursor.execute("SELECT DISTINCT user_id FROM esrp WHERE timestamp LIKE ?", (today_str + "%",))
        filled_students = [r[0] for r in cursor.fetchall()]
        not_filled = [uid for uid in all_students if uid not in filled_students]

        names = []
        for uid in not_filled:
            try:
                profile = line_bot_api.get_profile(uid)
                names.append(profile.display_name)
            except:
                names.append(uid)

        if not names:
            reply = "✅ 今天所有學生都已填寫！"
        else:
            reply = "以下學生尚未填寫：\n" + "\n".join(names)

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return
    
    if msg == "查詢今日回報":
        today_str = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("SELECT user_id FROM whitelist WHERE role='學生'")
        all_students = [r[0] for r in cursor.fetchall()]

        lines = []
        total = 0
        count = 0

        for uid in all_students:
            cursor.execute("SELECT srpe, note FROM esrp WHERE user_id=? AND timestamp LIKE ? ORDER BY id DESC LIMIT 1", (uid, today_str + "%"))
            row = cursor.fetchone()

            try:
                profile = line_bot_api.get_profile(uid)
                name = profile.display_name
            except:
                name = uid

            if row:
                srpe, note = row
                if note == "請假":
                    lines.append(f"{name}：請假")
                    total += 0
                    count += 1
                else:
                    lines.append(f"{name}：{srpe}")
                    total += srpe
                    count += 1
            else:
                lines.append(f"{name}：未填")

        if count > 0:
            avg = round(total / count, 1)
            lines.append(f"\n平均 SRPE：{avg}（請假視為 0）")
        else:
            lines.append("\n尚無任何紀錄。")

        reply = "\n".join(lines)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    if msg == "查詢 ACWR":
        today = datetime.now().date()
        this_monday = today - timedelta(days=today.weekday())  # 本週一
        four_weeks_ago = this_monday - timedelta(weeks=4)      # 四週前

        cursor.execute("SELECT user_id FROM whitelist WHERE role='學生'")
        students = cursor.fetchall()

        acwr_lines = []
        team_acwrs = []

        for (uid,) in students:
            # 嘗試取得 LINE display name
            try:
                profile = line_bot_api.get_profile(uid)
                display_name = profile.display_name
            except:
                display_name = uid[-4:]  # 若失敗則 fallback 用 user_id

            # 當週平均
            cursor.execute(
                "SELECT AVG(srpe) FROM esrp WHERE user_id=? AND timestamp >= ? AND timestamp < ? AND note != '請假'",
                (uid, this_monday, this_monday + timedelta(days=7))
            )
            current_week = cursor.fetchone()[0] or 0

            # 前四週平均
            cursor.execute(
                "SELECT AVG(srpe) FROM esrp WHERE user_id=? AND timestamp >= ? AND timestamp < ? AND note != '請假'",
                (uid, four_weeks_ago, this_monday)
            )
            last_4_weeks = cursor.fetchone()[0]

            if last_4_weeks and last_4_weeks > 0:
                acwr = round(current_week / last_4_weeks, 2)
                team_acwrs.append(acwr)
                if acwr > 1.5:
                    emoji = "🔴"
                elif acwr > 1.3:
                    emoji = "🟡"
                else:
                    emoji = "🟢"
                acwr_lines.append(f"{display_name}：{acwr} {emoji}")

        if team_acwrs:
            team_avg = round(sum(team_acwrs) / len(team_acwrs), 2)
            if team_avg > 1.5:
                team_emoji = "🔴"
            elif team_avg > 1.3:
                team_emoji = "🟡"
            else:
                team_emoji = "🟢"
            acwr_lines.append(f"\n球隊平均 ACWR：{team_avg} {team_emoji}")
        else:
            acwr_lines.append("⚠️ 尚未有足夠資料計算 ACWR")

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="\n".join(acwr_lines)))
        return


    # 學生 quick reply
    if msg.lower() in ["hi", "嘿", "欸", "誒", "hey"] and role == "球員":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="請選擇操作：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="回報 RPE", text="我要回報")),
                    QuickReplyButton(action=MessageAction(label="請假", text="請假")),
                    QuickReplyButton(action=MessageAction(label="查詢紀錄", text="查詢"))
                ])
            )
        )
        return

    # 狀態機：學生要回報數值
    if msg == "我要回報":
        reply = "請輸入 SRPE 數值（1–10）及運動時間（分鐘），格式如：6 60"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    if msg.startswith("校正"):
        try:
            parts = msg.replace("校正", "").strip().split()
            rpe, duration = int(parts[0]), int(parts[1])
            srpe = rpe * duration
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            cursor.execute("DELETE FROM esrp WHERE user_id=? AND timestamp LIKE ?", (user_id, today + "%"))
            cursor.execute("INSERT INTO esrp (user_id, srpe, rpe, duration, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                           (user_id, srpe, rpe, duration, "校正", timestamp))
            conn.commit()
            reply = f"✅ 今日紀錄已更新為 SRPE={srpe} ({rpe}x{duration})"
        except:
            reply = "❌ 格式錯誤，請使用：校正 RPE 時間（例如：校正 6 60）"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    if msg == "請假":
        if has_submitted_today(user_id):
            reply = "⚠️ 您今天已填寫過紀錄，若需修改請使用【校正】功能"
        else:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            cursor.execute("INSERT INTO esrp (user_id, srpe, rpe, duration, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, 0, 0, 0, "請假", timestamp))
            conn.commit()
            reply = "✅ 已登記請假"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
    return

    if msg == "查詢":
        cursor.execute("SELECT rpe, duration, srpe, note, timestamp FROM esrp WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))
        records = cursor.fetchall()
        if not records:
            reply = "查無紀錄。"
        else:
            lines = [f"RPE:{r} 時長:{d} SRPE:{s} ({n})\n[{t}]" for r, d, s, n, t in records]
            reply = "\n".join(lines)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # 一般填寫 RPE + 時間
    # 一般填寫 RPE + 時間
    try:
        parts = msg.split()
        if len(parts) == 2:
            if has_submitted_today(user_id):
                reply = "⚠️ 您今天已填寫過紀錄，若需修改請使用【校正】功能（例如：校正 6 60）"
            else:
                rpe = int(parts[0])
                duration = int(parts[1])
                srpe = rpe * duration
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                cursor.execute("INSERT INTO esrp (user_id, srpe, rpe, duration, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                               (user_id, srpe, rpe, duration, "", timestamp))
                conn.commit()
                reply = f"✅ 已記錄 SRPE：{srpe} ({rpe}×{duration})"
    except:
        reply = "⚠️ 請輸入格式正確：RPE 時長（如：6 60）或輸入請假 / 校正"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))


# ====== 每日提醒邏輯 ======
def remind_players():
    cursor.execute("SELECT user_id FROM whitelist WHERE role='球員'")
    users = cursor.fetchall()
    for (uid,) in users:
        line_bot_api.push_message(uid, TextSendMessage(text="🔔 請填寫今天的 RPE 與運動時間（格式如：6 60）"))

for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
    getattr(schedule.every(), day).at("22:00").do(remind_players)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler).start()

# ====== 啟動服務 ======
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
