import os
import time
import requests
import pandas as pd
import telebot
from telebot import types
from groq import Groq
import re
import json
from flask import Flask, send_file
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
        print("✅ Firebase Authenticated Successfully!")
    except Exception as e:
        print(f"⚠️ Auth Error: {e}")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# ================= Flask Server for File Downloads =================
app = Flask(__name__)

@app.route('/')
def keep_alive(): 
    return "Hurupay Lead Bot is Running!"

@app.route('/download/<file_type>')
def download_file(file_type):
    """Generates Excel or CSV from Firebase on the fly"""
    if not db:
        return "Database not connected", 500
    
    docs = db.collection('hurupay_leads').get()
    data = [doc.to_dict() for doc in docs]
    
    if not data:
        return "No leads found in database", 404
        
    df = pd.DataFrame(data).drop(columns=['channel_id'], errors='ignore')
    
    if file_type == "excel":
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Hurupay_Leads')
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name=f"Hurupay_Database_{int(time.time())}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    
    elif file_type == "csv":
        output = BytesIO()
        df.to_csv(output, index=False, encoding='utf-8')
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name=f"Hurupay_Database_{int(time.time())}.csv",
            mimetype="text/csv"
        )
    
    return "Invalid file type", 400

def self_ping():
    while True:
        time.sleep(600)
        try: requests.get(RENDER_URL)
        except: pass

# ================= Target Markets & Categories =================
PRIORITY_1 = {"ID": "Indonesia", "PH": "Philippines"}
PRIORITY_2 = {"BR": "Brazil", "PK": "Pakistan", "BD": "Bangladesh", "VN": "Vietnam"}
SECONDARY = {"AR": "Argentina", "ES": "Spain", "UA": "Ukraine", "RS": "Serbia", "SG": "Singapore"}
ALL_COUNTRIES = {**PRIORITY_1, **PRIORITY_2, **SECONDARY}

CATEGORIES = [
    "Remote Work / Freelancing", "Work-from-home Jobs", "Online Earning / Side Hustles",
    "Career Tips", "Personal Finance", "Tech/App Reviews", "Remittance (Money Transfer)"
]

user_session = {}
active_missions = {}

# ================= Main Menu & Buttons =================
def get_main_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🚀 START NEW MISSION", callback_data="start_mission_flow"))
    markup.add(types.InlineKeyboardButton("📥 DOWNLOAD FULL DB (EXCEL)", url=f"{RENDER_URL}/download/excel"))
    markup.add(types.InlineKeyboardButton("📥 DOWNLOAD FULL DB (CSV)", url=f"{RENDER_URL}/download/csv"))
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_message(
        message.chat.id, 
        "👋 **Hurupay Lead Finder HQ**\n\nUse the buttons below to start a mission or download the database.", 
        reply_markup=get_main_menu(), parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data == "start_mission_flow")
def show_countries(call):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("--- Priority 1 ---", callback_data="none"), types.InlineKeyboardButton("--- Priority 2 ---", callback_data="none"))
    
    p1_btns = [types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in PRIORITY_1.items()]
    p2_btns = [types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in PRIORITY_2.items()]
    
    markup.add(*p1_btns)
    markup.add(*p2_btns)
    markup.add(types.InlineKeyboardButton("🥉 Secondary Markets", callback_data="country_list_secondary"))
    markup.add(types.InlineKeyboardButton("✍️ Custom Country", callback_data="country_custom"))
    markup.add(types.InlineKeyboardButton("⬅️ BACK TO MENU", callback_data="back_to_main"))
    
    bot.edit_message_text("🌍 Select target **Country/Market**:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main(call):
    bot.edit_message_text("👋 **Main Menu**", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_main_menu())

@bot.callback_query_handler(func=lambda call: call.data.startswith('country_'))
def handle_country(call):
    if call.data == "country_custom":
        bot.send_message(call.message.chat.id, "✍️ Please type the **Country Name**:")
        bot.register_next_step_handler(call.message, lambda m: save_country_and_show_cat(m, 'CUSTOM'))
    elif call.data != "none":
        code = call.data.split('_')[1]
        user_session[call.message.chat.id] = {'country_code': code, 'country_name': ALL_COUNTRIES.get(code, "Unknown")}
        show_category_menu(call.message.chat.id, call.message.message_id)

def save_country_and_show_cat(message, code):
    user_session[message.chat.id] = {'country_code': code, 'country_name': message.text.strip()}
    show_category_menu(message.chat.id)

def show_category_menu(chat_id, message_id=None):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(*[types.InlineKeyboardButton(cat, callback_data=f"cat_{i}") for i, cat in enumerate(CATEGORIES)])
    markup.add(types.InlineKeyboardButton("✍️ Custom Category", callback_data="cat_custom"))
    markup.add(types.InlineKeyboardButton("⬅️ BACK", callback_data="start_mission_flow"))
    
    text = f"🎯 Target Market: **{user_session[chat_id]['country_name']}**\n\nNow, select the **Category**:"
    if message_id:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_cat(call):
    if call.data == "cat_custom":
        bot.send_message(call.message.chat.id, "✍️ Please type the **Category**:")
        bot.register_next_step_handler(call.message, lambda m: start_lead_generation(m.chat.id, m.text.strip()))
    else:
        cat = CATEGORIES[int(call.data.split('_')[1])]
        start_lead_generation(call.message.chat.id, cat)

# ================= Lead Generation Logic =================
def start_lead_generation(chat_id, category):
    country = user_session[chat_id]['country_name']
    active_missions[chat_id] = True
    
    stop_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🛑 STOP MISSION", callback_data="stop_mission"))
    bot.send_message(chat_id, f"🚀 **Mission Started!**\n\n🌍 Country: {country}\n🎯 Category: {category}\n\n*Filtering channels with 10k+ subscribers...*", reply_markup=stop_markup, parse_mode="Markdown")
    
    threading.Thread(target=process_mission, args=(chat_id, country, category)).start()

def process_mission(chat_id, country, category):
    leads_count = 0
    query = f"{category} in {country}"
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={query}&maxResults=50&key={YOUTUBE_API_KEY}"
    
    try:
        response = requests.get(search_url).json()
        items = response.get('items', [])
    except: items = []

    for item in items:
        if not active_missions.get(chat_id, True): break
        
        channel_id = item['snippet']['channelId']
        
        # Firebase Duplicate Check
        if db:
            docs = db.collection('hurupay_leads').where('channel_id', '==', channel_id).get()
            if len(docs) > 0: continue

        stats_resp = requests.get(f"https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&id={channel_id}&key={YOUTUBE_API_KEY}").json()
        
        if 'items' not in stats_resp: continue
        ch = stats_resp['items'][0]
        subs = int(ch['statistics'].get('subscriberCount', 0))
        
        if subs < 10000: continue 

        title = ch['snippet']['title']
        desc = ch['snippet'].get('description', '')
        ai = extract_ai_data(desc, title, category)
        
        lead = {
            "channel_id": channel_id,
            "Creator Name": title,
            "Link": f"https://youtube.com/channel/{channel_id}",
            "Country": country,
            "Subs": subs,
            "Rate": ai['rate'],
            "Fit": ai['fit'],
            "Contact": re.findall(r"[a-z0-9\.+-]+@[a-z0-9\.-]+\.[a-z0-9]+", desc.lower()) or ["Check About Section"]
        }
        
        if db: db.collection('hurupay_leads').add(lead) 
        leads_count += 1
        
        bot.send_message(chat_id, f"✅ **Lead Found:** {title}\n👥 Subs: {subs}\n💰 Rate: {ai['rate']}", parse_mode="Markdown")
        time.sleep(1)

    finalize_mission(chat_id, leads_count)

def finalize_mission(chat_id, count):
    active_missions[chat_id] = False
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📥 DOWNLOAD MISSION LEADS (EXCEL)", url=f"{RENDER_URL}/download/excel"),
        types.InlineKeyboardButton("📥 DOWNLOAD MISSION LEADS (CSV)", url=f"{RENDER_URL}/download/csv"),
        types.InlineKeyboardButton("🏠 BACK TO MENU", callback_data="back_to_main")
    )
    
    if count > 0:
        bot.send_message(chat_id, f"🏁 **Mission Finished!**\n\nCollected {count} new leads. Data has been saved to Firebase.\n\nYou can download the files below.", reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, "⚠️ **Mission Ended.** No new leads were found for this criteria.", reply_markup=markup, parse_mode="Markdown")

# ================= AI & Utilities =================
def extract_ai_data(desc, name, cat):
    prompt = f"Analyze YouTube channel '{name}' (Niche: {cat}). Description: {desc[:500]}. Respond ONLY in JSON format: {{\"rate\": \"estimate in $\", \"fit\": \"1 sentence why it fits Hurupay\"}}"
    try:
        resp = groq_client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model="llama3-8b-8192", temperature=0.1).choices[0].message.content
        data = json.loads(re.search(r'\{.*\}', resp, re.DOTALL).group())
        return {'rate': data.get('rate', '$50-$250'), 'fit': data.get('fit', 'Audience aligns with Hurupay services.')}
    except: return {'rate': '$100', 'fit': 'Channel content matches the target niche.'}

@bot.callback_query_handler(func=lambda call: call.data == "stop_mission")
def stop_mission(call):
    active_missions[call.message.chat.id] = False
    bot.answer_callback_query(call.id, "Mission stopping...")

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT)).start()
    threading.Thread(target=self_ping).start()
    print("🚀 Hurupay Bot is LIVE! Serving Excel & CSV files.")
    bot.polling(none_stop=True)
