# PixelDrain Uploader Bot

A Telegram bot that downloads files from URLs and uploads them to PixelDrain, providing both view and direct download links.

## Features

- Download files from any public URL
- Upload to PixelDrain with API key authentication
- Provides both view and direct download links
- Safe filename handling
- Error handling and logging

## Deployment on Koyeb

### Method 1: GitHub Deployment (Recommended)

1. **Fork this repository** to your GitHub account

2. **Go to [Koyeb Control Panel](https://app.koyeb.com/)**

3. **Create new application:**
   - Click "Create App"
   - Choose "GitHub" as deployment method
   - Select your forked repository
   - Select branch: `main`

4. **Set environment variables:**
   - `BOT_TOKEN`: Your Telegram Bot Token from [@BotFather](https://t.me/BotFather)
   - `PIXELDRAIN_API_KEY`: Your PixelDrain API key (optional but recommended)

5. **Click "Deploy"**

### Method 2: Docker Deployment

1. **Build and push to Docker registry:**
   ```bash
   docker build -t yourusername/pixeldrain-bot .
   docker push yourusername/pixeldrain-bot
