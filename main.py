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
import gspread
from gspread_dataframe import set_with_dataframe
from google.oauth2.service_account import Credentials

# ================= Configuration =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "আপনার_টেলিগ্রাম_বট_টোকেন_এখানে")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "আপনার_ইউটিউব_এপিআই_কি_এখানে")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "আপনার_GROQ_এপিআই_কি_এখানে")
MY_PERSONAL_EMAIL = os.environ.get("MY_PERSONAL_EMAIL", "আপনার_ইমেইল@gmail.com") # আপনার ইমেইল এখানে দিন
PORT = int(os.environ.get("PORT", 8080))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}") 

# ================= Firebase & Google Sheets Setup =================
firebase_json_str = os.environ.get("FIREBASE_CREDENTIALS")
gc = None
db = None

if firebase_json_str:
    try:
        cred_dict = json.loads(firebase_json_str)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        
        scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        google_creds = Credentials.from_service_account_info(cred_dict, scopes=scopes)
        gc = gspread.authorize(google_creds)
        print("✅ APIs Authenticated Successfully!")
    except Exception as e:
        print(f"⚠️ Auth Error: {e}")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# ================= 24/7 Keep-Alive =================
app = Flask(__name__)
@app.route('/')
def keep_alive(): return "Hurupay Lead Bot is Running!"

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
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("🚀 START NEW MISSION", callback_data="start_mission_flow"))
    markup.add(types.InlineKeyboardButton("📥 DOWNLOAD ALL LEADS (Google Sheet)", callback_data="download_all_leads"))
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_message(
        message.chat.id, 
        "👋 **Hurupay Lead Finder HQ**\nনিচের বাটন ব্যবহার করে কাজ শুরু করুন বা ডাটা ডাউনলোড করুন।", 
        reply_markup=get_main_menu(), parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data == "start_mission_flow")
def show_countries(call):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("🥇 Priority 1", callback_data="none"), types.InlineKeyboardButton("🥈 Priority 2", callback_data="none"))
    
    p1_btns = [types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in PRIORITY_1.items()]
    p2_btns = [types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in PRIORITY_2.items()]
    
    markup.add(*p1_btns)
    markup.add(*p2_btns)
    markup.add(types.InlineKeyboardButton("🥉 Secondary Markets", callback_data="country_list_secondary"))
    markup.add(types.InlineKeyboardButton("✍️ Custom Country", callback_data="country_custom"))
    markup.add(types.InlineKeyboardButton("⬅️ BACK", callback_data="back_to_main"))
    
    bot.edit_message_text("🌍 Select target **Country/Market**:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main(call):
    bot.edit_message_text("👋 **Main Menu**", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_main_menu())

@bot.callback_query_handler(func=lambda call: call.data.startswith('country_'))
def handle_country(call):
    if call.data == "country_custom":
        bot.send_message(call.message.chat.id, "✍️ Type the **Country Name**:")
        bot.register_next_step_handler(call.message, lambda m: save_country_and_show_cat(m, 'CUSTOM'))
    else:
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
    
    text = f"🎯 Target: **{user_session[chat_id]['country_name']}**\nSelect **Category**:"
    if message_id:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_cat(call):
    if call.data == "cat_custom":
        bot.send_message(call.message.chat.id, "✍️ Type the **Niche/Category**:")
        bot.register_next_step_handler(call.message, lambda m: start_lead_generation(m.chat.id, m.text.strip()))
    else:
        cat = CATEGORIES[int(call.data.split('_')[1])]
        start_lead_generation(call.message.chat.id, cat)

# ================= Lead Generation Logic =================
def start_lead_generation(chat_id, category):
    country = user_session[chat_id]['country_name']
    active_missions[chat_id] = True
    
    stop_markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🛑 STOP MISSION", callback_data="stop_mission"))
    bot.send_message(chat_id, f"🚀 **Mission Started!**\n🌍 {country} | 🎯 {category}", reply_markup=stop_markup, parse_mode="Markdown")
    
    threading.Thread(target=process_mission, args=(chat_id, country, category)).start()

def process_mission(chat_id, country, category):
    leads = []
    query = f"{category} in {country}"
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={query}&maxResults=50&key={YOUTUBE_API_KEY}"
    
    try:
        response = requests.get(search_url).json()
        items = response.get('items', [])
    except: items = []

    for item in items:
        if not active_missions.get(chat_id, True): break
        
        channel_id = item['snippet']['channelId']
        stats_resp = requests.get(f"https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&id={channel_id}&key={YOUTUBE_API_KEY}").json()
        
        if 'items' not in stats_resp: continue
        ch = stats_resp['items'][0]
        subs = int(ch['statistics'].get('subscriberCount', 0))
        
        if subs < 10000: continue # Strict 10k filter

        # Firebase Duplicate Check
        if db:
            docs = db.collection('hurupay_leads').where('channel_id', '==', channel_id).get()
            if len(docs) > 0: continue

        title = ch['snippet']['title']
        desc = ch['snippet'].get('description', '')
        ai = extract_ai_data(desc, title, category)
        
        lead = {
            "channel_id": channel_id,
            "Creator Name": title,
            "Link": f"https://youtube.com/channel/{channel_id}",
            "Subs": subs,
            "Rate": ai['rate'],
            "Fit": ai['fit'],
            "Contact": re.findall(r"[a-z0-9\.+-]+@[a-z0-9\.-]+\.[a-z0-9]+", desc.lower()) or ["Check About"]
        }
        
        leads.append(lead)
        if db: db.collection('hurupay_leads').add(lead) # Save to Firebase
        
        bot.send_message(chat_id, f"✅ **Lead Found:** {title}\n👥 Subs: {subs}\n💰 Rate: {ai['rate']}", parse_mode="Markdown")
        time.sleep(1)

    finalize_mission(chat_id, leads, country)

def finalize_mission(chat_id, leads, country):
    active_missions[chat_id] = False
    if not leads:
        bot.send_message(chat_id, "⚠️ No new leads found.")
        return

    df = pd.DataFrame(leads).drop(columns=['channel_id'])
    sheet_name = f"Hurupay_Leads_{country}_{int(time.time())}"
    
    if gc:
        try:
            sh = gc.create(sheet_name)
            sh.share(MY_PERSONAL_EMAIL, perm_type='user', role='writer')
            sh.share('', role='reader', type='anyone')
            set_with_dataframe(sh.get_worksheet(0), df)
            
            download_url = f"https://docs.google.com/spreadsheets/d/{sh.id}/export?format=xlsx"
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("📥 DOWNLOAD GOOGLE SHEET (XLSX)", url=download_url))
            markup.add(types.InlineKeyboardButton("🔗 OPEN LIVE SHEET", url=sh.url))
            
            bot.send_message(chat_id, f"🎉 **Mission Success!** {len(leads)} leads collected.\nফাইলটি আপনার ইমেইলে শেয়ার করা হয়েছে।", reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Sheet Error: {e}")

# ================= Download All Logic =================
@bot.callback_query_handler(func=lambda call: call.data == "download_all_leads")
def download_all_leads(call):
    bot.answer_callback_query(call.id, "Generating full report...")
    if not db:
        bot.send_message(call.message.chat.id, "❌ Database not connected.")
        return
        
    all_docs = db.collection('hurupay_leads').get()
    data = [doc.to_dict() for doc in all_docs]
    
    if not data:
        bot.send_message(call.message.chat.id, "📭 No leads in database.")
        return

    df = pd.DataFrame(data).drop(columns=['channel_id'], errors='ignore')
    sheet_name = f"Hurupay_FULL_Database_{int(time.time())}"
    
    try:
        sh = gc.create(sheet_name)
        sh.share(MY_PERSONAL_EMAIL, perm_type='user', role='writer')
        sh.share('', role='reader', type='anyone')
        set_with_dataframe(sh.get_worksheet(0), df)
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("📥 DOWNLOAD FULL GOOGLE SHEET", url=f"https://docs.google.com/spreadsheets/d/{sh.id}/export?format=xlsx"))
        bot.send_message(call.message.chat.id, "📊 **Full Database Report Generated:**", reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Download failed: {e}")

# ================= AI & Utilities =================
def extract_ai_data(desc, name, cat):
    prompt = f"Analyze YouTube channel '{name}' (Niche: {cat}). Description: {desc[:500]}. Respond ONLY in JSON format: {{\"rate\": \"estimate in $\", \"fit\": \"1 sentence why it fits Hurupay\"}}"
    try:
        resp = groq_client.chat.completions.create(messages=[{"role": "user", "content": prompt}], model="llama3-8b-8192", temperature=0.1).choices[0].message.content
        data = json.loads(re.search(r'\{.*\}', resp, re.DOTALL).group())
        return {'rate': data.get('rate', '$100-$300'), 'fit': data.get('fit', 'Relevant audience.')}
    except: return {'rate': '$150', 'fit': 'Aligned content.'}

@bot.callback_query_handler(func=lambda call: call.data == "stop_mission")
def stop_mission(call):
    active_missions[call.message.chat.id] = False
    bot.answer_callback_query(call.id, "Stopping...")

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT)).start()
    threading.Thread(target=self_ping).start()
    print("🚀 Bot is LIVE with Google Sheets & Firebase Protection!")
    bot.polling(none_stop=True)
