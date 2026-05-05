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
FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID") # আপনার শেয়ার্ড ফোল্ডার আইডি
PORT = int(os.environ.get("PORT", 8080))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}") 

# ================= Firebase & Google Sheets Setup =================
firebase_json_str = os.environ.get("FIREBASE_CREDENTIALS")
gc = None
db = None

if firebase_json_str:
    try:
        cred_dict = json.loads(firebase_json_str)
        
        # 1. Firebase Setup
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        
        # 2. Google Sheets & Drive Setup
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        google_creds = Credentials.from_service_account_info(cred_dict, scopes=scopes)
        gc = gspread.authorize(google_creds)
        
        print("✅ Firebase & Google Sheets API Successfully Authenticated!")
    except Exception as e:
        print(f"⚠️ API Authentication Error: {e}")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# ================= 24/7 Keep-Alive System =================
app = Flask(__name__)
@app.route('/')
def keep_alive():
    return "Hurupay Lead Finder Bot is Awake 24/7!"

def self_ping():
    while True:
        time.sleep(600) 
        try:
            requests.get(RENDER_URL)
        except:
            pass

# ================= Target Markets =================
PRIORITY_1 = {"ID": "Indonesia", "PH": "Philippines"}
PRIORITY_2 = {"BR": "Brazil", "PK": "Pakistan", "BD": "Bangladesh", "VN": "Vietnam"}
SECONDARY = {"AR": "Argentina", "ES": "Spain", "UA": "Ukraine", "RS": "Serbia", "SG": "Singapore"}

ALL_COUNTRIES = {**PRIORITY_1, **PRIORITY_2, **SECONDARY}

# ================= Categories =================
CATEGORIES = [
    "Remote Work / Freelancing",
    "Work-from-home Jobs",
    "Online Earning / Side Hustles",
    "Career Tips",
    "Personal Finance",
    "Tech/App Reviews",
    "Remittance (Money Transfer)"
]

user_session = {}
seen_channels = set()
active_missions = {} 

# ================= Menu Logic =================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    markup.add(types.InlineKeyboardButton("🥇 Priority 1 Markets 🥇", callback_data="ignore"))
    markup.add(*[types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in PRIORITY_1.items()])
    
    markup.add(types.InlineKeyboardButton("🥈 Priority 2 Markets 🥈", callback_data="ignore"))
    markup.add(*[types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in PRIORITY_2.items()])
    
    markup.add(types.InlineKeyboardButton("🥉 Secondary Markets 🥉", callback_data="ignore"))
    markup.add(*[types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in SECONDARY.items()])
    
    markup.add(types.InlineKeyboardButton("✍️ Custom Country (Type Manually)", callback_data="country_custom"))
    
    bot.send_message(
        message.chat.id, 
        "👋 **Welcome to Hurupay Lead Finder Bot!**\n\nPlease select the target **Country/Market**:", 
        reply_markup=markup, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('country_'))
def handle_country_selection(call):
    if call.data == "country_custom":
        bot.edit_message_text(
            "✍️ Please type the **Country Name** (e.g., Germany, Italy):",
            chat_id=call.message.chat.id, message_id=call.message.message_id
        )
        bot.register_next_step_handler(call.message, get_custom_country)
    elif call.data != "country_ignore":
        country_code = call.data.split('_')[1]
        user_session[call.message.chat.id] = {'country_code': country_code, 'country_name': ALL_COUNTRIES[country_code]}
        show_category_menu(call.message.chat.id, call.message.message_id)

def get_custom_country(message):
    chat_id = message.chat.id
    country_name = message.text.strip()
    user_session[chat_id] = {'country_code': 'CUSTOM', 'country_name': country_name}
    show_category_menu(chat_id)

def show_category_menu(chat_id, message_id=None):
    country_name = user_session[chat_id]['country_name']
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(*[types.InlineKeyboardButton(cat, callback_data=f"cat_{i}") for i, cat in enumerate(CATEGORIES)])
    markup.add(types.InlineKeyboardButton("✍️ Custom Category (Type Manually)", callback_data="cat_custom"))
    
    text = f"✅ Target Market selected: **{country_name}**\n\nNow, select the **Niche/Category**:"
    
    if message_id:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_category_selection(call):
    chat_id = call.message.chat.id
    
    if call.data == "cat_custom":
        bot.edit_message_text(
            "✍️ Please type the **Niche/Category** (e.g., Crypto Review):",
            chat_id=chat_id, message_id=call.message.message_id
        )
        bot.register_next_step_handler(call.message, get_custom_category)
    else:
        cat_index = int(call.data.split('_')[1])
        category = CATEGORIES[cat_index]
        start_lead_generation(chat_id, category)

def get_custom_category(message):
    start_lead_generation(message.chat.id, message.text.strip())

def start_lead_generation(chat_id, category):
    country_name = user_session[chat_id]['country_name']
    country_code = user_session[chat_id]['country_code']
    
    active_missions[chat_id] = True
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🛑 STOP MISSION", callback_data="stop_mission"))
    
    start_msg = f"🚀 **Mission Started!**\n\n🌍 Target Market: {country_name}\n🎯 Category/Niche: {category}\n\n*Searching for high-engagement channels (10k+ subs)...*\n\nClick the button below if you want to stop the process early."
    bot.send_message(chat_id, start_msg, reply_markup=markup, parse_mode="Markdown")
    
    threading.Thread(target=process_mission, args=(chat_id, country_code, country_name, category)).start()

@bot.callback_query_handler(func=lambda call: call.data == "stop_mission")
def stop_mission_callback(call):
    chat_id = call.message.chat.id
    if active_missions.get(chat_id, False):
        active_missions[chat_id] = False
        bot.edit_message_text("🛑 **Mission stopping...** Preparing the collected data into Google Sheets.", chat_id=chat_id, message_id=call.message.message_id, parse_mode="Markdown")
    else:
        bot.answer_callback_query(call.id, "No active mission to stop.")

# ================= Core Scraping Logic =================
def process_mission(chat_id, country_code, country_name, category):
    leads = []
    
    query = f"{category} in {country_name}" if country_code == 'CUSTOM' else category
    region_param = f"&regionCode={country_code}" if country_code != 'CUSTOM' else ""
    
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=video,channel&q={query}{region_param}&maxResults=50&key={YOUTUBE_API_KEY}"
    response = requests.get(search_url).json()
    
    if 'items' not in response:
        bot.send_message(chat_id, "❌ YouTube API Error or Quota Exceeded.")
        return

    channel_ids = list(set([item['snippet']['channelId'] for item in response['items']]))
    bot.send_message(chat_id, f"🔍 Initial channels found. Applying strict country & engagement filters...")

    for channel_id in channel_ids:
        if not active_missions.get(chat_id, True):
            bot.send_message(chat_id, "🛑 Mission halted by user.")
            break

        if channel_id in seen_channels:
            continue
            
        stats_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&id={channel_id}&key={YOUTUBE_API_KEY}"
        stats_resp = requests.get(stats_url).json()
        
        if 'items' not in stats_resp:
            continue
            
        channel_info = stats_resp['items'][0]
        channel_title = channel_info['snippet']['title']
        description = channel_info['snippet'].get('description', '')
        channel_url = f"https://www.youtube.com/channel/{channel_id}" 
        thumbnail_url = channel_info['snippet']['thumbnails']['high']['url']
        
        channel_country = channel_info['snippet'].get('country', '')
        if country_code != 'CUSTOM' and channel_country != country_code:
            continue
            
        sub_count = int(channel_info['statistics'].get('subscriberCount', 0))
        view_count = int(channel_info['statistics'].get('viewCount', 0))
        video_count = int(channel_info['statistics'].get('videoCount', 1))
        
        if sub_count < 10000:
            continue

        avg_views = view_count / (video_count if video_count > 0 else 1)
        eng_rate_value = (avg_views / sub_count) * 100 if sub_count > 0 else 0
        engagement_rate_str = f"{eng_rate_value:.2f}%"
        
        bot.send_message(chat_id, f"⚙️ AI Analysis: **{channel_title}** ({sub_count} Subs)...", parse_mode="Markdown")

        ai_data = extract_data_with_llama(description, channel_title, category)
        
        emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", description)
        contact = emails[0] if emails else "Check About Section"
        
        lead_data = {
            "Creator Name": channel_title,
            "Platform Link": channel_url,
            "Country": country_name,
            "Niche": ai_data['niche'],
            "Follower Count": sub_count,
            "Engagement Rate": engagement_rate_str,
            "Contact Info": contact,
            "Estimated Rate": ai_data['rate'],
            "Fit Analysis": ai_data['fit_analysis'],
            "_eng_sort_value": eng_rate_value 
        }
        leads.append(lead_data)
        seen_channels.add(channel_id)
        
        if db:
            try:
                db.collection('hurupay_leads').add({k: v for k, v in lead_data.items() if k != '_eng_sort_value'})
            except: pass
        
        caption = f"✅ **Lead Confirmed!**\n\n📌 **Name:** {channel_title}\n🌍 **Country:** {country_name}\n👥 **Subs:** {sub_count}\n🔥 **Eng. Rate:** {engagement_rate_str}\n💰 **Rate:** {ai_data['rate']}\n💡 **Fit:** {ai_data['fit_analysis']}"
        try:
            bot.send_photo(chat_id, thumbnail_url, caption=caption, parse_mode="Markdown")
        except:
            bot.send_message(chat_id, caption, parse_mode="Markdown")
            
        time.sleep(1)

        if len(leads) >= 40: 
            break

    # ================= Ranking & ONLY Google Sheet Generation =================
    active_missions[chat_id] = False 

    if leads:
        bot.send_message(chat_id, "📊 Data extraction complete. Ranking leads by Engagement Rate and generating Google Sheet...")
        
        leads.sort(key=lambda x: x['_eng_sort_value'], reverse=True)
        for lead in leads:
            del lead['_eng_sort_value']
            
        df = pd.DataFrame(leads)
        sheet_name = f"Hurupay_Leads_{country_name}_{int(time.time())}"
        
        if gc:
            try:
                # Quota সমস্যা সমাধানের জন্য নির্দিষ্ট ফোল্ডারে ফাইল তৈরি
                sh = gc.create(sheet_name, folder_id=FOLDER_ID)
                sh.share('', role='reader', type='anyone')
                worksheet = sh.get_worksheet(0)
                set_with_dataframe(worksheet, df)
                
                # ডাউনলোড বাটন সেটআপ (গুগল শিট এক্সপোর্ট ইউআরএল)
                download_url = f"https://docs.google.com/spreadsheets/d/{sh.id}/export?format=xlsx"
                
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("📥 DOWNLOAD GOOGLE SHEET (XLSX)", url=download_url))
                markup.add(types.InlineKeyboardButton("🔗 OPEN LIVE SHEET", url=sh.url))
                
                success_msg = f"🎉 **Mission Successful!**\n\nFound {len(leads)} qualified leads from '{country_name}'.\n\nনিচের বাটন থেকে আপনার গুগল শিটটি ডাউনলোড করুন অথবা সরাসরি ওপেন করুন।"
                bot.send_message(chat_id, success_msg, reply_markup=markup, parse_mode="Markdown")
                
            except Exception as e:
                bot.send_message(chat_id, f"⚠️ Google Sheet creation failed. Error: {e}")
        else:
            bot.send_message(chat_id, "⚠️ Google Sheets API is not authenticated.")

    else:
        bot.send_message(chat_id, f"⚠️ Sorry, no valid 10k+ subscriber channels found for '{country_name}'.")

# ================= Groq (Llama 3) AI Prompt =================
def extract_data_with_llama(description, channel_name, keyword):
    prompt = f"""
    You are an expert Lead Generation and Data Scraping Agent for 'Hurupay'.
    Analyze this YouTube channel strictly in ENGLISH.
    Channel Name: {channel_name}
    Description: {description}
    
    Respond STRICTLY in the following format with NO extra text:
    NICHE: [Identify the primary content category in 2-3 words]
    ESTIMATED_RATE: [Estimate a rate between $50 to $300]
    FIT_ANALYSIS: [Write 1 professional sentence explaining why this creator fits 'Hurupay' app]
    """
    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192",
            temperature=0.2,
        )
        response_text = chat_completion.choices[0].message.content
        
        niche = re.search(r'NICHE:\s*(.*)', response_text)
        rate = re.search(r'ESTIMATED_RATE:\s*(.*)', response_text)
        fit = re.search(r'FIT_ANALYSIS:\s*(.*)', response_text)
        
        return {
            'niche': niche.group(1).strip() if niche else "Related Niche",
            'rate': rate.group(1).strip() if rate else "TBD",
            'fit_analysis': fit.group(1).strip() if fit else f"Content aligns with {keyword}."
        }
    except:
        return {'niche': keyword, 'rate': 'TBD', 'fit_analysis': "Audience perfectly aligns with Hurupay."}

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    threading.Thread(target=self_ping).start()
    
    print("Bot is LIVE! 100% Google Sheets Output ONLY.")
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            time.sleep(3)
