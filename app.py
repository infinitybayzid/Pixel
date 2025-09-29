from flask import Flask, request, jsonify
import requests
import tempfile
import urllib.parse
import os
import threading
import asyncio
from main import download_file, upload_via_put, safe_filename

app = Flask(__name__)

# PixelDrain API Key (আপনার পূর্বের কোড থেকে)
PIXELDRAIN_API_KEY = "2a112291-e9f6-42a3-a03e-9b49b14d68e6"

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "PixelDrain Uploader Bot",
        "message": "Bot is running successfully!"
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

@app.route('/upload', methods=['POST', 'GET'])
def upload_file():
    if request.method == 'GET':
        return '''
        <h1>PixelDrain File Upload</h1>
        <form method="post">
            <input type="url" name="file_url" placeholder="Enter file URL" required style="width: 300px; padding: 8px;">
            <button type="submit" style="padding: 8px 16px; background: #007bff; color: white; border: none; border-radius: 4px;">Upload</button>
        </form>
        '''
    
    # POST request handling
    file_url = request.form.get('file_url') or request.json.get('file_url') if request.json else None
    
    if not file_url:
        return jsonify({"error": "File URL is required"}), 400
    
    try:
        # Download file
        filepath = download_file(file_url)
        filename = safe_filename(file_url)
        
        # Upload to PixelDrain
        view_link, direct_link = upload_via_put(filepath, filename)
        
        # Clean up
        if os.path.exists(filepath):
            os.remove(filepath)
        
        return jsonify({
            "success": True,
            "filename": filename,
            "view_link": view_link,
            "direct_download": direct_link
        })
        
    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500

@app.route('/upload_json', methods=['POST'])
def upload_json():
    """JSON API for file upload"""
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({"error": "URL is required in JSON body"}), 400
    
    file_url = data['url']
    
    try:
        # Download file
        filepath = download_file(file_url)
        filename = safe_filename(file_url)
        
        # Upload to PixelDrain
        view_link, direct_link = upload_via_put(filepath, filename)
        
        # Clean up
        if os.path.exists(filepath):
            os.remove(filepath)
        
        return jsonify({
            "status": "success",
            "filename": filename,
            "view_link": view_link,
            "direct_download": direct_link
        })
        
    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
