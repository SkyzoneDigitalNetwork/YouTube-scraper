import os
import re
import json
import traceback
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
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS") # Should be a JSON string in Render Env
PORT = int(os.getenv("PORT", 8443))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# ================= INITIALIZATION =================
# Firebase
if FIREBASE_CREDENTIALS:
    cred_dict = json.loads(FIREBASE_CREDENTIALS)
    cred = credentials.Certificate(cred_dict)
    initialize_app(cred)
db = firestore.client()

# APIs
youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

# States for ConversationHandler
SELECT_COUNTRY, CUSTOM_COUNTRY, SELECT_NICHE, CUSTOM_NICHE, SEARCHING = range(5)

# Flask for Render Webhook
app = Flask(__name__)

# ================= AI & SCRAPING LOGIC =================
def extract_contact_with_ai(description, links, niche):
    """Uses Groq AI to extract email, analyze fit, and estimate rate"""
    prompt = f"""
    Analyze this YouTube channel description and external links:
    Description: {description}
    Links: {links}
    Niche Required: {niche}
    
    Task:
    1. Extract any Email, WhatsApp, or Telegram.
    2. Write a short note on why they fit the "Hurupay" app (cross-border payments, freelance platform).
    3. Estimate rate (Micro: $50-$150, Mid: $150-$500, Macro: $500+).
    
    Return ONLY a JSON format: {{"email": "extracted_or_None", "fit_note": "...", "estimated_rate": "..."}}
    """
    try:
        completion = groq_client.chat.completions.create(
            model="mixtral-8x7b-32768",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        return {"email": "Not Found", "fit_note": "Good fit for specific niche.", "estimated_rate": "TBD"}

def search_youtube_leads(country, niche, max_results=30):
    leads = []
    query = f"{niche} {country}"
    
    # 1. Search Channels
    search_response = youtube.search().list(
        q=query, part='snippet', type='channel', maxResults=max_results
    ).execute()

    for item in search_response.get('items', []):
        channel_id = item['snippet']['channelId']
        
        # 2. Get Channel Stats & Details
        stats_response = youtube.channels().list(
            part='statistics,snippet,brandingSettings', id=channel_id
        ).execute()
        
        if not stats_response['items']: continue
        channel_info = stats_response['items'][0]
        
        subs = int(channel_info['statistics'].get('subscriberCount', 0))
        # Filter Micro, Mid, Macro (10k+)
        if subs < 10000: continue
        
        desc = channel_info['snippet'].get('description', '')
        title = channel_info['snippet'].get('title', '')
        country_code = channel_info['snippet'].get('country', country)
        
        # 3. Use AI to extract deep info
        ai_data = extract_contact_with_ai(desc, "External links omitted for brevity", niche)
        
        lead = {
            "Creator Name": title,
            "Platform Link": f"https://www.youtube.com/channel/{channel_id}",
            "Country": country_code,
            "Niche": niche,
            "Subscribers": subs,
            "Contact Details": ai_data.get('email', 'N/A'),
            "Estimated Rate": ai_data.get('estimated_rate', 'TBD'),
            "Why fit Hurupay": ai_data.get('fit_note', 'Matches criteria')
        }
        leads.append(lead)
        
        # Save to Firebase
        db.collection("leads").document(channel_id).set(lead)
        
    return leads

# ================= TELEGRAM BOT LOGIC =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🚀 Start New Mission", callback_data='new_mission')],
        [InlineKeyboardButton("📥 Download All Leads", callback_data='download_data')],
        [InlineKeyboardButton("🛑 Stop", callback_data='stop_bot')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome to Hurupay Lead Gen Bot! 🤖\nPlease select an option:", reply_markup=reply_markup)
    return SELECT_COUNTRY

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'new_mission':
        # Country Buttons
        keyboard = [
            [InlineKeyboardButton("🇮🇩 Indonesia", callback_data='country_Indonesia'),
             InlineKeyboardButton("🇵🇭 Philippines", callback_data='country_Philippines')],
            [InlineKeyboardButton("🇧🇷 Brazil", callback_data='country_Brazil'),
             InlineKeyboardButton("🇵🇰 Pakistan", callback_data='country_Pakistan')],
            [InlineKeyboardButton("✍️ Custom Country", callback_data='custom_country')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_start')]
        ]
        await query.edit_message_text("Select Target Country:", reply_markup=InlineKeyboardMarkup(keyboard))
        return SELECT_COUNTRY

    elif query.data.startswith('country_'):
        context.user_data['country'] = query.data.split('_')[1]
        return await show_niches(query)
        
    elif query.data == 'custom_country':
        await query.edit_message_text("Please type the Country name:")
        return CUSTOM_COUNTRY

    elif query.data == 'download_data':
        await download_data(query.message)
        return ConversationHandler.END
        
    elif query.data == 'stop_bot':
        await query.edit_message_text("Bot stopped. Type /start to restart.")
        return ConversationHandler.END

async def handle_custom_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['country'] = update.message.text
    # Proceed to niche selection (simulating a callback query response)
    # To keep it simple, sending a new message with niche buttons
    keyboard = get_niche_keyboard()
    await update.message.reply_text(f"Country set to {context.user_data['country']}.\nSelect Niche:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_NICHE

def get_niche_keyboard():
    return [
        [InlineKeyboardButton("💻 Remote Work/Freelancing", callback_data='niche_Remote Work')],
        [InlineKeyboardButton("💰 Personal Finance", callback_data='niche_Personal Finance')],
        [InlineKeyboardButton("📱 Tech/App Reviews", callback_data='niche_App Reviews')],
        [InlineKeyboardButton("✍️ Custom Niche", callback_data='custom_niche')],
        [InlineKeyboardButton("🔙 Back", callback_data='back_country')]
    ]

async def show_niches(query):
    await query.edit_message_text("Select Niche/Category:", reply_markup=InlineKeyboardMarkup(get_niche_keyboard()))
    return SELECT_NICHE

async def handle_niche_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'custom_niche':
        await query.edit_message_text("Please type the Keyword/Niche:")
        return CUSTOM_NICHE
    elif query.data.startswith('niche_'):
        context.user_data['niche'] = query.data.split('_')[1]
        await start_mission(query.message, context)
        return ConversationHandler.END

async def handle_custom_niche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['niche'] = update.message.text
    await start_mission(update.message, context)
    return ConversationHandler.END

async def start_mission(message, context):
    country = context.user_data.get('country')
    niche = context.user_data.get('niche')
    await message.reply_text(f"🚀 Mission Started!\nTarget: {country}\nNiche: {niche}\n\nPlease wait, scraping data and analyzing with AI... (This may take a few minutes)")
    
    try:
        # SCRAPING TRIGGER
        leads = search_youtube_leads(country, niche, max_results=30)
        
        # Create Excel File
        df = pd.DataFrame(leads)
        filename = f"Leads_{country}_{niche}.xlsx"
        df.to_excel(filename, index=False)
        
        # Send File
        await message.reply_document(document=open(filename, 'rb'), caption="✅ Mission Completed! Here is your data.")
        os.remove(filename) # Cleanup
        
    except Exception as e:
        # ERROR SCREENSHOT (Traceback)
        error_details = traceback.format_exc()
        error_msg = f"❌ **Mission Failed/Stopped!**\nHere is the error log (Crash Screenshot):\n\n`{error_details[-1000:]}`"
        await message.reply_text(error_msg, parse_mode='Markdown')

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

# ================= SERVER & WEBHOOK SETUP =================
application = Application.builder().token(TELEGRAM_TOKEN).build()

conv_handler = ConversationHandler(
    entry_points=[CommandHandler('start', start)],
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
        # Run via Webhook on Render
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"{RENDER_EXTERNAL_URL}/{TELEGRAM_TOKEN}"
        )
    else:
        # Run locally
        application.run_polling()
