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

# ================= কনফিগারেশন =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "আপনার_টেলিগ্রাম_বট_টোকেন_এখানে")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "আপনার_ইউটিউব_এপিআই_কি_এখানে")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "আপনার_GROQ_এপিআই_কি_এখানে")
PORT = int(os.environ.get("PORT", 8080))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}") # Render URL

# ================= Firebase সেটআপ =================
firebase_json_str = os.environ.get("FIREBASE_CREDENTIALS")
db = None
if firebase_json_str:
    try:
        cred_dict = json.loads(firebase_json_str)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Firebase Successfully Connected!")
    except Exception as e:
        print(f"⚠️ Firebase Error: {e}")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# ================= ২৪ ঘণ্টা সজাগ রাখার সিস্টেম =================
app = Flask(__name__)
@app.route('/')
def keep_alive():
    return "Hurupay Lead Finder Bot is Awake 24/7!"

def self_ping():
    # এই ফাংশনটি বটকে নিজে থেকেই সজাগ রাখবে, কোনো রোবট লাগবে না।
    while True:
        time.sleep(600) # প্রতি ১০ মিনিট পর পর
        try:
            requests.get(RENDER_URL)
        except:
            pass

# ================= মেনু ও লিস্ট =================
COUNTRIES = {
    "ID": "Indonesia", "PH": "Philippines", "BR": "Brazil", 
    "PK": "Pakistan", "BD": "Bangladesh", "VN": "Vietnam",
    "AR": "Argentina", "ES": "Spain", "UA": "Ukraine", 
    "RS": "Serbia", "SG": "Singapore"
}

CATEGORIES = [
    "Remote work / freelancing",
    "Work-from-home jobs",
    "Online earning / side hustles",
    "Career tips",
    "Personal finance",
    "Tech/app reviews",
    "Remittance or receiving money"
]

user_session = {}
seen_channels = set() # ডুপ্লিকেট চ্যানেল ঠেকানোর জন্য

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in COUNTRIES.items()]
    markup.add(*buttons)
    bot.send_message(
        message.chat.id, 
        "👋 **Hurupay Lead Finder Bot**\n\nঅনুগ্রহ করে টার্গেট **দেশ (Country)** সিলেক্ট করুন:", 
        reply_markup=markup, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('country_'))
def select_category(call):
    country_code = call.data.split('_')[1]
    chat_id = call.message.chat.id
    user_session[chat_id] = {'country': country_code}
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    buttons = [types.InlineKeyboardButton(cat, callback_data=f"cat_{cat}") for cat in CATEGORIES]
    markup.add(*buttons)
    
    bot.edit_message_text(
        f"✅ দেশ সিলেক্ট করা হয়েছে: **{COUNTRIES[country_code]}**\n\nএবার **ক্যাটাগরি (Niche)** সিলেক্ট করুন:", 
        chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def start_mission(call):
    category = call.data.split('_')[1]
    chat_id = call.message.chat.id
    
    country_code = user_session.get(chat_id, {}).get('country', 'US')
    country_name = COUNTRIES.get(country_code, "Unknown")
    
    # কাজ শুরুর স্ক্রিনশট (প্রতীকী) ও মেসেজ
    start_msg = f"🚀 **মিশন শুরু হয়েছে!**\n\n🌍 দেশ: {country_name}\n🎯 ক্যাটাগরি: {category}\n\nবট এখন শুধুমাত্র **{country_name}**-এর 10k+ সাবস্ক্রাইবারের রিয়েল চ্যানেল খুঁজছে..."
    bot.send_message(chat_id, start_msg, parse_mode="Markdown")
    
    # ব্যাকগ্রাউন্ডে মিশন শুরু
    threading.Thread(target=process_mission, args=(chat_id, country_code, category)).start()

# ================= লিড খোঁজার মূল লজিক =================
def process_mission(chat_id, country_code, category):
    leads = []
    
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=video,channel&q={category}&regionCode={country_code}&maxResults=50&key={YOUTUBE_API_KEY}"
    response = requests.get(search_url).json()
    
    if 'items' not in response:
        bot.send_message(chat_id, "❌ YouTube API Error বা কোটা শেষ।")
        return

    channel_ids = list(set([item['snippet']['channelId'] for item in response['items']]))
    bot.send_message(chat_id, f"🔍 প্রাথমিক চ্যানেল পাওয়া গেছে। এখন 100% দেশ এবং সাবস্ক্রাইবার ফিল্টার করা হচ্ছে...")

    for channel_id in channel_ids:
        if channel_id in seen_channels:
            continue
            
        stats_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&id={channel_id}&key={YOUTUBE_API_KEY}"
        stats_resp = requests.get(stats_url).json()
        
        if 'items' not in stats_resp:
            continue
            
        channel_info = stats_resp['items'][0]
        channel_name = channel_info['snippet']['title']
        description = channel_info['snippet'].get('description', '')
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
        thumbnail_url = channel_info['snippet']['thumbnails']['high']['url']
        
        # 🚨 কঠোর দেশ যাচাই 🚨
        channel_country = channel_info['snippet'].get('country', '')
        if channel_country != country_code:
            continue # অন্য দেশ হলে সাথে সাথে বাদ
            
        sub_count = int(channel_info['statistics'].get('subscriberCount', 0))
        view_count = int(channel_info['statistics'].get('viewCount', 0))
        video_count = int(channel_info['statistics'].get('videoCount', 1))
        
        # শর্ত: 10k থেকে কম হলে বাদ
        if sub_count < 10000:
            continue

        bot.send_message(chat_id, f"⚙️ AI যাচাই করছে: **{channel_name}**...")

        # এনগেজমেন্ট রেট
        avg_views_per_video = view_count / (video_count if video_count > 0 else 1)
        eng_rate_value = (avg_views_per_video / sub_count) * 100 if sub_count > 0 else 0
        engagement_rate = f"{eng_rate_value:.2f}%"
        
        # AI Data Extraction
        ai_data = extract_data_with_llama(description, channel_name, category)
        
        emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", description)
        contact = emails[0] if emails else "About সেকশন চেক করুন"
        
        lead_data = {
            "Creator Name": channel_name,
            "Platform & Link": channel_url,
            "Country": COUNTRIES[country_code],
            "Niche": ai_data['niche'],
            "Subscribers": sub_count,
            "Engagement Rate": engagement_rate,
            "Contact Details": contact,
            "Estimated Rate": ai_data['rate'],
            "Why fit for Hurupay": ai_data['reason']
        }
        leads.append(lead_data)
        seen_channels.add(channel_id)
        
        # Firebase Save
        if db:
            try:
                lead_data_db = lead_data.copy()
                lead_data_db["Timestamp"] = firestore.SERVER_TIMESTAMP
                db.collection('hurupay_leads').add(lead_data_db)
            except:
                pass
        
        # লিড পাওয়ার পর লাইভ স্ক্রিনশট (Channel Thumbnail)
        caption = f"✅ **লিড কনফার্মড!**\n\n📌 **Name:** {channel_name}\n🌍 **Country:** {COUNTRIES[country_code]}\n👥 **Subs:** {sub_count}\n🔥 **Eng. Rate:** {engagement_rate}\n💰 **Rate:** {ai_data['rate']}\n💡 **Reason:** {ai_data['reason']}"
        try:
            bot.send_photo(chat_id, thumbnail_url, caption=caption, parse_mode="Markdown")
        except:
            bot.send_message(chat_id, caption, parse_mode="Markdown")
            
        time.sleep(1)

        if len(leads) >= 40: 
            break

    # ================= ফাইল তৈরি ও পাঠানো =================
    if leads:
        df = pd.DataFrame(leads)
        file_path = f"Hurupay_Leads_{country_code}_{category.replace(' ', '_')}.xlsx"
        df.to_excel(file_path, index=False)
        
        success_msg = f"🎉 **মিশন সম্পূর্ণ সফল!**\n\nআপনার সিলেক্ট করা দেশ থেকে {len(leads)} টি যোগ্য লিড পাওয়া গেছে। নিচে ডাউনলোড বাটন (ফাইল) দেওয়া হলো:"
        bot.send_message(chat_id, success_msg, parse_mode="Markdown")
        
        with open(file_path, "rb") as file:
            bot.send_document(chat_id, file)
        
        os.remove(file_path) # সার্ভার থেকে মুছে ফেলা
    else:
        bot.send_message(chat_id, f"⚠️ দুঃখিত, '{COUNTRIES[country_code]}' দেশে 10k+ সাবস্ক্রাইবার আছে এমন কোনো চ্যানেল এই মুহূর্তে পাওয়া যায়নি। অন্য ক্যাটাগরি চেষ্টা করুন।")

# ================= Groq (Llama 3) AI =================
def extract_data_with_llama(description, channel_name, category):
    prompt = f"""
    You work for 'Hurupay' (an app for freelancers to receive money from abroad).
    Extract info from this YouTube channel.
    Name: {channel_name}
    Description: {description}
    
    Respond STRICTLY in this format with NO extra text:
    NICHE: [Channel niche in 2-3 words]
    ESTIMATED_RATE: [Estimate rate $50-$300]
    REASON: [1 professional sentence why their audience needs Hurupay]
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
        reason = re.search(r'REASON:\s*(.*)', response_text)
        return {
            'niche': niche.group(1).strip() if niche else category,
            'rate': rate.group(1).strip() if rate else "TBD",
            'reason': reason.group(1).strip() if reason else "Matches Hurupay target audience."
        }
    except:
        return {'niche': category, 'rate': 'TBD', 'reason': "Audience aligns with Hurupay."}

def run_flask():
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    # Flask Server Thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Self-Pinging Thread (২৪ ঘন্টা সজাগ রাখার জাদুকরী ট্রিক)
    ping_thread = threading.Thread(target=self_ping)
    ping_thread.start()
    
    print("Bot is LIVE! 24/7 Active with Strict Country Filters.")
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            time.sleep(3)
