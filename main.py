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
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS") # Should be a JSON string in Render Env
PORT = int(os.getenv("PORT", 8443))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# ================= INITIALIZATION =================
if FIREBASE_CREDENTIALS:
    cred_dict = json.loads(FIREBASE_CREDENTIALS)
    cred = credentials.Certificate(cred_dict)
    initialize_app(cred)
db = firestore.client()

youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
groq_client = Groq(api_key=GROQ_API_KEY)

SELECT_COUNTRY, CUSTOM_COUNTRY, SELECT_NICHE, CUSTOM_NICHE, SEARCHING = range(5)
app = Flask(__name__)

# ================= DICTIONARIES =================
# Country ISO Codes for strict filtering
COUNTRY_MAP = {
    "Indonesia": "ID", "Brazil": "BR", "Pakistan": "PK", "Bangladesh": "BD", 
    "Philippines": "PH", "Vietnam": "VN", "Argentina": "AR", "Spain": "ES", 
    "Ukraine": "UA", "Serbia": "RS", "Singapore": "SG"
}

NICHES_LIST = [
    "Remote work / freelancing", "Work-from-home jobs", "Online earning / side hustles",
    "Career tips", "Personal finance", "Tech/app reviews", "Remittance or receiving money from abroad"
]

# ================= AI & SCRAPING LOGIC =================
def extract_contact_with_ai(description, links, niche, regex_email):
    """Uses Groq AI to format contact, analyze fit, and estimate rate"""
    prompt = f"""
    Analyze this YouTube channel description and external links:
    Description: {description}
    Links: {links}
    Niche Required: {niche}
    Regex Found Email: {regex_email if regex_email else 'None'}
    
    Task:
    1. Extract Email, WhatsApp, or Telegram. If Regex Found Email is provided, use it.
    2. Write a short note on why they fit the "Hurupay" app.
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
        return {"email": regex_email if regex_email else "Not Found", "fit_note": "Good fit.", "estimated_rate": "TBD"}

async def search_youtube_leads(country, niche, message, max_results=50):
    leads = []
    target_code = COUNTRY_MAP.get(country)
    query = f"{niche} {country}"
    
    # YouTube API setup
    search_kwargs = {'q': query, 'part': 'snippet', 'type': 'channel', 'maxResults': max_results}
    if target_code:
        search_kwargs['regionCode'] = target_code # Strict search by region

    search_response = youtube.search().list(**search_kwargs).execute()

    for index, item in enumerate(search_response.get('items', [])):
        channel_id = item['snippet']['channelId']
        
        # Get Deep Stats
        stats_response = youtube.channels().list(part='statistics,snippet,brandingSettings', id=channel_id).execute()
        if not stats_response['items']: continue
        channel_info = stats_response['items'][0]
        
        # 1. STRICT COUNTRY CHECK
        actual_country = channel_info['snippet'].get('country')
        if target_code and actual_country != target_code:
            continue # Skip if country doesn't match perfectly
            
        # 2. FILTER SUBSCRIBERS
        subs = int(channel_info['statistics'].get('subscriberCount', 0))
        if subs < 10000: continue
        
        desc = channel_info['snippet'].get('description', '')
        title = channel_info['snippet'].get('title', '')
        
        # 3. MANDATORY CONTACT INFO SEARCH
        # Fast Regex search first
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', desc)
        extracted_email = emails[0] if emails else None
        
        ai_data = extract_contact_with_ai(desc, "External links omitted", niche, extracted_email)
        final_contact = ai_data.get('email', 'N/A')
        
        # Skip lead if NO contact info is found
        if final_contact in ['Not Found', 'N/A', '', None]:
            continue 
        
        # Build Lead Data
        lead = {
            "Creator Name": title,
            "Platform Link": f"https://www.youtube.com/channel/{channel_id}",
            "Country": actual_country if actual_country else country,
            "Niche": niche,
            "Subscribers": subs,
            "Contact Details": final_contact,
            "Estimated Rate": ai_data.get('estimated_rate', 'TBD'),
            "Why fit Hurupay": ai_data.get('fit_note', 'Matches criteria')
        }
        leads.append(lead)
        db.collection("leads").document(channel_id).set(lead)
        
        # 4. LIVE UPDATE IN TELEGRAM
        if len(leads) % 2 == 0: # Update message every 2 leads found
            try:
                await message.edit_text(f"🚀 **Mission Live!**\nTarget: {country}\nNiche: {niche}\n\n🔍 **Found {len(leads)} valid leads with contact info so far...**\nProcessing, please wait! ⏳", parse_mode='Markdown')
            except:
                pass # Ignore telegram "message is not modified" errors
                
        await asyncio.sleep(0.1) # Prevent event loop blocking
        
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
        # Generate Country Buttons based on requested list
        keyboard = []
        countries = list(COUNTRY_MAP.keys())
        for i in range(0, len(countries), 2):
            row = [InlineKeyboardButton(c, callback_data=f'country_{c}') for c in countries[i:i+2]]
            keyboard.append(row)
            
        keyboard.append([InlineKeyboardButton("✍️ Custom Country", callback_data='custom_country')])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='back_start')])
        
        await query.edit_message_text("Select Target Country:", reply_markup=InlineKeyboardMarkup(keyboard))
        return SELECT_COUNTRY

    elif query.data.startswith('country_'):
        context.user_data['country'] = query.data.split('_')[1]
        return await show_niches(query)
        
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
    keyboard = get_niche_keyboard()
    await update.message.reply_text(f"Country set to {context.user_data['country']}.\nSelect Niche:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_NICHE

def get_niche_keyboard():
    keyboard = []
    for niche in NICHES_LIST:
        # Callback data length limit workaround
        short_niche = niche[:25] 
        keyboard.append([InlineKeyboardButton(niche, callback_data=f'niche_{short_niche}')])
        
    keyboard.append([InlineKeyboardButton("✍️ Custom Niche", callback_data='custom_niche')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='back_country')])
    return keyboard

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
        # Match short niche back to full string if possible
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
    
    status_msg = await message.reply_text(f"🚀 **Mission Started!**\nTarget: {country}\nNiche: {niche}\n\n🔍 Starting search and AI analysis... Please wait.", parse_mode='Markdown')
    
    try:
        # SCRAPING TRIGGER WITH LIVE UPDATE
        leads = await search_youtube_leads(country, niche, status_msg, max_results=50)
        
        if not leads:
            await status_msg.edit_text(f"❌ Mission Finished for {country}.\nCould not find any channels with VALID Contact Info & >10k subs in this exact region.")
            return

        # Create Excel File
        df = pd.DataFrame(leads)
        filename = f"Leads_{country}_{niche[:10]}.xlsx"
        df.to_excel(filename, index=False)
        
        # Send File & Final Update
        await status_msg.edit_text(f"✅ **Mission Completed!**\nFound {len(leads)} highly targeted leads with contact info.", parse_mode='Markdown')
        await message.reply_document(document=open(filename, 'rb'), caption=f"📁 Target: {country} | Niche: {niche}")
        os.remove(filename) # Cleanup
        
    except Exception as e:
        # ERROR SCREENSHOT (Traceback)
        error_details = traceback.format_exc()
        error_msg = f"❌ **Mission Failed/Stopped!**\nHere is the error log:\n\n`{error_details[-1000:]}`"
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
