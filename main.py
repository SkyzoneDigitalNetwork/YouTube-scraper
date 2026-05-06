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
    "Indonesia": "ID", "Brazil": "BR", "Pakistan": "PK", 
    "Bangladesh": "BD", "Philippines": "PH", "Vietnam": "VN", 
    "Argentina": "AR", "Spain": "ES", "Ukraine": "UA", 
    "Serbia": "RS", "Singapore": "SG"
}

NICHES_LIST = [
    "Remote work / freelancing", "Work-from-home jobs", "Online earning / side hustles",
    "Career tips", "Personal finance", "Tech/app reviews", "Remittance or receiving money from abroad"
]

# ================= HELPER FUNCTIONS =================
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start New Mission", callback_data='new_mission')],
        [InlineKeyboardButton("📥 Download Leads", callback_data='download_data'), 
         InlineKeyboardButton("🗑️ Clear Database", callback_data='clear_db')],
        [InlineKeyboardButton("🛑 Stop", callback_data='stop_bot')]
    ])

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
def extract_contact_with_ai(description, niche):
    prompt = f"""
    Read this YouTube channel description carefully:
    "{description}"
    
    Task:
    1. Scan for ANY true contact information (Email, WhatsApp number, Telegram handle, or Business Phone).
    2. Provide ONLY the most relevant contact method found as a single string (e.g., "Email: example@gmail.com" OR "WhatsApp: +123456..."). If absolutely no contact is found, return "Not Found".
    3. Write a short note (1-2 sentences) on why they fit the "Hurupay" app in the '{niche}' niche.
    (Do NOT estimate any pricing or rates. Keep it blank).
    
    Return EXACTLY in this JSON format: 
    {{"contact_details": "...", "fit_note": "..."}}
    """
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, 
        )
        return json.loads(completion.choices[0].message.content)
    except Exception:
        return {"contact_details": "Not Found", "fit_note": "Matches criteria."}

async def search_youtube_leads(country, niche, status_message, telegram_bot_msg_context, max_results=50):
    leads = []
    target_code = COUNTRY_MAP.get(country)
    query = f"{niche} {country}"
    
    next_page_token = None
    snapshot_sent = False # To send screenshot/proof only once
    
    # Deep Search: Loop through 3 pages (Max 150 channels) to take time and find more leads
    for page in range(3): 
        search_kwargs = {'q': query, 'part': 'snippet', 'type': 'channel', 'maxResults': max_results}
        if target_code: search_kwargs['regionCode'] = target_code
        if next_page_token: search_kwargs['pageToken'] = next_page_token

        search_response = youtube.search().list(**search_kwargs).execute()

        for item in search_response.get('items', []):
            channel_id = item['snippet']['channelId']
            stats_response = youtube.channels().list(part='statistics,snippet,brandingSettings', id=channel_id).execute()
            
            if not stats_response['items']: continue
            channel_info = stats_response['items'][0]
            
            actual_country = channel_info['snippet'].get('country')
            if target_code and actual_country != target_code: continue 
                
            subs = int(channel_info['statistics'].get('subscriberCount', 0))
            if subs < 10000: continue
            
            if subs <= 50000: size_label = "Micro (10k-50k)"
            elif subs <= 250000: size_label = "Mid-tier (50k-250k)"
            else: size_label = "Macro (250k+)"
            
            desc = channel_info['snippet'].get('description', '')
            title = channel_info['snippet'].get('title', '')
            
            ai_data = extract_contact_with_ai(desc, niche)
            final_contact = ai_data.get('contact_details', 'N/A')
            
            if final_contact.lower() in ['not found', 'n/a', '', 'none']: 
                continue 
            
            # Send "Screenshot/AI Vision Proof" for the first successful lead
            if not snapshot_sent:
                snapshot_text = f"📸 **AI Vision Snapshot (Proof of Work)**\n\n" \
                                f"📺 **Channel Name:** {title}\n" \
                                f"📄 **Raw Description Snippet:**\n`{desc[:250]}...`\n\n" \
                                f"🤖 **AI Output Extracted:**\n`{final_contact}`"
                await telegram_bot_msg_context.reply_text(snapshot_text, parse_mode='Markdown')
                snapshot_sent = True

            eng_rate = calculate_engagement_rate(channel_id)
            
            lead = {
                "Creator/channel name": title,
                "Platform and account link": f"https://www.youtube.com/channel/{channel_id}",
                "Country/audience country": actual_country if actual_country else country,
                "Niche": niche,
                "Subscriber/follower count": subs,
                "Engagement rate if available": eng_rate,
                "Contact details": final_contact,
                "Estimated rate/package": "", # Kept blank as requested
                "Short note on why they fit Hurupay": ai_data.get('fit_note', 'Matches criteria')
            }
            leads.append(lead)
            db.collection("leads").document(channel_id).set(lead)
            
            if len(leads) % 3 == 0:
                try:
                    await status_message.edit_text(f"🚀 **Mission Live & Searching Deeply!**\nTarget: {country}\nNiche: {niche}\n\n🔍 **Found {len(leads)} solid leads so far...**\nProcessing pages, this will take a few minutes! ⏳", parse_mode='Markdown')
                except: pass 
                    
            await asyncio.sleep(2) # Intentional delay to avoid API block and search deeply
            
        next_page_token = search_response.get('nextPageToken')
        if not next_page_token:
            break # Stop if no more pages

    return leads

# ================= TELEGRAM BOT LOGIC =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome to Hurupay Lead Gen Bot! 🤖\nPlease select an option:", reply_markup=main_menu_keyboard())
    return SELECT_COUNTRY

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    data = query.data
    
    if data == 'new_mission' or data == 'back_country':
        await query.edit_message_text("Select Target Country:", reply_markup=InlineKeyboardMarkup(get_country_keyboard()))
        return SELECT_COUNTRY
        
    elif data == 'back_start':
        await query.edit_message_text("Welcome to Hurupay Lead Gen Bot! 🤖\nPlease select an option:", reply_markup=main_menu_keyboard())
        return SELECT_COUNTRY

    elif data.startswith('country_'):
        context.user_data['country'] = data.split('_')[1]
        await query.edit_message_text(f"Country selected: {context.user_data['country']}\n\nSelect Niche/Category:", reply_markup=InlineKeyboardMarkup(get_niche_keyboard()))
        return SELECT_NICHE
        
    elif data == 'custom_country':
        await query.edit_message_text("Please type the Country name (e.g., USA):")
        return CUSTOM_COUNTRY

    elif data == 'download_data':
        await download_data(query.message)
        return SELECT_COUNTRY

    elif data == 'clear_db':
        # Delete all documents in Firestore 'leads' collection
        docs = db.collection("leads").stream()
        count = 0
        for doc in docs:
            doc.reference.delete()
            count += 1
        await query.edit_message_text(f"🗑️ Database Cleared Successfully!\nDeleted {count} old leads.", reply_markup=main_menu_keyboard())
        return SELECT_COUNTRY
        
    elif data == 'stop_bot':
        await query.edit_message_text("Bot stopped. Type /start to restart.")
        return ConversationHandler.END

async def handle_custom_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['country'] = update.message.text
    await update.message.reply_text(f"Country set to {context.user_data['country']}.\nSelect Niche:", reply_markup=InlineKeyboardMarkup(get_niche_keyboard()))
    return SELECT_NICHE

async def handle_niche_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == 'back_country':
        await query.edit_message_text("Select Target Country:", reply_markup=InlineKeyboardMarkup(get_country_keyboard()))
        return SELECT_COUNTRY
        
    elif data == 'custom_niche':
        await query.edit_message_text("Please type the Keyword/Niche:")
        return CUSTOM_NICHE
        
    elif data.startswith('niche_'):
        short_val = data.split('_', 1)[1]
        full_niche = next((n for n in NICHES_LIST if n.startswith(short_val)), short_val)
        context.user_data['niche'] = full_niche
        
        # FIX: Remove Inline Keyboard so Back button can't be clicked during mission
        await query.edit_message_reply_markup(reply_markup=None) 
        
        await start_mission(query.message, context)
        return ConversationHandler.END

async def handle_custom_niche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['niche'] = update.message.text
    await start_mission(update.message, context)
    return ConversationHandler.END

async def start_mission(message, context):
    country = context.user_data.get('country')
    niche = context.user_data.get('niche')
    
    status_msg = await message.reply_text(f"🚀 **Mission Started!**\nTarget: {country}\nNiche: {niche}\n\n🔍 AI is researching deeply. This will take a few minutes...", parse_mode='Markdown')
    
    try:
        leads = await search_youtube_leads(country, niche, status_msg, message, max_results=50)
        
        if not leads:
            await status_msg.edit_text(f"❌ Mission Finished for {country}.\nCould not find channels with VALID Contact Info in this region.")
            await message.reply_text("What would you like to do next?", reply_markup=main_menu_keyboard())
            return

        df = pd.DataFrame(leads)
        safe_niche = re.sub(r'[\\/*?:"<>|]', "_", niche)[:15]
        filename = f"Leads_{country}_{safe_niche}.xlsx"
        df.to_excel(filename, index=False)
        
        await status_msg.edit_text(f"✅ **Mission Completed!**\nFound {len(leads)} highly targeted leads with solid contact info.", parse_mode='Markdown')
        await message.reply_document(document=open(filename, 'rb'), caption=f"📁 Target: {country} | Niche: {niche}")
        os.remove(filename)
        
        # Send fresh menu after mission completes
        await message.reply_text("Mission Finished! 🎯 What would you like to do next?", reply_markup=main_menu_keyboard())
        
    except Exception as e:
        error_details = traceback.format_exc()
        error_msg = f"❌ **Mission Failed/Stopped!**\nError Log:\n\n`{error_details[-1000:]}`"
        await status_msg.reply_text(error_msg, parse_mode='Markdown')
        await message.reply_text("System Restarted.", reply_markup=main_menu_keyboard())

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
    entry_points=[
        CommandHandler('start', start), 
        CallbackQueryHandler(button_handler, pattern='^download_data$|^clear_db$|^stop_bot$|^new_mission$')
    ],
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
