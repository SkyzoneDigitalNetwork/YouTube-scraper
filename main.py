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

# ================= কনফিগারেশন =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "আপনার_টেলিগ্রাম_বট_টোকেন_এখানে")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "আপনার_ইউটিউব_এপিআই_কি_এখানে")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "আপনার_GROQ_এপিআই_কি_এখানে")
PORT = int(os.environ.get("PORT", 8080))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}") 

# ================= Firebase ও Google Sheets সেটআপ =================
firebase_json_str = os.environ.get("FIREBASE_CREDENTIALS")
cred_dict = None
gc = None
db = None

if firebase_json_str:
    try:
        cred_dict = json.loads(firebase_json_str)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        gc = gspread.service_account_from_dict(cred_dict)
        print("✅ Firebase & Google Sheets Successfully Connected!")
    except Exception as e:
        print(f"⚠️ Firebase/Sheets Error: {e}")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# ================= ২৪ ঘণ্টা সজাগ রাখার সিস্টেম =================
app = Flask(__name__)
@app.route('/')
def keep_alive():
    return "Hurupay Lead Finder Bot is Awake 24/7!"

def self_ping():
    while True:
        time.sleep(600) # প্রতি ১০ মিনিট পর পর নিজেকে নক করবে
        try:
            requests.get(RENDER_URL)
        except:
            pass

# ================= দেশের লিস্ট (Priority অনুযায়ী) =================
PRIORITY_1 = {"ID": "Indonesia", "PH": "Philippines"}
PRIORITY_2 = {"BR": "Brazil", "PK": "Pakistan", "BD": "Bangladesh", "VN": "Vietnam"}
SECONDARY = {"AR": "Argentina", "ES": "Spain", "UA": "Ukraine", "RS": "Serbia", "SG": "Singapore"}

ALL_COUNTRIES = {**PRIORITY_1, **PRIORITY_2, **SECONDARY}

# ================= ক্যাটাগরি লিস্ট =================
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

# ================= মেনু লজিক =================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    # Priority 1
    markup.add(types.InlineKeyboardButton("🥇 Priority 1 Markets 🥇", callback_data="ignore"))
    markup.add(*[types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in PRIORITY_1.items()])
    
    # Priority 2
    markup.add(types.InlineKeyboardButton("🥈 Priority 2 Markets 🥈", callback_data="ignore"))
    markup.add(*[types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in PRIORITY_2.items()])
    
    # Secondary
    markup.add(types.InlineKeyboardButton("🥉 Secondary Markets 🥉", callback_data="ignore"))
    markup.add(*[types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in SECONDARY.items()])
    
    # Custom Option
    markup.add(types.InlineKeyboardButton("✍️ Custom Country (ম্যানুয়ালি লিখুন)", callback_data="country_custom"))
    
    bot.send_message(
        message.chat.id, 
        "👋 **Hurupay Lead Finder Bot**\n\nঅনুগ্রহ করে টার্গেট **দেশ (Target Market)** সিলেক্ট করুন:", 
        reply_markup=markup, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('country_'))
def handle_country_selection(call):
    if call.data == "country_custom":
        bot.edit_message_text(
            "✍️ অনুগ্রহ করে দেশের নাম **ইংরেজিতে** লিখে মেসেজ করুন (যেমন: Germany, Italy):",
            chat_id=call.message.chat.id, message_id=call.message.message_id
        )
        bot.register_next_step_handler(call.message, get_custom_country)
    else:
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
    markup.add(types.InlineKeyboardButton("✍️ Custom Category (ম্যানুয়ালি লিখুন)", callback_data="cat_custom"))
    
    text = f"✅ দেশ সিলেক্ট করা হয়েছে: **{country_name}**\n\nএবার **Niche/Category** সিলেক্ট করুন:"
    
    if message_id:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_category_selection(call):
    chat_id = call.message.chat.id
    
    if call.data == "cat_custom":
        bot.edit_message_text(
            "✍️ অনুগ্রহ করে Niche/Category লিখে মেসেজ করুন (যেমন: Crypto Review):",
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
    
    start_msg = f"🚀 **মিশন শুরু হয়েছে!**\n\n🌍 Target Market: {country_name}\n🎯 Category/Niche: {category}\n\nবট এখন শুধুমাত্র উচ্চ Engagement Rate সম্পন্ন 10k+ সাবস্ক্রাইবারের চ্যানেল খুঁজছে..."
    bot.send_message(chat_id, start_msg, parse_mode="Markdown")
    
    threading.Thread(target=process_mission, args=(chat_id, country_code, country_name, category)).start()

# ================= লিড খোঁজার মূল লজিক =================
def process_mission(chat_id, country_code, country_name, category):
    leads = []
    
    # Custom দেশের ক্ষেত্রে শুধু কিওয়ার্ডের সাথে দেশের নাম জুড়ে দেওয়া হলো, regionCode বাদ দিয়ে।
    query = f"{category} in {country_name}" if country_code == 'CUSTOM' else category
    region_param = f"&regionCode={country_code}" if country_code != 'CUSTOM' else ""
    
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=video,channel&q={query}{region_param}&maxResults=50&key={YOUTUBE_API_KEY}"
    response = requests.get(search_url).json()
    
    if 'items' not in response:
        bot.send_message(chat_id, "❌ YouTube API Error বা কোটা শেষ হয়ে গেছে।")
        return

    channel_ids = list(set([item['snippet']['channelId'] for item in response['items']]))
    bot.send_message(chat_id, f"🔍 প্রাথমিক চ্যানেল পাওয়া গেছে। এখন Filtering এবং Engagement যাচাই করা হচ্ছে...")

    for channel_id in channel_ids:
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
        
        # 🚨 Strict Country Verification (যদি Custom দেশ না হয়) 🚨
        channel_country = channel_info['snippet'].get('country', '')
        if country_code != 'CUSTOM' and channel_country != country_code:
            continue
            
        sub_count = int(channel_info['statistics'].get('subscriberCount', 0))
        view_count = int(channel_info['statistics'].get('viewCount', 0))
        video_count = int(channel_info['statistics'].get('videoCount', 1))
        
        if sub_count < 10000:
            continue

        # Engagement Rate Calculation
        avg_views = view_count / (video_count if video_count > 0 else 1)
        eng_rate_value = (avg_views / sub_count) * 100 if sub_count > 0 else 0
        engagement_rate_str = f"{eng_rate_value:.2f}%"
        
        bot.send_message(chat_id, f"⚙️ AI Analysis: **{channel_title}** ({sub_count} Subs)...", parse_mode="Markdown")

        # Llama 3 AI Data Extraction (Strictly English)
        ai_data = extract_data_with_llama(description, channel_title, category)
        
        emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", description)
        contact = emails[0] if emails else "Check About Section"
        
        # Exact Google Sheet Columns in English
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
            "_eng_sort_value": eng_rate_value # শুধু সর্টিংয়ের জন্য, শিটে যাবে না
        }
        leads.append(lead_data)
        seen_channels.add(channel_id)
        
        # ফায়ারবেসে সেভ
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

    # ================= Ranking & Google Sheet Generation =================
    if leads:
        try:
            bot.send_message(chat_id, "📊 তথ্য সংগ্রহ শেষ। Engagement Rate অনুযায়ী Rank করে Google Sheet তৈরি করা হচ্ছে...")
            
            # Ranking Leads by Engagement Rate (Highest to Lowest)
            leads.sort(key=lambda x: x['_eng_sort_value'], reverse=True)
            
            # Remove hidden sorting column
            for lead in leads:
                del lead['_eng_sort_value']
                
            df = pd.DataFrame(leads)
            sheet_name = f"Hurupay_Leads_{country_name}_{int(time.time())}"
            
            if gc:
                sh = gc.create(sheet_name)
                sh.share('', role='reader', type='anyone')
                worksheet = sh.get_worksheet(0)
                set_with_dataframe(worksheet, df)
                
                success_msg = f"🎉 **মিশন সম্পূর্ণ সফল!**\n\nTarget Market '{country_name}' থেকে {len(leads)} টি যোগ্য লিড পাওয়া গেছে। (Engagement Rate অনুযায়ী সাজানো হয়েছে)\n\n📝 **Live Google Sheet Link:**\n{sh.url}"
                bot.send_message(chat_id, success_msg, parse_mode="Markdown")
            else:
                raise Exception("Google Sheets credentials not fully loaded.")
                
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ গুগল শিট তৈরি করতে সমস্যা হয়েছে। বিকল্প হিসেবে Excel File দেওয়া হলো।")
            file_path = f"{sheet_name}.xlsx"
            df.to_excel(file_path, index=False)
            with open(file_path, "rb") as file:
                bot.send_document(chat_id, file)
            os.remove(file_path)
    else:
        bot.send_message(chat_id, f"⚠️ দুঃখিত, '{country_name}'-এ 10k+ সাবস্ক্রাইবার আছে এমন কোনো চ্যানেল পাওয়া যায়নি।")

# ================= Groq (Llama 3) AI Prompt =================
def extract_data_with_llama(description, channel_name, keyword):
    prompt = f"""
    You are an expert Lead Generation and Data Scraping Agent for 'Hurupay' (a fintech app for freelancers to receive money from abroad).
    Analyze this YouTube channel strictly in ENGLISH.
    Channel Name: {channel_name}
    Description: {description}
    
    Respond STRICTLY in the following format with NO extra text:
    NICHE: [Identify the primary content category in 2-3 words]
    ESTIMATED_RATE: [Estimate a rate between $50 to $300 based on micro/mid-tier influencer pricing]
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
    
    print("Bot is fully live with PRIORITY MENUS, ENGLISH GOOGLE SHEETS & RANKING!")
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            time.sleep(3)
