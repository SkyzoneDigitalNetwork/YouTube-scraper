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

# ================= Firebase সেটআপ =================
firebase_json_str = os.environ.get("FIREBASE_CREDENTIALS")
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
else:
    print("⚠️ Firebase Credentials Missing!")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
@app.route('/')
def keep_alive():
    return "Hurupay Lead Finder Bot is Awake 24/7!"

# ================= রিকোয়ারমেন্ট অনুযায়ী মেনু =================
COUNTRIES = {
    "ID": "🇮🇩 Indonesia (Priority)", "PH": "🇵🇭 Philippines (Priority)", 
    "BR": "🇧🇷 Brazil", "PK": "🇵🇰 Pakistan", "BD": "🇧🇩 Bangladesh", "VN": "🇻🇳 Vietnam",
    "AR": "🇦🇷 Argentina", "ES": "🇪🇸 Spain", "UA": "🇺🇦 Ukraine", 
    "RS": "🇷🇸 Serbia", "SG": "🇸🇬 Singapore"
}

CATEGORIES = [
    "Remote work freelancing", "Work-from-home jobs", "Online earning side hustles", 
    "Career tips", "Personal finance", "Tech app reviews", "Remittance receiving money"
]

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in COUNTRIES.items()]
    markup.add(*buttons)
    bot.send_message(
        message.chat.id, 
        "👋 **Hurupay Lead Finder Bot**-এ স্বাগতম!\n\nঅনুগ্রহ করে টার্গেট **দেশ (Country)** সিলেক্ট করুন:", 
        reply_markup=markup, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('country_'))
def select_category(call):
    country_code = call.data.split('_')[1]
    chat_id = str(call.message.chat.id)
    
    if firebase_json_str:
        db.collection('user_states').document(chat_id).set({'country': country_code}, merge=True)
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    buttons = [types.InlineKeyboardButton(cat, callback_data=f"cat_{cat}") for cat in CATEGORIES]
    markup.add(*buttons)
    
    bot.edit_message_text(
        f"✅ দেশ: **{COUNTRIES[country_code]}**\n\nএবার **Niche/Category** সিলেক্ট করুন:", 
        chat_id=call.message.chat.id, message_id=call.message.message_id, 
        reply_markup=markup, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def start_mission(call):
    category = call.data.split('_')[1]
    chat_id = str(call.message.chat.id)
    
    country_code = "US" # Default
    if firebase_json_str:
        user_doc = db.collection('user_states').document(chat_id).get()
        if user_doc.exists:
            country_code = user_doc.to_dict().get('country', 'US')
    
    country_name = COUNTRIES.get(country_code, "Unknown")
    
    if firebase_json_str:
        db.collection('user_states').document(chat_id).set({'category': category}, merge=True)
    
    bot.edit_message_text(
        f"🚀 **মিশন শুরু হয়েছে!**\n\n🌍 দেশ: {country_name}\n🎯 ক্যাটাগরি: {category}\n\nবট এখন রিয়েল-টাইম ভিডিও সার্চ করে উপযুক্ত ইনফ্লুয়েন্সার খুঁজছে। দয়া করে অপেক্ষা করুন...", 
        chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown"
    )
    
    threading.Thread(target=process_mission, args=(call.message.chat.id, country_code, category)).start()

# ================= লিড খোঁজার মূল লজিক =================
def process_mission(chat_id, country_code, category):
    leads = []
    
    # 1. সরাসরি চ্যানেল না খুঁজে ওই বিষয়ের ভিডিও খুঁজছি (এতে রিয়েল ও এক্টিভ ক্রিয়েটর পাবো)
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&q={category}&regionCode={country_code}&maxResults=50&key={YOUTUBE_API_KEY}"
    response = requests.get(search_url).json()
    
    if 'items' not in response:
        bot.send_message(chat_id, "❌ YouTube API Error বা কোটা শেষ হয়ে গেছে।")
        return

    # ইউনিক চ্যানেল আইডি বের করা
    channel_ids = list(set([item['snippet']['channelId'] for item in response['items']]))
    bot.send_message(chat_id, f"🔍 **{len(channel_ids)}** টি সম্ভাব্য চ্যানেল পাওয়া গেছে। এখন Llama-3 AI দিয়ে আপনার শর্ত অনুযায়ী যাচাই করা হচ্ছে...")

    for channel_id in channel_ids:
        # 2. চ্যানেলের বিস্তারিত তথ্য (Subscribers & Views)
        stats_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&id={channel_id}&key={YOUTUBE_API_KEY}"
        stats_resp = requests.get(stats_url).json()
        
        if 'items' not in stats_resp:
            continue
            
        channel_info = stats_resp['items'][0]
        channel_name = channel_info['snippet']['title']
        description = channel_info['snippet'].get('description', '')
        thumbnail = channel_info['snippet']['thumbnails']['high']['url']
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
        
        # স্ট্যাটিস্টিকস
        sub_count = int(channel_info['statistics'].get('subscriberCount', 0))
        view_count = int(channel_info['statistics'].get('viewCount', 0))
        video_count = int(channel_info['statistics'].get('videoCount', 1))
        
        # শর্ত: Channel Size (Micro, Mid-tier, Macro) - 10k এর নিচে বাদ
        if sub_count < 10000:
            continue
            
        # এনগেজমেন্ট রেট ক্যালকুলেশন
        avg_views_per_video = view_count / (video_count if video_count > 0 else 1)
        eng_rate_value = (avg_views_per_video / sub_count) * 100 if sub_count > 0 else 0
        engagement_rate = f"{eng_rate_value:.2f}%"
        
        bot.send_message(chat_id, f"⚙️ AI যাচাই করছে: **{channel_name}** ({sub_count} Subs)...", parse_mode="Markdown")

        # Llama 3 AI যাচাই
        ai_eval = evaluate_with_llama(description, channel_name, category, COUNTRIES[country_code])
        
        if ai_eval['fit'] == "YES":
            emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", description)
            contact = emails[0] if emails else "About সেকশন চেক করুন"
            
            lead_data = {
                "Creator Name": channel_name,
                "Platform & Link": channel_url,
                "Country": COUNTRIES[country_code],
                "Niche": ai_eval['niche'],
                "Subscribers": sub_count,
                "Engagement Rate": engagement_rate,
                "Contact Details": contact,
                "Estimated Rate": ai_eval['rate'],
                "Why fit for Hurupay": ai_eval['reason']
            }
            leads.append(lead_data)
            
            # ফায়ারবেসে সেভ করা
            if firebase_json_str:
                lead_data_db = lead_data.copy()
                lead_data_db["Timestamp"] = firestore.SERVER_TIMESTAMP
                db.collection('hurupay_leads').add(lead_data_db)
            
            # লাইভ স্ক্রিনশট ও মেসেজ
            caption = f"✅ **লিড কনফার্মড!**\n\n📌 **Name:** {channel_name}\n👥 **Subs:** {sub_count}\n🔥 **Eng. Rate:** {engagement_rate}\n💰 **Rate:** {ai_eval['rate']}\n💡 **Why Fit:** {ai_eval['reason']}"
            bot.send_photo(chat_id, thumbnail, caption=caption, parse_mode="Markdown")
            time.sleep(1.5)

        if len(leads) >= 30: # টার্গেট লিড সংখ্যা
            break

    # 3. কাজ শেষে ফাইল পাঠানো
    if leads:
        df = pd.DataFrame(leads)
        file_path = f"Hurupay_Leads_{country_code}.xlsx"
        df.to_excel(file_path, index=False)
        
        bot.send_message(chat_id, f"🎉 **মিশন সফল!** {len(leads)} টি 100% যোগ্য লিড পাওয়া গেছে। নিচে আপনার এক্সেল ফাইল দেওয়া হলো:")
        with open(file_path, "rb") as file:
            bot.send_document(chat_id, file)
        
        os.remove(file_path)
    else:
        bot.send_message(chat_id, "⚠️ এই মুহূর্তে এই ক্যাটাগরিতে 100k+ কোয়ালিটি সম্পন্ন কোনো নতুন চ্যানেল পাওয়া যায়নি। অন্য ক্যাটাগরি বা দেশ ট্রাই করুন।")

# ================= Groq (Llama 3) AI Prompt =================
def evaluate_with_llama(description, channel_name, category, country):
    prompt = f"""
    You are an expert Influencer Marketing Manager for 'Hurupay'. 
    Hurupay is an app for freelancers and remote workers to receive money from abroad easily.
    
    Evaluate this YouTube channel:
    Channel Name: {channel_name}
    Description: {description}
    Target Audience/Country: {country}
    Target Niche: {category}
    
    CRITICAL INSTRUCTION: The description might be in a local language (e.g., Indonesian, Tagalog, Portuguese, Bengali). You MUST mentally translate it. If the description is empty but the channel name clearly suggests earning, tech, or freelance, consider it.
    
    Respond STRICTLY in the following format with NO extra text:
    FIT: [YES or NO. Answer YES only if the channel relates to {category}, remote work, finance, tech reviews, or earning money]
    NICHE: [Identify the exact niche in 2-3 words]
    ESTIMATED_RATE: [Estimate a rate between $50 to $300 based on micro/mid-tier influencer pricing]
    REASON: [Write 1 short, professional sentence explaining why this creator's audience would use Hurupay]
    """
    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192",
            temperature=0.2,
        )
        response_text = chat_completion.choices[0].message.content
        
        fit_match = re.search(r'FIT:\s*(YES|NO)', response_text, re.IGNORECASE)
        fit = fit_match.group(1).upper() if fit_match else "NO"
        
        niche = re.search(r'NICHE:\s*(.*)', response_text)
        rate = re.search(r'ESTIMATED_RATE:\s*(.*)', response_text)
        reason = re.search(r'REASON:\s*(.*)', response_text)
        
        return {
            'fit': fit,
            'niche': niche.group(1).strip() if niche else "Unknown",
            'rate': rate.group(1).strip() if rate else "TBD",
            'reason': reason.group(1).strip() if reason else "Good fit for Hurupay"
        }
    except Exception as e:
        return {'fit': 'NO', 'niche': '', 'rate': '', 'reason': ''}

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    print("Hurupay Bot is running with advanced settings...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
