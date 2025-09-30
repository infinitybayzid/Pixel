import os
import re
import requests
import tempfile
import urllib.parse
import time
from flask import Flask, request, jsonify

app = Flask(__name__)

# আপনার মূল কোড থেকে কনফিগারেশন
PIXELDRAIN_API_KEY = "2a112291-e9f6-42a3-a03e-9b49b14d68e6"
WORKER_URL = "https://cinedrive.blmbd.workers.dev/direct.aspx"

# ক্যাশে স্টোর করার জন্য ডিকশনারি
cache_store = {}

def safe_filename(url):
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path) or "file"
    name = urllib.parse.unquote(name)
    if len(name) > 255:
        name = name[:255]
    return name

def is_google_drive_url(url):
    """গুগল ড্রাইভের URL ডিটেক্ট করা"""
    google_drive_patterns = [
        r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
        r'https?://drive\.google\.com/uc\?id=([a-zA-Z0-9_-]+)',
        r'https?://docs\.google\.com/uc\?id=([a-zA-Z0-9_-]+)',
        r'https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)',
        r'https?://drive\.google\.com/uc\?export=download&id=([a-zA-Z0-9_-]+)'
    ]
    
    for pattern in google_drive_patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)  # ফাইল ID রিটার্ন করবে
    return None

def convert_to_worker_url(file_id):
    """গুগল ড্রাইভ ফাইল ID কে ওয়ার্কার URL এ কনভার্ট করা"""
    return f"{WORKER_URL}?id={file_id}"

def download_file(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': 'https://drive.google.com/'
    }
    
    # ওয়ার্কার URL এর জন্য আলাদা হেডার
    if 'workers.dev' in url:
        headers.update({
            'Origin': 'https://drive.google.com',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site'
        })
    
    resp = requests.get(url, stream=True, timeout=60, headers=headers)
    resp.raise_for_status()
    
    # ফাইলের নাম ডিটেক্ট করা
    filename = None
    if 'Content-Disposition' in resp.headers:
        content_disposition = resp.headers['Content-Disposition']
        filename_match = re.findall('filename="(.+)"', content_disposition)
        if filename_match:
            filename = filename_match[0]
    
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=filename or '')
    for chunk in resp.iter_content(chunk_size=8192):
        if chunk:
            tmp.write(chunk)
    tmp.close()
    return tmp.name, filename

def upload_via_put(filepath, filename):
    quoted = urllib.parse.quote(filename, safe='')
    url = f"https://pixeldrain.com/api/file/{quoted}"
    auth = None
    if PIXELDRAIN_API_KEY:
        auth = ("", PIXELDRAIN_API_KEY)
    with open(filepath, "rb") as f:
        resp = requests.put(url, data=f, auth=auth, headers={"Content-Type":"application/octet-stream"}, timeout=300)
    resp.raise_for_status()
    j = resp.json()
    if "id" in j:
        file_id = j['id']
        view_link = f"https://pixeldrain.com/u/{file_id}"
        direct_link = f"https://pixeldrain.com/api/file/{file_id}?download"
        return view_link, direct_link
    else:
        raise RuntimeError(f"Could not parse upload response: {resp.text}")

@app.route('/')
def home():
    return jsonify({
        "service": "PixelDrain Uploader with Google Drive Support",
        "usage": "Visit https://your-domain.koyeb.app/example.com",
        "example": "https://graceful-gusti-bayzid-simr-an-d83671b1.koyeb.app/https://drive.google.com/file/d/1idVjz4I5G6EbH2F-2SwXvFRqdhIIyVZe/view",
        "supported_formats": [
            "https://drive.google.com/file/d/FILE_ID/view",
            "https://drive.google.com/uc?id=FILE_ID",
            "https://drive.google.com/open?id=FILE_ID",
            "https://drive.google.com/uc?export=download&id=FILE_ID"
        ]
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

@app.route('/<path:url_path>')
def upload_file(url_path):
    """
    মূল ফাংশন: https://your-domain.koyeb.app/example.com
    """
    if not url_path:
        return jsonify({"error": "URL path is required"}), 400
    
    # URL রিকনস্ট্রাক্ট করা
    if not url_path.startswith(('http://', 'https://')):
        full_url = 'https://' + url_path
    else:
        full_url = url_path

    # ক্যাশে চেক করা
    cache_key = full_url
    if cache_key in cache_store:
        cache_data = cache_store[cache_key]
        # 30 মিনিটের কম হলে ক্যাশে থেকে রিটার্ন
        if time.time() - cache_data['timestamp'] < 1800:  # 1800 seconds = 30 minutes
            response_data = cache_data['response']
            response_data['cached'] = True
            return jsonify(response_data)

    try:
        # গুগল ড্রাইভ URL ডিটেক্ট করা
        google_drive_id = is_google_drive_url(full_url)
        download_url = full_url
        is_google_drive = False
        
        if google_drive_id:
            is_google_drive = True
            # ওয়ার্কার URL এ কনভার্ট করা
            download_url = convert_to_worker_url(google_drive_id)
            print(f"Google Drive detected. ID: {google_drive_id}")
            print(f"Using worker URL: {download_url}")

        # ডাউনলোড ফাইল
        filepath, detected_filename = download_file(download_url)
        
        # ফাইলের নাম নির্ধারণ
        if detected_filename:
            filename = detected_filename
        elif is_google_drive:
            filename = f"google_drive_{google_drive_id}.bin"
        else:
            filename = safe_filename(full_url)
        
        # পিক্সেলড্রেনে আপলোড
        view_link, direct_link = upload_via_put(filepath, filename)
        
        # টেম্প ফাইল ডিলিট
        if os.path.exists(filepath):
            os.remove(filepath)
        
        # JSON রেস্পন্স
        response_data = {
            "success": True,
            "original_url": full_url,
            "filename": filename,
            "view_link": view_link,
            "direct_download": direct_link,
            "message": "File uploaded successfully to PixelDrain"
        }
        
        if is_google_drive:
            response_data.update({
                "google_drive_id": google_drive_id,
                "worker_url_used": download_url,
                "source": "google_drive_via_worker"
            })
        
        # ক্যাশে সেভ করা
        cache_store[cache_key] = {
            'response': response_data,
            'timestamp': time.time()
        }
        
        return jsonify(response_data)
        
    except requests.exceptions.RequestException as e:
        error_msg = f"Download error: {str(e)}"
        if google_drive_id:
            error_msg += f" (Google Drive ID: {google_drive_id}, Worker URL: {download_url})"
        return jsonify({
            "success": False,
            "error": error_msg,
            "original_url": full_url,
            "google_drive_id": google_drive_id if 'google_drive_id' in locals() else None
        }), 500
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Upload error: {str(e)}",
            "original_url": full_url,
            "google_drive_id": google_drive_id if 'google_drive_id' in locals() else None
        }), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Endpoint not found",
        "usage": "Use format: https://your-domain.koyeb.app/example.com",
        "example": "https://graceful-gusti-bayzid-simr-an-d83671b1.koyeb.app/https://drive.google.com/file/d/1idVjz4I5G6EbH2F-2SwXvFRqdhIIyVZe/view",
        "supported_google_drive_formats": [
            "https://drive.google.com/file/d/FILE_ID/view",
            "https://drive.google.com/uc?id=FILE_ID", 
            "https://drive.google.com/open?id=FILE_ID",
            "https://drive.google.com/uc?export=download&id=FILE_ID"
        ]
    }), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
