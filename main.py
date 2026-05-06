import os
import time
import requests
import pandas as pd
import telebot
from telebot import types
from groq import Groq
import re
import json
from flask import Flask
import threading
import firebase_admin
from firebase_admin import credentials, firestore
from io import BytesIO

# ================= Configuration =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
PORT = int(os.environ.get("PORT", 8080))
# Render URL for keeping the bot alive
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")

# ================= Firebase Setup =================
firebase_json_str = os.environ.get("FIREBASE_CREDENTIALS")
db = None
if firebase_json_str:
    try:
        cred_dict = json.loads(firebase_json_str)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Firebase Active")
    except Exception as e:
        print(f"⚠️ Firebase Error: {e}")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# ================= 24/7 Keep-Alive & Wake Up =================
app = Flask(__name__)
@app.route('/')
def live(): return "Hurupay System Online"

def self_ping():
    while True:
        time.sleep(300) # Ping every 5 mins
        try: requests.get(RENDER_URL)
        except: pass

# ================= Target Markets & Settings =================
MARKETS = {
    "Indonesia": "ID", "Philippines": "PH", "Brazil": "BR", 
    "Pakistan": "PK", "Bangladesh": "BD", "Vietnam": "VN",
    "Argentina": "AR", "Spain": "ES", "Ukraine": "UA", 
    "Serbia": "RS", "Singapore": "SG"
}

CATEGORIES = [
    "Remote work / Freelancing", "Work-from-home jobs", "Online earning / Side hustles",
    "Career tips", "Personal finance", "Tech/App reviews", "Remittance / Sending money abroad"
]

user_session = {}
active_missions = {}

# ================= UI Design =================
def main_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🚀 START NEW MISSION", callback_data="start_mission"),
        types.InlineKeyboardButton("📥 DOWNLOAD ALL (EXCEL)", callback_data="full_excel"),
        types.InlineKeyboardButton("📥 DOWNLOAD ALL (CSV)", callback_data="full_csv")
    )
    return markup

@bot.message_handler(commands=['start'])
def start_bot(message):
    bot.send_message(
        message.chat.id, 
        "⚡ **Hurupay Lead System Awakened!**\nEverything is ready for processing.", 
        reply_markup=main_menu(), parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data == "start_mission")
def select_country(call):
    markup = types.InlineKeyboardMarkup(row_width=2)
    btns = [types.InlineKeyboardButton(name, callback_data=f"setco_{name}") for name in MARKETS.keys()]
    markup.add(*btns)
    markup.add(types.InlineKeyboardButton("⬅️ BACK", callback_data="back_home"))
    bot.edit_message_text("🌍 **Select Target Market:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('setco_'))
def select_cat(call):
    country = call.data.split('_')[1]
    user_session[call.message.chat.id] = {'country': country}
    markup = types.InlineKeyboardMarkup(row_width=1)
    for i, cat in enumerate(CATEGORIES):
        markup.add(types.InlineKeyboardButton(cat, callback_data=f"setcat_{i}"))
    markup.add(types.InlineKeyboardButton("⬅️ BACK", callback_data="start_mission"))
    bot.edit_message_text(f"🎯 Market: {country}\n**Select Niche:**", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

# ================= Scraper Engine =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('setcat_'))
def run_mission(call):
    idx = int(call.data.split('_')[1])
    niche = CATEGORIES[idx]
    chat_id = call.message.chat.id
    country = user_session[chat_id]['country']
    
    active_missions[chat_id] = True
    stop_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🛑 STOP MISSION", callback_data="stop_now"))
    bot.send_message(chat_id, f"🚀 **Mission Live!**\n📍 {country} | 📂 {niche}", reply_markup=stop_markup, parse_mode="Markdown")
    
    threading.Thread(target=process_leads, args=(chat_id, country, niche)).start()

def process_leads(chat_id, country, niche):
    # Strict location filtering using query and region code
    region_code = MARKETS.get(country, "")
    query = f"{niche} {country}"
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={query}&maxResults=50&regionCode={region_code}&key={YOUTUBE_API_KEY}"
    
    mission_leads = []
    try:
        data = requests.get(url).json()
        for item in data.get('items', []):
            if not active_missions.get(chat_id): break
            
            chan_id = item['snippet']['channelId']
            # Duplicate check in Firebase
            if db and db.collection('leads').document(chan_id).get().exists: continue

            # Deep Channel Profile
            c_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics,brandingSettings&id={chan_id}&key={YOUTUBE_API_KEY}"
            c_data = requests.get(c_url).json()['items'][0]
            
            subs = int(c_data['statistics'].get('subscriberCount', 0))
            if subs < 10000: continue # Client Threshold

            desc = c_data['snippet'].get('description', '')
            title = c_data['snippet']['title']
            
            # Contact Mining (Email & Social/WhatsApp)
            contacts = re.findall(r"[a-z0-9\.+-]+@[a-z0-9\.-]+\.[a-z0-9]+|wa\.me/\d+", desc.lower())
            links = re.findall(r"instagram\.com/[^\s]+|t\.me/[^\s]+|facebook\.com/[^\s]+", desc.lower())
            all_contact = ", ".join(list(set(contacts + links))) or "No direct contact found"

            # AI Lead Validation
            fit_note = analyze_with_ai(title, desc, niche)

            lead = {
                "Creator/Channel Name": title,
                "Platform": "YouTube",
                "Account Link": f"https://youtube.com/channel/{chan_id}",
                "Country": country,
                "Niche": niche,
                "Subscriber Count": subs,
                "Contact Details": all_contact,
                "Why they fit Hurupay": fit_note
            }
            
            if db: db.collection('leads').document(chan_id).set(lead)
            mission_leads.append(lead)
            bot.send_message(chat_id, f"✅ **Found:** {title}\n👥 Subs: {subs}\n📧 {all_contact[:50]}...")
            time.sleep(1)

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Error: {str(e)}")

    finalize_files(chat_id, mission_leads)

def analyze_with_ai(name, desc, niche):
    prompt = f"Creator: {name}. Niche: {niche}. Bio: {desc[:300]}. Why is this creator good for a USD remittance app like Hurupay? (1 short sentence)"
    try:
        resp = groq_client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model="llama3-8b-8192").choices[0].message.content
        return resp.strip()
    except: return "Targets audience interested in online earning and finance."

# ================= File Generation & Back Buttons =================
def finalize_files(chat_id, leads):
    active_missions[chat_id] = False
    if not leads:
        bot.send_message(chat_id, "🏁 Mission ended. No new unique leads found.", reply_markup=main_menu())
        return

    df = pd.DataFrame(leads)
    
    # Send Excel Directly
    ex_io = BytesIO()
    with pd.ExcelWriter(ex_io, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False)
    ex_io.seek(0)
    bot.send_document(chat_id, ex_io, visible_file_name="Mission_Result.xlsx", caption="🎯 Mission Completed! (Excel)")

    # Send CSV Directly
    cs_io = BytesIO()
    df.to_csv(cs_io, index=False)
    cs_io.seek(0)
    bot.send_document(chat_id, cs_io, visible_file_name="Mission_Result.csv", caption="📊 Mission Completed! (CSV)")
    
    bot.send_message(chat_id, "Ready for next mission?", reply_markup=main_menu())

@bot.callback_query_handler(func=lambda call: call.data == "back_home")
def home(call):
    bot.edit_message_text("🤖 **Hurupay System Menu**", call.message.chat.id, call.message.message_id, reply_markup=main_menu())

@bot.callback_query_handler(func=lambda call: call.data == "stop_now")
def stop(call):
    active_missions[call.message.chat.id] = False
    bot.answer_callback_query(call.id, "Stopping mission...")

@bot.callback_query_handler(func=lambda call: call.data.startswith('full_'))
def export_db(call):
    if not db: return
    docs = db.collection('leads').get()
    data = [d.to_dict() for d in docs]
    if not data:
        bot.answer_callback_query(call.id, "Database is empty!")
        return
    
    df = pd.DataFrame(data)
    out = BytesIO()
    if "excel" in call.data:
        with pd.ExcelWriter(out, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False)
        out.seek(0)
        bot.send_document(call.message.chat.id, out, visible_file_name="Hurupay_Full_DB.xlsx")
    else:
        df.to_csv(out, index=False)
        out.seek(0)
        bot.send_document(call.message.chat.id, out, visible_file_name="Hurupay_Full_DB.csv")

# ================= Execution =================
if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT)).start()
    threading.Thread(target=self_ping).start()
    print("💎 Hurupay Lead Bot is Operational.")
    bot.polling(none_stop=True)
