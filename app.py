from flask import Flask, request
import sqlite3
from datetime import datetime
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import schedule
import threading
import time

app = Flask(__name__)

line_bot_api = LineBotApi('BgnQvOHL4eDBoYj8/A11+Vxw3EIiMbCqRNFmwI87OYaL98louNY17OuFhWDXRoBzO2TJ5fwVIMuvVgNgalPOLD28jyAfX3ayIZrJ5TvgEy7CG7os9Gz+O9JS0y+S4ME7q4+KPq5ofMIlvSWpNTsMLAdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('3bea3ced2a7d267a563d87642bb288bb')

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
conn.commit()

# ===== 驗證機制 =====
valid_codes = {
    "1111": "學生",
    "0607": "教練"
}
whitelist = {}

# ===== 接收訊息處理邏輯 =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print("Handle Error:", e)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    reply = ""

    # 驗證流程
    if user_id not in whitelist:
        if msg.startswith("驗證"):
            code = msg.replace("驗證", "").strip()
            if code in valid_codes:
                role = valid_codes[code]
                whitelist[user_id] = {"role": role}
                reply = f"✅ 驗證成功，你的身份是：{role}，歡迎使用！"
            else:
                reply = "❌ 驗證碼錯誤，請重新輸入。"
        else:
            reply = "🚫 請先輸入驗證碼，例如：驗證 1111"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # 驗證後使用功能
    role = whitelist[user_id]["role"]

    if msg.lower() == "查詢":
        cursor.execute("SELECT rpe, duration, srpe, note, timestamp FROM esrp WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))
        records = cursor.fetchall()
        if not records:
            reply = "查無紀錄。"
        else:
            lines = [f"RPE:{r} 時長:{d} 分鐘 = SRPE:{s} ({n}) [{t}]" for r, d, s, n, t in records]
            reply = "\n".join(lines)

    elif any(x in msg for x in ["請假", "沒去", "校正"]):
        note = msg
        rpe = 0
        duration = 0
        srpe = 0
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        cursor.execute("INSERT INTO esrp (user_id, srpe, rpe, duration, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, srpe, rpe, duration, note, timestamp))
        conn.commit()
        reply = f"✅ 備註已記錄：{note}"

    else:
        try:
            parts = msg.split()
            if len(parts) == 2:
                rpe = int(parts[0])
                duration = int(parts[1])
                srpe = rpe * duration
                note = ""
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                cursor.execute("INSERT INTO esrp (user_id, srpe, rpe, duration, note, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                               (user_id, srpe, rpe, duration, note, timestamp))
                conn.commit()
                reply = f"✅ 已記錄 SRPE：{srpe} ({rpe}×{duration})"
            else:
                reply = "請輸入格式：RPE 運動時間（例如：6 60）或輸入備註如請假／校正 8"
        except:
            reply = "❌ 資料格式錯誤，請輸入數字"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# ===== 定時提醒邏輯（未來補上） =====
def reminder():
    for user_id in whitelist:
        if whitelist[user_id]['role'] == '學生':
            line_bot_api.push_message(user_id, TextSendMessage(text='🔔 請填寫今日的 RPE 與運動時間'))

# 每日 22:00 提醒
schedule.every().monday.at("22:00").do(reminder)
schedule.every().tuesday.at("22:00").do(reminder)
schedule.every().wednesday.at("22:00").do(reminder)
schedule.every().thursday.at("22:00").do(reminder)
schedule.every().friday.at("22:00").do(reminder)

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler).start()

if __name__ == "__main__":
    from os import environ
    port = int(environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=port)

