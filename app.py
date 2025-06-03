# app.py（完整版本，使用 Google Sheets 替代 SQLite）
from flask import Flask, request
from datetime import datetime, timedelta, date
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, QuickReply, QuickReplyButton, MessageAction
import schedule
import threading
import time
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials


# ===== Flask & LINE 初始化 =====
app = Flask(__name__)
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")
assert LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET, "LINE API 環境變數未設定"
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ===== Google Sheets API 初始化 =====

import json
import os
from google.oauth2 import service_account
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

GOOGLE_CREDS = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
creds = service_account.Credentials.from_service_account_info(GOOGLE_CREDS, scopes=SCOPES)

client = gspread.authorize(creds)
sheet = client.open("SRPE")
esrp_sheet = sheet.worksheet("srpe")
whitelist_sheet = sheet.worksheet("whitelist")

# ===== 驗證碼設定 =====
valid_codes = {
    "1111": "球員",
    "0607": "教練"
}

# ===== Helper Functions =====
def get_role(user_id):
    rows = whitelist_sheet.get_all_records()
    for row in rows:
        if row["user_id"] == user_id:
            return row["role"]
    return None

def add_to_whitelist(user_id, role):
    try:
        profile = line_bot_api.get_profile(user_id)
        name = profile.display_name
    except:
        name = "未知名稱"

    rows = whitelist_sheet.get_all_records()
    for i, row in enumerate(rows):
        if row["user_id"] == user_id:
            whitelist_sheet.delete_rows(i + 2)
            break
    whitelist_sheet.append_row([user_id, role, name])


def has_submitted_today(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    data = esrp_sheet.get_all_records()
    return any(row["user_id"] == user_id and row["timestamp"].startswith(today) for row in data)

def write_esrp(user_id, srpe, rpe, duration, note):
    # 台灣時間 = UTC + 8 小時
    timestamp = (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    esrp_sheet.append_row([user_id, srpe, rpe, duration, note, timestamp])

def delete_today_esrp(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    data = esrp_sheet.get_all_records()
    for i, row in enumerate(data):
        if row["user_id"] == user_id and row["timestamp"].startswith(today):
            esrp_sheet.delete_rows(i + 2)
            break

# ===== Webhook Endpoint =====
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print("Handle Error:", e)
    return 'OK'

# ===== LINE Message Handler =====
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    msg = event.message.text.strip()
    today = date.today().strftime("%Y-%m-%d")
    reply = ""

    role = get_role(user_id)

    if not role:
        if msg in valid_codes:
            role = valid_codes[msg]
            add_to_whitelist(user_id, role)
            reply = f"✅ 驗證成功，您的身份是：{role}，歡迎使用！"
        else:
            reply = "🚫 請先輸入 4 碼驗證碼（例如：1111）"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

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

    # 查詢未填
    if msg == "查詢未填":
        today_str = datetime.now().strftime("%Y-%m-%d")
        all_students = [r["user_id"] for r in whitelist_sheet.get_all_records() if r["role"] == "球員"]
        filled = [r["user_id"] for r in esrp_sheet.get_all_records() if r["timestamp"].startswith(today_str)]
        not_filled = [uid for uid in all_students if uid not in filled]
        names = []
        for uid in not_filled:
            try:
                profile = line_bot_api.get_profile(uid)
                names.append(profile.display_name)
            except:
                names.append(uid[-4:])
        reply = "✅ 今天所有學生都已填寫！" if not names else "以下學生尚未填寫：\n" + "\n".join(names)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # 查詢今日回報
    if msg == "查詢今日回報":
        today_str = datetime.now().strftime("%Y-%m-%d")
        all_students = [r["user_id"] for r in whitelist_sheet.get_all_records() if r["role"] == "球員"]
        data = esrp_sheet.get_all_records()
        lines = []
        total = 0
        count = 0
        for uid in all_students:
            record = next((r for r in data if r["user_id"] == uid and r["timestamp"].startswith(today_str)), None)
            try:
                name = line_bot_api.get_profile(uid).display_name
            except:
                name = uid[-4:]
            if record:
                note = record["note"]
                srpe = record["srpe"]
                if note == "請假":
                    lines.append(f"{name}：請假")
                else:
                    lines.append(f"{name}：{srpe}")
                    total += int(srpe)
                    count += 1
            else:
                lines.append(f"{name}：未填")
        avg = round(total / count, 1) if count else 0
        lines.append(f"\n平均 SRPE：{avg}（請假視為 0）")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="\n".join(lines)))
        return

    # 查詢 ACWR
    if msg == "查詢 ACWR":
        today = datetime.now().date()
        this_monday = today - timedelta(days=today.weekday())
        four_weeks_ago = this_monday - timedelta(weeks=4)
        data = esrp_sheet.get_all_records()
        students = [r["user_id"] for r in whitelist_sheet.get_all_records() if r["role"] == "球員"]
        lines = []
        acwrs = []
        for uid in students:
            try:
                name = line_bot_api.get_profile(uid).display_name
            except:
                name = uid[-4:]
            cur = [int(r["srpe"]) for r in data if r["user_id"] == uid and this_monday.strftime("%Y-%m-%d") <= r["timestamp"][:10] < (this_monday + timedelta(days=7)).strftime("%Y-%m-%d") and r["note"] != "請假"]
            prev = [int(r["srpe"]) for r in data if r["user_id"] == uid and four_weeks_ago.strftime("%Y-%m-%d") <= r["timestamp"][:10] < this_monday.strftime("%Y-%m-%d") and r["note"] != "請假"]
            if prev:
                acwr = round(sum(cur)/len(cur) / (sum(prev)/len(prev)), 2) if cur else 0
                emoji = "🔴" if acwr > 1.5 else "🟡" if acwr > 1.3 else "🟢"
                lines.append(f"{name}：{acwr} {emoji}")
                acwrs.append(acwr)
        if acwrs:
            team_avg = round(sum(acwrs)/len(acwrs), 2)
            emoji = "🔴" if team_avg > 1.5 else "🟡" if team_avg > 1.3 else "🟢"
            lines.append(f"\n球隊平均 ACWR：{team_avg} {emoji}")
        else:
            lines.append("⚠️ 尚未有足夠資料計算 ACWR")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="\n".join(lines)))
        return

    # 學生 quick reply
    if msg.lower() in ["hi", "Hi", "欸", "誒", "hey"] and role == "球員":
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

    if msg == "我要回報":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入 SRPE 數值（1–10）及運動時間（分鐘），格式如：6 60"))
        return

    if msg == "請假":
        if has_submitted_today(user_id):
            reply = "⚠️ 您今天已填寫過紀錄，若需修改請使用【校正】功能"
        else:
            write_esrp(user_id, 0, 0, 0, "請假")
            reply = "✅ 已登記請假"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    if msg.startswith("校正"):
        try:
            parts = msg.replace("校正", "").strip().split()
            rpe, duration = int(parts[0]), int(parts[1])
            srpe = rpe * duration
            delete_today_esrp(user_id)
            write_esrp(user_id, srpe, rpe, duration, "校正")
            reply = f"✅ 今日紀錄已更新為 SRPE={srpe} ({rpe}x{duration})"
        except:
            reply = "❌ 格式錯誤，請使用：校正 RPE 時間（例如：校正 6 60）"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return


    if msg == "查詢":
        records = [r for r in esrp_sheet.get_all_records() if r["user_id"] == user_id]
        last10 = records[-10:][::-1]

        if not last10:
            reply = "查無紀錄。"
        else:
            lines = []
            for r in last10:
                note = f"（{r['note']}）" if r.get("note") else ""
                line = f"RPE:{r['rpe']} 時長:{r['duration']} SRPE:{r['srpe']}{note}\n[{r['timestamp']}]"
                lines.append(line)

            reply = "\n".join(lines)

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return


    try:
        parts = msg.split()
        if len(parts) == 2:
            if has_submitted_today(user_id):
                reply = "⚠️ 您今天已填寫過紀錄，若需修改請使用【校正】功能（例如：校正 6 60）"
            else:
                rpe = int(parts[0])
                duration = int(parts[1])
                srpe = rpe * duration
                write_esrp(user_id, srpe, rpe, duration, "")
                reply = f"✅ 已記錄 SRPE：{srpe} ({rpe}×{duration})"
        else:
            reply = "⚠️ 請輸入格式正確：RPE 時長（如：6 60）或輸入請假 / 校正"
    except:
        reply = "⚠️ 請輸入格式正確：RPE 時長（如：6 60）或輸入請假 / 校正"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

# ===== 每日提醒排程 =====
from flask import Flask, request
from linebot.models import TextSendMessage
from datetime import datetime, timedelta

@app.route("/daily_remind", methods=["GET"])
def daily_remind():
    now = datetime.utcnow() + timedelta(hours=8)  # 台灣時間
    taiwan_hour = now.hour
    weekday = now.weekday()  # 0=週一, 6=週日

    if 22 <= taiwan_hour < 23 and weekday < 5:  # 週一到週五 22:00-22:59
        try:
            rows = whitelist_sheet.get_all_records()
            for row in rows:
                if row["role"] == "球員":
                    uid = row["user_id"]
                    line_bot_api.push_message(
                        uid,
                        TextSendMessage(text="🔔 請填寫今天的 sRPE 數值與運動時間（格式如：6 60）")
                    )
            return "✅ 已於台灣時間 22:00-23:00 推播提醒"
        except Exception as e:
            return f"❌ 發送提醒時出錯：{e}"
    else:
        return f"⌛ 現在非推播時間（目前台灣時間：{now.strftime('%Y-%m-%d %H:%M:%S')}）"
    
@app.route("/coach_daily_report", methods=["GET"])
def coach_daily_report():
    now = datetime.utcnow() + timedelta(hours=8)  # 台灣時間
    today_str = now.strftime("%Y-%m-%d")

    # 限定執行時間為 23:30～23:59，避免 Apps Script 提前觸發
    if not (now.hour == 23 and now.minute >= 30):
        return f"⌛ 現在非推播時間（目前台灣時間：{now.strftime('%Y-%m-%d %H:%M:%S')}）"

    try:
        data = esrp_sheet.get_all_records()
        whitelist = whitelist_sheet.get_all_records()

        # 篩選出教練 ID
        coach_ids = [row["user_id"] for row in whitelist if row["role"] == "教練"]
        # 建立 user_id 對應球員名字的字典
        players = {row["user_id"]: row["name"] for row in whitelist if row["role"] == "球員"}

        # 只取今天的資料
        today_data = [row for row in data if row["date"].startswith(today_str)]

        if not today_data:
            message = f"📋 今日（{today_str}）尚無球員填寫 sRPE 資料"
        else:
            message = f"📋 今日（{today_str}）sRPE 回報彙整：\n"
            for row in today_data:
                name = players.get(row["user_id"], "未知球員")
                message += f"{name}\nRPE:{row['rpe']} 時長:{row['duration']} SRPE:{row['srpe']}"
                if row.get("note") == "校正":
                    message += "（校正）"
                message += f"\n[{row['timestamp']}]\n"

        # 推播給所有教練
        for coach_id in coach_ids:
            line_bot_api.push_message(coach_id, TextSendMessage(text=message))

        return "✅ 教練推播完成"
    except Exception as e:
        return f"❌ 發送 coach 報表出錯：{e}"


# ===== 啟動服務 =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
