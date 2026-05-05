import os
import time
import requests
import pandas as pd
import telebot
from telebot import types
import re
from io import BytesIO
import sqlite3
import threading
from groq import Groq

# ================= CONFIG =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
LOG_GROUP_ID = int(os.environ.get("LOG_GROUP_ID", 0))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8080")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq = Groq(api_key=GROQ_API_KEY)

# ================= DB (NO FIREBASE) =================
conn = sqlite3.connect("leads.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS leads (
channel_id TEXT PRIMARY KEY,
name TEXT,
link TEXT,
country TEXT,
category TEXT,
subs INTEGER,
emails TEXT,
links TEXT
)
""")
conn.commit()

# ================= STATE =================
user_session = {}
active_missions = {}
blocked_channels = set()

# ================= TARGET =================
PRIORITY = {
    "ID": "Indonesia",
    "PH": "Philippines"
}

SECONDARY = {
    "BR": "Brazil",
    "PK": "Pakistan",
    "BD": "Bangladesh",
    "VN": "Vietnam",
    "AR": "Argentina",
    "ES": "Spain",
    "SG": "Singapore"
}

ALL = {**PRIORITY, **SECONDARY}

CATEGORIES = [
    "remote work",
    "freelancing",
    "online earning",
    "work from home",
    "career tips",
    "personal finance",
    "app review",
    "money transfer"
]

# ================= UTIL =================
def log(msg):
    if LOG_GROUP_ID:
        try:
            bot.send_message(LOG_GROUP_ID, msg)
        except:
            pass


def extract_contact(text):
    emails = re.findall(r"[a-z0-9\.+-]+@[a-z0-9\.-]+\.[a-z0-9]+", text.lower())
    links = re.findall(r"(https?://[^\s]+)", text)
    return emails, links


# ================= ABOUT SCRAPER =================
def get_about(channel_id):
    url = "https://www.googleapis.com/youtube/v3/channels"

    res = requests.get(url, params={
        "part": "snippet,statistics,brandingSettings",
        "id": channel_id,
        "key": YOUTUBE_API_KEY
    }).json()

    if "items" not in res:
        return None

    ch = res["items"][0]

    desc = ch["snippet"].get("description", "")
    title = ch["snippet"]["title"]

    return title, desc, ch


# ================= AI FILTER =================
def ai_score(title, desc, category):
    prompt = f"""
Channel: {title}
Description: {desc[:500]}
Category: {category}

Return ONLY JSON:
{{"score": 0-100, "reason": "short reason"}}
"""

    try:
        r = groq.chat.completions.create(
            model="llama3-8b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        ).choices[0].message.content

        data = eval(re.search(r"\{.*\}", r).group())
        return data.get("score", 50), data.get("reason", "")
    except:
        return 60, "default match"


# ================= START =================
@bot.message_handler(commands=['start'])
def start(msg):
    markup = types.InlineKeyboardMarkup()
    for c, n in PRIORITY.items():
        markup.add(types.InlineKeyboardButton(n, callback_data=f"c_{c}"))

    bot.send_message(msg.chat.id, "🌍 Select Country:", reply_markup=markup)
    log(f"User {msg.chat.id} started bot")


# ================= COUNTRY =================
@bot.callback_query_handler(func=lambda c: c.data.startswith("c_"))
def country(c):
    code = c.data.split("_")[1]

    user_session[c.message.chat.id] = {
        "country": ALL[code],
        "code": code
    }

    markup = types.InlineKeyboardMarkup()
    for i, cat in enumerate(CATEGORIES):
        markup.add(types.InlineKeyboardButton(cat, callback_data=f"cat_{i}"))

    bot.send_message(c.message.chat.id, "🎯 Select Category:", reply_markup=markup)


# ================= CATEGORY =================
@bot.callback_query_handler(func=lambda c: c.data.startswith("cat_"))
def cat(c):
    cat = CATEGORIES[int(c.data.split("_")[1])]
    chat = c.message.chat.id

    active_missions[chat] = True

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🛑 STOP", callback_data="stop"))

    bot.send_message(chat, f"🚀 Mission Started: {cat}", reply_markup=markup)

    threading.Thread(target=run_mission, args=(chat, cat)).start()


# ================= STOP =================
@bot.callback_query_handler(func=lambda c: c.data == "stop")
def stop(c):
    active_missions[c.message.chat.id] = False
    bot.send_message(c.message.chat.id, "🛑 Mission Stopped")
    log(f"Mission stopped by {c.message.chat.id}")


# ================= MISSION =================
def run_mission(chat_id, category):
    country = user_session[chat_id]["country"]
    code = user_session[chat_id]["code"]

    keywords = [category, f"{category} tips", f"{category} app"]

    collected = 0

    for q in keywords:
        if not active_missions.get(chat_id):
            return

        url = "https://www.googleapis.com/youtube/v3/search"

        res = requests.get(url, params={
            "part": "snippet",
            "type": "channel",
            "q": q,
            "regionCode": code,
            "maxResults": 30,
            "key": YOUTUBE_API_KEY
        }).json()

        for item in res.get("items", []):

            channel_id = item["snippet"]["channelId"]

            if channel_id in blocked_channels:
                continue

            about = get_about(channel_id)
            if not about:
                continue

            title, desc, ch = about

            subs = int(ch["statistics"].get("subscriberCount", 0))
            if subs < 10000:
                continue

            score, reason = ai_score(title, desc, category)
            if score < 80:
                continue

            emails, links = extract_contact(desc)

            # duplicate check
            cur.execute("SELECT channel_id FROM leads WHERE channel_id=?", (channel_id,))
            if cur.fetchone():
                continue

            link = f"https://youtube.com/channel/{channel_id}"

            cur.execute("""
            INSERT INTO leads VALUES (?,?,?,?,?,?,?,?)
            """, (
                channel_id, title, link, country, category,
                subs, ",".join(emails), ",".join(links)
            ))
            conn.commit()

            collected += 1

            bot.send_message(chat_id, f"✅ {title}\nSubs: {subs}\nScore: {score}")

            log(f"Lead found: {title} | {subs}")

            time.sleep(1)

    finish(chat_id, collected)


# ================= FINISH =================
def finish(chat_id, count):
    if count == 0:
        bot.send_message(chat_id, "❌ No leads found")
        return

    df = pd.read_sql("SELECT * FROM leads", conn)

    # CSV
    csv = BytesIO()
    df.to_csv(csv, index=False)
    csv.seek(0)

    # Excel
    excel = BytesIO()
    df.to_excel(excel, index=False)
    excel.seek(0)

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📄 CSV", callback_data="csv"))
    markup.add(types.InlineKeyboardButton("📊 EXCEL", callback_data="excel"))

    bot.send_message(chat_id, f"🏁 Done: {count} leads", reply_markup=markup)

    log(f"Mission completed: {count} leads")


# ================= ADMIN =================
@bot.message_handler(commands=['admin'])
def admin(msg):
    if msg.chat.id != ADMIN_ID:
        return

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🚫 Block Channel", callback_data="block"))
    markup.add(types.InlineKeyboardButton("🧹 Clear DB", callback_data="clear"))

    bot.send_message(msg.chat.id, "⚙️ Admin Panel", reply_markup=markup)


# ================= RUN =================
print("🚀 BOT RUNNING")
bot.infinity_polling()
