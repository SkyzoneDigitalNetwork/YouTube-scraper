import os
import re
import json
import traceback
import asyncio
import pandas as pd
import requests
from bs4 import BeautifulSoup
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from googleapiclient.discovery import build
from firebase_admin import credentials, firestore, initialize_app
from groq import Groq

# ================= ENVIRONMENT VARIABLES =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS") # JSON string
PORT = int(os.getenv("PORT", 8080))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# ================= INITIALIZATION =================
if FIREBASE_CREDENTIALS:
    cred_dict = json.loads(FIREBASE_CREDENTIALS)
    cred = credentials.Certificate(cred_dict)
    initialize_app(cred)
db = firestore.client()

youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

SELECT_COUNTRY, CUSTOM_COUNTRY, SELECT_NICHE, CUSTOM_NICHE = range(4)
app = Flask(__name__)

# ================= DICTIONARIES =================
COUNTRY_MAP = {
    "Indonesia": "ID", "Brazil": "BR", "Pakistan": "PK", "Bangladesh": "BD", 
    "Philippines": "PH", "Vietnam": "VN", "Argentina": "AR", "Spain": "ES", 
    "Ukraine": "UA", "Serbia": "RS", "Singapore": "SG"
}

NICHES_LIST = [
    "Remote work / freelancing", "Work-from-home jobs", "Online earning / side hustles",
    "Career tips", "Personal finance", "Tech/app reviews", "Remittance or receiving money from abroad"
]

# ================= HELPER FUNCTIONS =================
def get_country_keyboard():
    keyboard = []
    countries = list(COUNTRY_MAP.keys())
    for i in range(0, len(countries), 2):
        row = [InlineKeyboardButton(c, callback_data=f'country_{c}') for c in countries[i:i+2]]
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("✍️ Custom Country", callback_data='custom_country')])
    keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data='back_start')])
    return keyboard

def get_niche_keyboard():
    keyboard = []
    for niche in NICHES_LIST:
        short_niche = niche[:25] 
        keyboard.append([InlineKeyboardButton(niche, callback_data=f'niche_{short_niche}')])
    keyboard.append([InlineKeyboardButton("✍️ Custom Niche", callback_data='custom_niche')])
    keyboard.append([InlineKeyboardButton("🔙 Back to Country", callback_data='back_country')])
    return keyboard

def calculate_engagement_rate(channel_id):
    """Calculate engagement rate based on last 5 videos"""
    try:
        channel_res = youtube.channels().list(part='contentDetails', id=channel_id).execute()
        uploads_playlist_id = channel_res['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        
        playlist_items = youtube.playlistItems().list(part='snippet', playlistId=uploads_playlist_id, maxResults=5).execute()
        video_ids = [item['snippet']['resourceId']['videoId'] for item in playlist_items.get('items', [])]
        
        if not video_ids: return "N/A"
        
        video_stats = youtube.videos().list(part='statistics', id=','.join(video_ids)).execute()
        total_views, total_engagements = 0, 0
        
        for video in video_stats.get('items', []):
            stats = video.get('statistics', {})
            total_views += int(stats.get('viewCount', 0))
            total_engagements += int(stats.get('likeCount', 0)) + int(stats.get('commentCount', 0))
            
        if total_views == 0: return "0.00%"
        rate = (total_engagements / total_views) * 100
        return f"{rate:.2f}%"
    except Exception:
        return "N/A"

# ================= AI & SCRAPING LOGIC =================
def extract_contact_with_ai(description, niche, regex_contacts):
    """Uses Groq AI to format all found contacts nicely"""
    prompt = f"""
    Analyze this YouTube channel description:
    Description: {description}
    Niche Required: {niche}
    Pre-extracted Contacts (Regex): {regex_contacts}
    
    Task:
    1. Verify and extract ALL available contact info (Email, WhatsApp, Telegram, Phone numbers). If pre-extracted contacts exist, format them properly.
    2. Write a short note on why they fit the "Hurupay" app.
    3. Estimate rate (Micro: $50-$150, Mid: $150-$500, Macro: $500+).
    
    Return ONLY JSON format: {{"contact_info": "Email: ... | WA: ... | Telegram: ...", "fit_note": "...", "estimated_rate": "..."}}
    Make 'contact_info' equal to 'Not Found' ONLY if absolutely no email/wa/phone/telegram exists.
    """
    try:
        completion = groq_client.chat.completions.create(
            model="mixtral-8x7b-32768",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return json.loads(completion.choices[0].message.content)
    except Exception:
        return {"contact_info": regex_contacts if regex_contacts else "Not Found", "fit_note": "Good fit.", "estimated_rate": "TBD"}

async def search_youtube_leads(country, niche, message, max_results=50):
    leads = []
    target_code = COUNTRY_MAP.get(country)
    query = f"{niche} {country}"
    
    search_kwargs = {'q': query, 'part': 'snippet', 'type': 'channel', 'maxResults': max_results}
    if target_code: search_kwargs['regionCode'] = target_code

    search_response = youtube.search().list(**search_kwargs).execute()

    for item in search_response.get('items', []):
        channel_id = item['snippet']['channelId']
        stats_response = youtube.channels().list(part='statistics,snippet,brandingSettings', id=channel_id).execute()
        
        if not stats_response['items']: continue
        channel_info = stats_response['items'][0]
        
        # 1. Strict Country Check
        actual_country = channel_info['snippet'].get('country')
        if target_code and actual_country != target_code: continue 
            
        # 2. Filter Subscribers & Size Logic
        subs = int(channel_info['statistics'].get('subscriberCount', 0))
        if subs < 10000: continue
        
        if subs <= 50000: size_label = "Micro (10k-50k)"
        elif subs <= 250000: size_label = "Mid-tier (50k-250k)"
        else: size_label = "Macro (250k+)"
        
        desc = channel_info['snippet'].get('description', '')
        title = channel_info['snippet'].get('title', '')
        
        # 3. Deep Regex Search for Contacts
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', desc)
        wa_links = re.findall(r'(?:wa\.me/|api\.whatsapp\.com/send\?phone=|\+)(\d{10,15})', desc)
        tg_links = re.findall(r'(?:t\.me/|telegram\.me/)([a-zA-Z0-9_]+)', desc)
        
        raw_contacts = f"Emails: {emails}, WhatsApp: {wa_links}, Telegram: {tg_links}"
        
        ai_data = extract_contact_with_ai(desc, niche, raw_contacts)
        final_contact = ai_data.get('contact_info', 'N/A')
        
        if final_contact in ['Not Found', 'N/A', '', None, '[]']: continue 
        
        # 4. Calculate Engagement
        eng_rate = calculate_engagement_rate(channel_id)
        
        lead = {
            "Creator Name": title,
            "Platform Link": f"https://www.youtube.com/channel/{channel_id}",
            "Country": actual_country if actual_country else country,
            "Niche": niche,
            "Channel Size": size_label,
            "Subscribers": subs,
            "Engagement Rate": eng_rate,
            "Contact Details": final_contact,
            "Estimated Rate": ai_data.get('estimated_rate', 'TBD'),
            "Why fit Hurupay": ai_data.get('fit_note', 'Matches criteria')
        }
        leads.append(lead)
        db.collection("leads").document(channel_id).set(lead)
        
        # Live Update
        if len(leads) % 2 == 0:
            try:
                await message.edit_text(f"🚀 **Mission Live!**\nTarget: {country}\nNiche: {niche}\n\n🔍 **Found {len(leads)} valid leads with deep contact info...**\nProcessing, please wait! ⏳", parse_mode='Markdown')
            except: pass 
                
        await asyncio.sleep(0.1)
        
    return leads

# ================= TELEGRAM BOT LOGIC =================
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start New Mission", callback_data='new_mission')],
        [InlineKeyboardButton("📥 Download All Leads", callback_data='download_data')],
        [InlineKeyboardButton("🛑 Stop", callback_data='stop_bot')]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to Hurupay Lead Gen Bot! 🤖\nPlease select an option:", reply_markup=main_menu_keyboard())
    return SELECT_COUNTRY

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'new_mission' or query.data == 'back_country':
        await query.edit_message_text("Select Target Country:", reply_markup=InlineKeyboardMarkup(get_country_keyboard()))
        return SELECT_COUNTRY
        
    elif query.data == 'back_start':
        await query.edit_message_text("Welcome to Hurupay Lead Gen Bot! 🤖\nPlease select an option:", reply_markup=main_menu_keyboard())
        return SELECT_COUNTRY

    elif query.data.startswith('country_'):
        context.user_data['country'] = query.data.split('_')[1]
        await query.edit_message_text(f"Country selected: {context.user_data['country']}\n\nSelect Niche/Category:", reply_markup=InlineKeyboardMarkup(get_niche_keyboard()))
        return SELECT_NICHE
        
    elif query.data == 'custom_country':
        await query.edit_message_text("Please type the Country name (e.g., USA):")
        return CUSTOM_COUNTRY

    elif query.data == 'download_data':
        await download_data(query.message)
        return ConversationHandler.END
        
    elif query.data == 'stop_bot':
        await query.edit_message_text("Bot stopped. Type /start to restart.")
        return ConversationHandler.END

async def handle_custom_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['country'] = update.message.text
    await update.message.reply_text(f"Country set to {context.user_data['country']}.\nSelect Niche:", reply_markup=InlineKeyboardMarkup(get_niche_keyboard()))
    return SELECT_NICHE

async def handle_niche_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'back_country':
        await query.edit_message_text("Select Target Country:", reply_markup=InlineKeyboardMarkup(get_country_keyboard()))
        return SELECT_COUNTRY
        
    elif query.data == 'custom_niche':
        await query.edit_message_text("Please type the Keyword/Niche:")
        return CUSTOM_NICHE
        
    elif query.data.startswith('niche_'):
        short_val = query.data.split('_', 1)[1]
        full_niche = next((n for n in NICHES_LIST if n.startswith(short_val)), short_val)
        context.user_data['niche'] = full_niche
        await start_mission(query.message, context)
        return ConversationHandler.END

async def handle_custom_niche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['niche'] = update.message.text
    await start_mission(update.message, context)
    return ConversationHandler.END

async def start_mission(message, context):
    country = context.user_data.get('country')
    niche = context.user_data.get('niche')
    
    status_msg = await message.reply_text(f"🚀 **Mission Started!**\nTarget: {country}\nNiche: {niche}\n\n🔍 Extracting Engagement Rate & Deep Contacts... Please wait.", parse_mode='Markdown')
    
    try:
        leads = await search_youtube_leads(country, niche, status_msg, max_results=50)
        
        if not leads:
            await status_msg.edit_text(f"❌ Mission Finished for {country}.\nCould not find any channels with VALID Contact Info in this exact region.")
            return

        df = pd.DataFrame(leads)
        filename = f"Leads_{country}_{niche[:10]}.xlsx"
        df.to_excel(filename, index=False)
        
        await status_msg.edit_text(f"✅ **Mission Completed!**\nFound {len(leads)} highly targeted leads with solid contact info.", parse_mode='Markdown')
        await message.reply_document(document=open(filename, 'rb'), caption=f"📁 Target: {country} | Niche: {niche}")
        os.remove(filename)
        
    except Exception as e:
        error_details = traceback.format_exc()
        error_msg = f"❌ **Mission Failed/Stopped!**\nError Log:\n\n`{error_details[-1000:]}`"
        await status_msg.reply_text(error_msg, parse_mode='Markdown')

async def download_data(message):
    try:
        users_ref = db.collection("leads")
        docs = users_ref.stream()
        data = [doc.to_dict() for doc in docs]
        
        if not data:
            await message.reply_text("Database is empty.")
            return
            
        df = pd.DataFrame(data)
        filename = "All_Firebase_Leads.xlsx"
        df.to_excel(filename, index=False)
        await message.reply_document(document=open(filename, 'rb'), caption="📥 Complete Database Backup")
        os.remove(filename)
    except Exception as e:
        await message.reply_text(f"Error downloading data: {str(e)}")

# ================= SERVER & WEBHOOK =================
application = Application.builder().token(TELEGRAM_TOKEN).build()

conv_handler = ConversationHandler(
    entry_points=[CommandHandler('start', start), CallbackQueryHandler(button_handler, pattern='^download_data$|^stop_bot$|^new_mission$')],
    states={
        SELECT_COUNTRY: [CallbackQueryHandler(button_handler)],
        CUSTOM_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_country)],
        SELECT_NICHE: [CallbackQueryHandler(handle_niche_selection)],
        CUSTOM_NICHE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_niche)],
    },
    fallbacks=[CommandHandler('start', start)]
)
application.add_handler(conv_handler)

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put(update)
    return "OK", 200

if __name__ == "__main__":
    if RENDER_EXTERNAL_URL:
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{RENDER_EXTERNAL_URL}/{TELEGRAM_TOKEN}"
        )
    else:
        application.run_polling()
