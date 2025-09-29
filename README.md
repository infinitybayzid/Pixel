# PixelDrain Uploader Bot

একটি Telegram bot এবং ওয়েব সার্ভার যেটি URLs থেকে ফাইল ডাউনলোড করে PixelDrain-এ আপলোড করে, ভিউ এবং ডাইরেক্ট ডাউনলোড লিংক প্রদান করে।

## Features

- Telegram bot মাধ্যমে ফাইল আপলোড
- ওয়েব ইন্টারফেস মাধ্যমে ফাইল আপলোড
- JSON API সাপোর্ট
- PixelDrain এ authenticated আপলোড
- 24/7 Koyeb হোস্টিং সাপোর্ট

## Deployment on Koyeb

### Method 1: GitHub থেকে ডিপ্লয় (সবচেয়ে সহজ)

1. **এই repository টি Fork করুন** আপনার GitHub account এ

2. **[Koyeb Control Panel](https://app.koyeb.com/) এ যান**

3. **নতুন অ্যাপ তৈরি করুন:**
   - "Create App" ক্লিক করুন
   - Deployment method হিসেবে "GitHub" নির্বাচন করুন
   - আপনার forked repository সিলেক্ট করুন
   - Branch: `main` সিলেক্ট করুন
   - Build method: Dockerfile অটো ডিটেক্ট হবে

4. **"Deploy" ক্লিক করুন**

### Method 2: Docker Image থেকে

1. **Docker image build এবং push করুন:**
   ```bash
   docker build -t yourusername/pixeldrain-bot .
   docker push yourusername/pixeldrain-bot
