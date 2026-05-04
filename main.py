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
# Render.com এ FIREBASE_CREDENTIALS নামে একটি Environment Variable খুলবেন
# এবং সেখানে ফায়ারবেসের JSON ফাইলের ভেতরের সব লেখা কপি করে পেস্ট করে দেবেন।
firebase_json_str = os.environ.get("FIREBASE_CREDENTIALS")

if firebase_json_str:
    cred_dict = json.loads(firebase_json_str)
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase Successfully Connected!")
else:
    print("⚠️ Firebase Credentials Missing!")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

# ফ্লাস্ক সার্ভার (Render.com এ ২৪ ঘন্টা সজাগ রাখার জন্য)
app = Flask(__name__)
@app.route('/')
def keep_alive():
    return "Hurupay Lead Finder Bot is Awake 24/7!"

# ================= মেনু এবং অপশন =================
COUNTRIES = {
    "ID": "Indonesia", "BR": "Brazil", "PK": "Pakistan", 
    "BD": "Bangladesh", "PH": "Philippines", "VN": "Vietnam"
}
CATEGORIES = [
    "Remote work", "Freelancing", "Online earning", 
    "Personal finance", "Tech app review", "Remittance"
]

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(name, callback_data=f"country_{code}") for code, name in COUNTRIES.items()]
    markup.add(*buttons)
    bot.send_message(message.chat.id, "👋 Hurupay Lead Finder Bot-এ স্বাগতম!\n\nঅনুগ্রহ করে প্রথমে **দেশ (Country)** সিলেক্ট করুন:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('country_'))
def select_category(call):
    country_code = call.data.split('_')[1]
    chat_id = str(call.message.chat.id)
    
    # Firebase-এ ইউজার ডাটা সেভ করা
    db.collection('user_states').document(chat_id).set({'country': country_code}, merge=True)
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(cat, callback_data=f"cat_{cat}") for cat in CATEGORIES]
    markup.add(*buttons)
    bot.edit_message_text("✅ দেশ সিলেক্ট করা হয়েছে।\n\nএবার **ক্যাটাগরি** সিলেক্ট করুন:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def start_mission(call):
    category = call.data.split('_')[1]
    chat_id = str(call.message.chat.id)
    
    # Firebase থেকে দেশের নাম নিয়ে আসা ও ক্যাটাগরি আপডেট করা
    user_doc = db.collection('user_states').document(chat_id).get()
    country_code = user_doc.to_dict().get('country')
    country_name = COUNTRIES[country_code]
    
    db.collection('user_states').document(chat_id).set({'category': category}, merge=True)
    
    # মিশন শুরুর মেসেজ
    bot.edit_message_text(f"🚀 **মিশন শুরু হচ্ছে!**\n\nদেশ: {country_name}\nক্যাটাগরি: {category}\n\nবট এখন YouTube API ও Groq AI ব্যবহার করে স্বয়ংক্রিয়ভাবে কাজ করছে। দয়া করে অপেক্ষা করুন...", chat_id=call.message.chat.id, message_id=call.message.message_id)
    
    # মিশন ব্যাকগ্রাউন্ডে চালানোর জন্য Thread
    threading.Thread(target=process_mission, args=(call.message.chat.id, country_code, category)).start()

# ================= মূল মিশন লজিক =================
def process_mission(chat_id, country_code, category):
    leads = []
    max_results = 20
    
    # YouTube Search API
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel&q={category}&regionCode={country_code}&maxResults={max_results}&key={YOUTUBE_API_KEY}"
    response = requests.get(search_url).json()
    
    if 'items' not in response:
        bot.send_message(chat_id, "❌ YouTube API-তে কোনো সমস্যা হয়েছে অথবা কোটা শেষ।")
        return

    bot.send_message(chat_id, f"🔍 {len(response['items'])} টি প্রাথমিক চ্যানেল পাওয়া গেছে। এখন Groq (Llama-3) দিয়ে ডেসক্রিপশন পড়ে ফিল্টার করা হচ্ছে...")

    for item in response['items']:
        channel_id = item['snippet']['channelId']
        channel_name = item['snippet']['title']
        
        # Channel Details
        stats_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&id={channel_id}&key={YOUTUBE_API_KEY}"
        stats_resp = requests.get(stats_url).json()
        
        if 'items' not in stats_resp:
            continue
            
        channel_info = stats_resp['items'][0]
        sub_count = int(channel_info['statistics'].get('subscriberCount', 0))
        description = channel_info['snippet'].get('description', '')
        thumbnail = channel_info['snippet']['thumbnails']['high']['url']
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
        
        if sub_count < 10000:
            continue
            
        bot.send_message(chat_id, f"⚙️ AI যাচাই করছে: {channel_name}...")

        # Llama 3 (Groq) এআই যাচাই
        ai_evaluation = evaluate_with_llama(description, channel_name, category)
        
        if "YES" in ai_evaluation['fit'].upper():
            emails = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", description)
            contact = emails[0] if emails else "About সেকশন চেক করুন"
            
            lead_data = {
                "Creator Name": channel_name,
                "Platform & Link": channel_url,
                "Country": COUNTRIES[country_code],
                "Niche": ai_evaluation['niche'],
                "Subscribers": sub_count,
                "Contact Details": contact,
                "Estimated Rate": ai_evaluation['rate'],
                "Why fit for Hurupay": ai_evaluation['reason'],
                "Timestamp": firestore.SERVER_TIMESTAMP
            }
            leads.append(lead_data)
            
            # Firebase এ লিড সেভ করা (যাতে আজীবন ডাটাবেসে থাকে)
            db.collection('hurupay_leads').add(lead_data)
            
            # যোগ্য লিড পেলে ছবি ও মেসেজ পাঠানো
            caption = f"✅ **লিড কনফার্মড!**\n\n📌 **নাম:** {channel_name}\n👥 **Subs:** {sub_count}\n💰 **Rate:** {ai_evaluation['rate']}\n💡 **Why Fit:** {ai_evaluation['reason']}"
            bot.send_photo(chat_id, thumbnail, caption=caption)
            time.sleep(1)

        if len(leads) >= 30:
            break

    # সব শেষে ফাইল তৈরি ও পাঠানো
    if leads:
        # Timestamp বাদ দিয়ে এক্সেল তৈরি
        excel_data = [{k: v for k, v in d.items() if k != 'Timestamp'} for d in leads]
        df = pd.DataFrame(excel_data)
        file_path = f"Hurupay_Leads_{country_code}_{category.replace(' ', '_')}.xlsx"
        df.to_excel(file_path, index=False)
        
        bot.send_message(chat_id, "🎉 **মিশন সফলভাবে সম্পন্ন হয়েছে!** লিডগুলো ফায়ারবেসে সেভ হয়েছে এবং নিচে এক্সেল ফাইল দেওয়া হলো:")
        with open(file_path, "rb") as file:
            bot.send_document(chat_id, file)
        
        os.remove(file_path)
    else:
        bot.send_message(chat_id, "⚠️ দুঃখিত, আপনার দেওয়া শর্ত অনুযায়ী 100% যোগ্য কোনো চ্যানেল পাওয়া যায়নি।")

# ================= Groq (Llama 3) AI ফাংশন =================
def evaluate_with_llama(description, channel_name, category):
    prompt = f"""
    You are an expert influencer marketing manager for 'Hurupay' (an app for freelancers to receive money from abroad).
    Analyze this YouTube channel description.
    Channel Name: {channel_name}
    Description: {description}
    Target Niche: {category}
    
    Respond STRICTLY in the following format with NO extra text:
    FIT: [YES or NO based on if it matches {category}, remote work, freelance, or finance]
    NICHE: [Exact niche of the channel]
    ESTIMATED_RATE: [Estimate a price between $20-$150 based on normal micro-influencer rates]
    REASON: [1 short sentence why they fit Hurupay]
    """
    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192",
            temperature=0.2,
        )
        response_text = chat_completion.choices[0].message.content
        
        fit = re.search(r'FIT:\s*(.*)', response_text)
        niche = re.search(r'NICHE:\s*(.*)', response_text)
        rate = re.search(r'ESTIMATED_RATE:\s*(.*)', response_text)
        reason = re.search(r'REASON:\s*(.*)', response_text)
        
        return {
            'fit': fit.group(1).strip() if fit else "NO",
            'niche': niche.group(1).strip() if niche else "Unknown",
            'rate': rate.group(1).strip() if rate else "TBD",
            'reason': reason.group(1).strip() if reason else "Good fit"
        }
    except Exception as e:
        return {'fit': 'NO', 'niche': '', 'rate': '', 'reason': ''}

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    print("Bot is running...")
    bot.infinity_polling()
