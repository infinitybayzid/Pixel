import os
import requests
import tempfile
import urllib.parse
from flask import Flask, request, jsonify

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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    resp = requests.get(url, stream=True, timeout=60, headers=headers)
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

    try:
        # ডাউনলোড ফাইল
        filepath = download_file(full_url)
        filename = safe_filename(full_url)
        
        # পিক্সেলড্রেনে আপলোড
        view_link, direct_link = upload_via_put(filepath, filename)
        
        # টেম্প ফাইল ডিলিট
        if os.path.exists(filepath):
            os.remove(filepath)
        
        # JSON রেস্পন্স
        return jsonify({
            "success": True,
            "original_url": full_url,
            "filename": filename,
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

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Endpoint not found",
        "usage": "Use format: https://your-domain.koyeb.app/example.com",
        "example": "https://graceful-gusti-bayzid-simr-an-d83671b1.koyeb.app/https://github.com/example/file.zip"
    }), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
