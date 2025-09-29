import os
import requests
import tempfile
import urllib.parse
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import nest_asyncio
import asyncio
import logging
import threading
from app import app  # Flask app import

# -----------------------
# Logging সেটআপ
# -----------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Apply nest_asyncio for async compatibility
nest_asyncio.apply()

# -----------------------
# কনফিগারেশন - সবকিছু ফাইলের ভিতরে
# -----------------------
BOT_TOKEN = "7843173732:AAEXf9AuZcgGhLz2bVX4NMg3Z87SZCyXlBI"
PIXELDRAIN_API_KEY = "2a112291-e9f6-42a3-a03e-9b49b14d68e6"

# -----------------------
# Safe filename
# -----------------------
def safe_filename(url):
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path) or "file"
    name = urllib.parse.unquote(name)
    if len(name) > 255:
        name = name[:255]
    # Remove problematic characters
    name = "".join(c for c in name if c.isalnum() or c in "._- ")
    return name

# -----------------------
# File download
# -----------------------
def download_file(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    resp = requests.get(url, stream=True, timeout=60, headers=headers)
    resp.raise_for_status()
    
    tmp = tempfile.NamedTemporaryFile(delete=False)
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                tmp.write(chunk)
        tmp.close()
        return tmp.name
    except Exception:
        if os.path.exists(tmp.name):
            os.remove(tmp.name)
        raise

# -----------------------
# Upload via Pixeldrain
# -----------------------
def upload_via_put(filepath, filename):
    # Pixeldrain requires the filename in the URL
    quoted = urllib.parse.quote(filename, safe='')
    url = f"https://pixeldrain.com/api/file/{quoted}"
    
    auth = None
    if PIXELDRAIN_API_KEY:
        auth = ("", PIXELDRAIN_API_KEY)
    
    with open(filepath, "rb") as f:
        resp = requests.put(
            url, 
            data=f, 
            auth=auth, 
            headers={"Content-Type": "application/octet-stream"}, 
            timeout=300
        )
    
    resp.raise_for_status()
    j = resp.json()
    
    if "id" in j:
        file_id = j['id']
        # Permanent view link
        view_link = f"https://pixeldrain.com/u/{file_id}"
        # Direct download link
        direct_link = f"https://pixeldrain.com/api/file/{file_id}?download"
        return view_link, direct_link
    else:
        raise RuntimeError(f"Could not parse upload response: {resp.text}")

# -----------------------
# Start command
# -----------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **PixelDrain Uploader Bot**\n\n"
        "Usage: `/upload <file_url>`\n\n"
        "Example: `/upload https://example.com/file.zip`\n\n"
        "The bot will download the file and upload it to PixelDrain, then provide you with both view and direct download links.",
        parse_mode='Markdown'
    )

# -----------------------
# Upload command
# -----------------------
async def upload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/upload <file_url>`", parse_mode='Markdown')
        return
    
    url = context.args[0].strip()
    
    # Basic URL validation
    if not url.startswith(('http://', 'https://')):
        await update.message.reply_text("❌ Please provide a valid HTTP/HTTPS URL")
        return
    
    msg = await update.message.reply_text(f"⏳ Downloading file...\n`{url}`", parse_mode='Markdown')
    
    try:
        filepath = await asyncio.to_thread(download_file, url)
        logger.info(f"Downloaded file to: {filepath}")
    except Exception as e:
        logger.error(f"Download error: {e}")
        await msg.edit_text(f"❌ Error downloading file: `{str(e)}`", parse_mode='Markdown')
        return
    
    filename = safe_filename(url)
    await msg.edit_text(f"⬆️ Uploading to PixelDrain: `{filename}`", parse_mode='Markdown')
    
    try:
        view_link, direct_link = await asyncio.to_thread(upload_via_put, filepath, filename)
        logger.info(f"Upload successful: {view_link}")
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await msg.edit_text(f"❌ Upload error: `{str(e)}`", parse_mode='Markdown')
        if os.path.exists(filepath):
            os.remove(filepath)
        return
    
    # Clean up temporary file
    if os.path.exists(filepath):
        os.remove(filepath)
    
    # Send success message
    await msg.edit_text(
        f"✅ **Upload Successful!**\n\n"
        f"📁 **Filename:** `{filename}`\n\n"
        f"🌐 **View Link:** {view_link}\n\n"
        f"⬇️ **Direct Download:** {direct_link}",
        parse_mode='Markdown'
    )

# -----------------------
# Error handler
# -----------------------
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling an update: {context.error}")

# -----------------------
# Start web server in a thread
# -----------------------
def run_web_server():
    app.run(host='0.0.0.0', port=8000, debug=False, use_reloader=False)

# -----------------------
# Main function
# -----------------------
def main():
    # Start web server in a separate thread
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("Web server started on port 8000")
    
    # Check if BOT_TOKEN is available
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not found!")
        return
    
    # Create Telegram application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("upload", upload_cmd))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
