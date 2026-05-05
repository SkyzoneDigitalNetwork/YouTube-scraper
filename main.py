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

bot = telebot.TeleBot(TELEGRAM_TOKEN)
groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)
@app.route('/')
def keep_alive():
    return "Hurupay Lead Finder Bot is Awake 24/7!"

# ================= দেশের লিস্ট =================
COUNTRIES = {
    "ID": "Indonesia", "PH": "Philippines", "BR": "Brazil", 
    "PK": "Pakistan", "BD": "Bangladesh", "VN": "Vietnam",
    "AR": "Argentina", "ES": "Spain", "UA": "Ukraine", 
    "RS": "Serbia", "SG": "Singapore"
}

# ইউজার ডাটা সংরক্ষণের জন্য লোকাল ডিকশনারি
user_session = {}

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
def select_country(call):
    country_code = call.data.split('_')[1]
    chat_id = call.message.chat.id
    
    user_session[chat_id] = {'country': country_code}
    
    # ম্যানুয়াল কিওয়ার্ড চাওয়ার মেসেজ
    bot.edit_message_text(
        f"✅ দেশ সিলেক্ট করা হয়েছে: **{COUNTRIES[country_code]}**\n\nএবার আপনি ইউটিউবে যা লিখে সার্চ করতে চান (Niche/Category), সেটি **ম্যানুয়ালি টাইপ করে মেসেজ পাঠান**।\n\n*(যেমন: Freelancing, Make money online, Remote work)*", 
        chat_id=chat_id, message_id=call.message.message_id, parse_mode="Markdown"
    )
    # পরবর্তী মেসেজটি ম্যানুয়ালি রিসিভ করার জন্য
    bot.register_next_step_handler(call.message, get_manual_keyword)

def get_manual_keyword(message):
    chat_id = message.chat.id
    keyword = message.text.strip()
    
    if chat_id not in user_session:
        bot.send_message(chat_id, "⚠️ সেশন শেষ হয়ে গেছে। দয়া করে /start লিখে আবার শুরু করুন।")
        return
        
    country_code = user_session[chat_id]['country']
    country_name = COUNTRIES[country_code]
    
    user_session[chat_id]['keyword'] = keyword
    
    # কাজ শুরুর মেসেজ এবং ইউটিউব সার্চ পেজের স্ক্রিনশট
    search_query = keyword.replace(" ", "+")
    youtube_search_url = f"https://www.youtube.com/results?search_query={search_query}"
    start_screenshot = f"https://image.thum.io/get/width/1080/crop/800/{youtube_search_url}"
    
    bot.send_photo(
        chat_id, 
        start_screenshot, 
        caption=f"🚀 **মিশন শুরু হয়েছে!**\n\n🌍 দেশ: {country_name}\n🎯 সার্চ কিওয়ার্ড: {keyword}\n\nবট এখন আপনার দেওয়া কিওয়ার্ড দিয়ে ইউটিউবে সার্চ করছে এবং লিড সংগ্রহ করছে। রিয়েল-টাইম আপডেট নিচে আসতে থাকবে..."
    )
    
    # ব্যাকগ্রাউন্ডে মিশন চালানো
    threading.Thread(target=process_mission, args=(chat_id, country_code, keyword)).start()

# ================= লিড খোঁজার মূল লজিক =================
def process_mission(chat_id, country_code, keyword):
    leads = []
    
    # আপনার ম্যানুয়াল কিওয়ার্ড দিয়ে ইউটিউবে সরাসরি সার্চ (ঠিক যেমন আপনি নিজে করেন)
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=channel,video&q={keyword}&regionCode={country_code}&maxResults=50&key={YOUTUBE_API_KEY}"
    response = requests.get(search_url).json()
    
    if 'items' not in response:
        bot.send_message(chat_id, "❌ YouTube API Error বা কোটা শেষ হয়ে গেছে।")
        return

    # ইউনিক চ্যানেল আইডি বের করা
    channel_ids = list(set([item['snippet']['channelId'] for item in response['items']]))
    bot.send_message(chat_id, f"🔍 ইউটিউব থেকে **{len(channel_ids)}** টি চ্যানেল পাওয়া গেছে। এখন 10k+ সাবস্ক্রাইবার ফিল্টার করা হচ্ছে...")

    for channel_id in channel_ids:
        stats_url = f"https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&id={channel_id}&key={YOUTUBE_API_KEY}"
        stats_resp = requests.get(stats_url).json()
        
        if 'items' not in stats_resp:
            continue
            
        channel_info = stats_resp['items'][0]
        channel_name = channel_info['snippet']['title']
        description = channel_info['snippet'].get('description', '')
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
        
        # স্ট্যাটিস্টিকস
        sub_count = int(channel_info['statistics'].get('subscriberCount', 0))
        view_count = int(channel_info['statistics'].get('viewCount', 0))
        video_count = int(channel_info['statistics'].get('videoCount', 1))
        
        # শর্ত: 10k থেকে কম হলে বাদ (Micro, Mid, Macro এর শর্ত অনুযায়ী)
        if sub_count < 10000:
            continue
            
        bot.send_message(chat_id, f"⚙️ প্রসেস করা হচ্ছে: **{channel_name}** ({sub_count} Subs)...", parse_mode="Markdown")

        # এনগেজমেন্ট রেট ক্যালকুলেশন
        avg_views_per_video = view_count / (video_count if video_count > 0 else 1)
        eng_rate_value = (avg_views_per_video / sub_count) * 100 if sub_count > 0 else 0
        engagement_rate = f"{eng_rate_value:.2f}%"
        
        # Llama 3 AI দিয়ে শুধুমাত্র ডাটা সাজানো (AI এখন আর কাউকে বাদ দেবে না)
        ai_data = extract_data_with_llama(description, channel_name, keyword)
        
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
        
        # ফায়ারবেসে সেভ করা
        if firebase_json_str:
            try:
                lead_data_db = lead_data.copy()
                lead_data_db["Timestamp"] = firestore.SERVER_TIMESTAMP
                db.collection('hurupay_leads').add(lead_data_db)
            except:
                pass
        
        # মূল চ্যানেলের লাইভ ওয়েবসাইট স্ক্রিনশট (Screenshot API)
        channel_screenshot = f"https://image.thum.io/get/width/1080/crop/900/{channel_url}"
        
        caption = f"✅ **লিড কনফার্মড!**\n\n📌 **Name:** {channel_name}\n👥 **Subs:** {sub_count}\n🔥 **Eng. Rate:** {engagement_rate}\n💰 **Rate:** {ai_data['rate']}\n💡 **Fit Reason:** {ai_data['reason']}\n🔗 **Link:** {channel_url}"
        
        try:
            bot.send_photo(chat_id, channel_screenshot, caption=caption, parse_mode="Markdown")
        except:
            # স্ক্রিনশট এপিআই ফেল করলে নরমাল মেসেজ
            bot.send_message(chat_id, caption, parse_mode="Markdown")
            
        time.sleep(2) # ইউটিউব এবং স্ক্রিনশট API এর লিমিট এড়াতে

        if len(leads) >= 40: # সর্বোচ্চ ৪০টি লিড পেলে থামবে
            break

    # কাজ শেষে ফাইল তৈরি ও পাঠানো
    if leads:
        df = pd.DataFrame(leads)
        file_path = f"Hurupay_Leads_{country_code}_{keyword.replace(' ', '_')}.xlsx"
        df.to_excel(file_path, index=False)
        
        bot.send_message(chat_id, f"🎉 **মিশন সফল!** আপনার ম্যানুয়াল সার্চ অনুযায়ী {len(leads)} টি যোগ্য লিড পাওয়া গেছে। নিচে ডাউনলোড বাটন/ফাইল দেওয়া হলো:")
        with open(file_path, "rb") as file:
            bot.send_document(chat_id, file)
        
        os.remove(file_path)
    else:
        bot.send_message(chat_id, "⚠️ দুঃখিত, আপনার দেওয়া কিওয়ার্ড দিয়ে এই দেশে 10k+ সাবস্ক্রাইবার আছে এমন কোনো চ্যানেল পাওয়া যায়নি। অন্য কিওয়ার্ড লিখে সার্চ করুন।")

# ================= Groq (Llama 3) AI Prompt (Modified) =================
def extract_data_with_llama(description, channel_name, keyword):
    # AI কে কড়া নির্দেশ দেওয়া হয়েছে যাতে সে নিজে থেকে কোনো কিছু বাতিল না করে, শুধু ডাটা এক্সট্রাক্ট করে।
    prompt = f"""
    You are working for 'Hurupay' (an app for receiving freelance money from abroad).
    A YouTube search for "{keyword}" returned this channel. 
    Channel Name: {channel_name}
    Description: {description}
    
    Do NOT evaluate if they fit or not. Just assume they DO fit.
    Extract the following information based on their name and description.
    Respond STRICTLY in the following format with NO extra text:
    
    NICHE: [Identify the channel's niche in 2-3 words]
    ESTIMATED_RATE: [Estimate a rate between $50 to $300 based on micro/mid-tier influencer pricing]
    REASON: [Write 1 professional sentence explaining how 'Hurupay' app aligns with their content or audience]
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
            'niche': niche.group(1).strip() if niche else "Related to Search",
            'rate': rate.group(1).strip() if rate else "TBD",
            'reason': reason.group(1).strip() if reason else f"Audience is interested in {keyword}, which aligns with Hurupay."
        }
    except Exception as e:
        return {'niche': keyword, 'rate': 'TBD', 'reason': f"Aligns with {keyword}."}

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    print("Hurupay Bot is running with manual keyword and screenshot features...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)
