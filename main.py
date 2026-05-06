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
        print("✅ Firebase Connected")
    except Exception as e:
        print(f"⚠️ Firebase Error: {e}")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# ================= 24/7 Keep-Alive =================
app = Flask(__name__)
@app.route('/')
def live(): return "System Online"

def self_ping():
    while True:
        time.sleep(300)
        try: requests.get(RENDER_URL)
        except: pass

# ================= Location Mapping (Lat, Lon, Region) =================
# Added coordinates to force YouTube to search in specific geographic areas
MARKETS = {
    "Indonesia": {"code": "ID", "lat_lon": "-0.7893,113.9213"},
    "Philippines": {"code": "PH", "lat_lon": "12.8797,121.7740"},
    "Brazil": {"code": "BR", "lat_lon": "-14.2350,-51.9253"},
    "Pakistan": {"code": "PK", "lat_lon": "30.3753,69.3451"},
    "Bangladesh": {"code": "BD", "lat_lon": "23.6850,90.3563"},
    "Vietnam": {"code": "VN", "lat_lon": "14.0583,108.2772"},
    "Argentina": {"code": "AR", "lat_lon": "-38.4161,-63.6167"},
    "Spain": {"code": "ES", "lat_lon": "40.4637,-3.7492"},
    "Ukraine": {"code": "UA", "lat_lon": "48.3794,31.1656"},
    "Serbia": {"code": "RS", "lat_lon": "44.0165,21.0059"},
    "Singapore": {"code": "SG", "lat_lon": "1.3521,103.8198"}
}

CATEGORIES = [
    "Remote work / Freelancing", "Work-from-home jobs", "Online earning / Side hustles",
    "Career tips", "Personal finance", "Tech/App reviews", "Remittance / Sending money abroad"
]

user_session = {}
active_missions = {}

# ================= UI =================
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
    bot.send_message(message.chat.id, "⚡ **Hurupay Lead System Awakened!**", reply_markup=main_menu(), parse_mode="Markdown")

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

# ================= Scraper =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('setcat_'))
def run_mission(call):
    idx = int(call.data.split('_')[1])
    niche = CATEGORIES[idx]
    chat_id = call.message.chat.id
    country = user_session[chat_id]['country']
    
    active_missions[chat_id] = True
    stop_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🛑 STOP MISSION", callback_data="stop_now"))
    bot.send_message(chat_id, f"🚀 **Mission Started!**\n📍 Location: {country}\n📂 Niche: {niche}", reply_markup=stop_markup, parse_mode="Markdown")
    
    threading.Thread(target=process_leads, args=(chat_id, country, niche)).start()

def process_leads(chat_id, country, niche):
    m_data = MARKETS.get(country)
    # Using regionCode AND location/locationRadius to pin the search to the specific country
    query = f"{niche} {country}"
    url = (f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={query}"
           f"&maxResults=50&regionCode={m_data['code']}&location={m_data['lat_lon']}"
           f"&locationRadius=500km&key={YOUTUBE_API_KEY}")
    
    mission_leads = []
    try:
        data = requests.get(url).json()
        for item in data.get('items', []):
            if not active_missions.get(chat_id): break
            
            chan_id = item['snippet']['channelId']
            if db and db.collection('leads').document(chan_id).get().exists: continue

            c_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&id={chan_id}&key={YOUTUBE_API_KEY}"
            c_res = requests.get(c_url).json()
            if not c_res.get('items'): continue
            
            c_data = c_res['items'][0]
            subs = int(c_data['statistics'].get('subscriberCount', 0))
            if subs < 10000: continue 

            desc = c_data['snippet'].get('description', '')
            title = c_data['snippet']['title']
            
            contacts = re.findall(r"[a-z0-9\.+-]+@[a-z0-9\.-]+\.[a-z0-9]+|wa\.me/\d+|\+\d{10,15}", desc.lower())
            all_contact = ", ".join(list(set(contacts))) or "No direct contact in bio"

            fit_note = analyze_with_ai(title, desc, niche)

            lead = {
                "Creator Name": title,
                "Link": f"https://youtube.com/channel/{chan_id}",
                "Country": country,
                "Niche": niche,
                "Subscribers": subs,
                "Contact": all_contact,
                "Fit Note": fit_note
            }
            
            if db: db.collection('leads').document(chan_id).set(lead)
            mission_leads.append(lead)
            bot.send_message(chat_id, f"✅ **Lead Found:** {title}\n👥 Subs: {subs}")
            time.sleep(1)

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Error: {str(e)}")

    finalize_files(chat_id, mission_leads)

def analyze_with_ai(name, desc, niche):
    prompt = f"Creator: {name}. Niche: {niche}. Bio: {desc[:300]}. Why is this creator good for a USD remittance app? (1 short sentence)"
    try:
        resp = groq_client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model="llama3-8b-8192").choices[0].message.content
        return resp.strip()
    except: return "Targets audience interested in finance/earning."

# ================= Finalize & Deliver =================
def finalize_files(chat_id, leads):
    active_missions[chat_id] = False
    if not leads:
        bot.send_message(chat_id, "🏁 Mission ended. No new leads found.", reply_markup=main_menu())
        return

    df = pd.DataFrame(leads)
    
    # Generate Excel
    ex_io = BytesIO()
    with pd.ExcelWriter(ex_io, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False)
    ex_io.seek(0)
    
    # Generate CSV
    cs_io = BytesIO()
    df.to_csv(cs_io, index=False)
    cs_io.seek(0)

    # Sending files immediately
    bot.send_document(chat_id, ex_io, visible_file_name="Mission_Result.xlsx", caption="🎯 Mission Finished! (Excel)")
    bot.send_document(chat_id, cs_io, visible_file_name="Mission_Result.csv", caption="📊 Mission Finished! (CSV)")
    
    bot.send_message(chat_id, "System ready for next mission.", reply_markup=main_menu())

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
        bot.answer_callback_query(call.id, "Database empty!")
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

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT)).start()
    threading.Thread(target=self_ping).start()
    bot.polling(none_stop=True)
