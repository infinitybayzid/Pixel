# bot.py
import os
import requests
import tempfile
import urllib.parse
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import nest_asyncio, asyncio

nest_asyncio.apply()

BOT_TOKEN = os.getenv("BOT_TOKEN")  # Environment variable থেকে নেবে
PIXELDRAIN_API_KEY = os.getenv("PIXELDRAIN_API_KEY")

def safe_filename(url):
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path) or "file"
    name = urllib.parse.unquote(name)
    if len(name) > 255:
        name = name[:255]
    return name

def download_file(url):
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(delete=False)
    for chunk in resp.iter_content(chunk_size=8192):
        if chunk:
            tmp.write(chunk)
    tmp.close()
    return tmp.name

def upload_via_put(filepath, filename):
    quoted = urllib.parse.quote(filename, safe='')
    url = f"https://pixeldrain.com/api/file/{quoted}"
    auth = None
    if PIXELDRAIN_API_KEY:
        auth = ("", PIXELDRAIN_API_KEY)
    with open(filepath, "rb") as f:
        resp = requests.put(url, data=f, auth=auth,
                            headers={"Content-Type":"application/octet-stream"},
                            timeout=300)
    resp.raise_for_status()
    j = resp.json()
    if "id" in j:
        return f"https://pixeldrain.com/u/{j['id']}"
    else:
        raise RuntimeError(f"Could not parse upload response: {resp.text}")

async def upload_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /upload <url>")
        return
    url = context.args[0]
    msg = await update.message.reply_text(f"Downloading from: {url}")
    try:
        filepath = await asyncio.to_thread(download_file, url)
    except Exception as e:
        await msg.edit_text(f"Error downloading: {e}")
        return
    filename = safe_filename(url)
    await msg.edit_text(f"Uploading file: {filename}")
    try:
        link = await asyncio.to_thread(upload_via_put, filepath, filename)
    except Exception as e:
        await msg.edit_text(f"Upload error: {e}")
        os.remove(filepath)
        return
    os.remove(filepath)
    await msg.edit_text(f"Success: {link}")

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("upload", upload_cmd))
    print("Bot starting...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
