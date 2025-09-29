import os
import requests
import tempfile
import urllib.parse
from flask import Flask, request, jsonify
import time

app = Flask(__name__)

# আপনার মূল কোড থেকে কনফিগারেশন
PIXELDRAIN_API_KEY = "2a112291-e9f6-42a3-a03e-9b49b14d68e6"

def safe_filename(url):
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path) or "file"
    name = urllib.parse.unquote(name)
    if len(name) > 255:
        name = name[:255]
    return name

def download_file(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': 'https://cinedrive.blmbd.workers.dev/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin'
    }
    
    # Session ব্যবহার করে cookies ম্যানেজ করা
    session = requests.Session()
    session.headers.update(headers)
    
    # কখনো কখনো একাধিক রিকোয়েস্ট প্রয়োজন হয়
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = session.get(url, stream=True, timeout=60, allow_redirects=True)
            resp.raise_for_status()
            
            # ফাইল সাইজ চেক
            content_length = resp.headers.get('Content-Length')
            if content_length and int(content_length) == 0:
                if attempt < max_retries - 1:
                    time.sleep(2)  # 2 সেকেন্ড অপেক্ষা করে আবার চেষ্টা করুন
                    continue
                else:
                    raise RuntimeError("Empty file content")
            
            tmp = tempfile.NamedTemporaryFile(delete=False)
            downloaded_size = 0
            
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    tmp.write(chunk)
                    downloaded_size += len(chunk)
            
            tmp.close()
            
            # যদি ফাইল সাইজ 0 হয়, তাহলে আবার চেষ্টা করুন
            if downloaded_size == 0:
                if attempt < max_retries - 1:
                    os.unlink(tmp.name)
                    time.sleep(2)
                    continue
                else:
                    raise RuntimeError("Downloaded file is empty")
            
            return tmp.name
            
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            else:
                raise e

def upload_via_put(filepath, filename):
    quoted = urllib.parse.quote(filename, safe='')
    url = f"https://pixeldrain.com/api/file/{quoted}"
    auth = None
    if PIXELDRAIN_API_KEY:
        auth = ("", PIXELDRAIN_API_KEY)
    
    try:
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
    except Exception as e:
        raise RuntimeError(f"Upload failed: {str(e)}")

@app.route('/')
def home():
    return jsonify({
        "service": "PixelDrain Uploader",
        "usage": "Visit https://your-domain.koyeb.app/example.com",
        "example": "https://graceful-gusti-bayzid-simr-an-d83671b1.koyeb.app/https://example.com/file.zip"
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

    filepath = None
    try:
        # ডাউনলোড ফাইল
        filepath = download_file(full_url)
        filename = safe_filename(full_url)
        
        # ফাইল সাইজ চেক
        file_size = os.path.getsize(filepath)
        if file_size == 0:
            raise RuntimeError("Downloaded file is empty")
        
        # পিক্সেলড্রেনে আপলোড
        view_link, direct_link = upload_via_put(filepath, filename)
        
        return jsonify({
            "success": True,
            "original_url": full_url,
            "filename": filename,
            "file_size": file_size,
            "view_link": view_link,
            "direct_download": direct_link,
            "message": "File uploaded successfully to PixelDrain"
        })
        
    except requests.exceptions.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"Download error: {str(e)}",
            "original_url": full_url
        }), 500
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"Upload error: {str(e)}",
            "original_url": full_url
        }), 500
    finally:
        # টেম্প ফাইল ডিলিট (যদি থাকে)
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except:
                pass

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Endpoint not found",
        "usage": "Use format: https://your-domain.koyeb.app/example.com",
        "example": "https://graceful-gusti-bayzid-simr-an-d83671b1.koyeb.app/https://github.com/example/file.zip"
    }), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
