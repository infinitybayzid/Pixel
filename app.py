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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    }
    
    # বিশেষ করে Google Drive worker লিংকের জন্য
    if 'workers.dev' in url or 'google' in url:
        headers.update({
            'Referer': 'https://drive.google.com/',
            'Origin': 'https://drive.google.com'
        })
    
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        resp = session.get(url, stream=True, timeout=60, allow_redirects=True)
        resp.raise_for_status()
        
        # কন্টেন্ট টাইপ এবং সাইজ চেক
        content_type = resp.headers.get('content-type', '')
        content_length = resp.headers.get('content-length')
        
        # যদি HTML রিটার্ন করে (error page), তাহলে error throw
        if 'text/html' in content_type and int(content_length or 0) < 100000:
            # ছোট HTML ফাইল সাধারণত error page
            content_preview = resp.text[:500]
            if any(keyword in content_preview.lower() for keyword in ['error', 'unauthorized', 'access denied', 'forbidden']):
                raise requests.exceptions.HTTPError(f"Server returned HTML error page: {content_preview}")
        
        tmp = tempfile.NamedTemporaryFile(delete=False)
        downloaded_size = 0
        
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                tmp.write(chunk)
                downloaded_size += len(chunk)
                
                # যদি খুব ছোট ফাইল হয় (সম্ভাব্য error page)
                if downloaded_size < 1000 and b'<html' in chunk.lower():
                    tmp.close()
                    os.remove(tmp.name)
                    raise requests.exceptions.HTTPError("Server returned HTML instead of file")
        
        tmp.close()
        
        # ফাইল সাইজ চেক
        file_size = os.path.getsize(tmp.name)
        if file_size < 100:  # খুব ছোট ফাইল সম্ভবত error
            with open(tmp.name, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                if any(keyword in content.lower() for keyword in ['error', 'unauthorized']):
                    os.remove(tmp.name)
                    raise requests.exceptions.HTTPError(f"Server error: {content}")
        
        return tmp.name
        
    except requests.exceptions.RequestException as e:
        raise e
    except Exception as e:
        if 'tmp' in locals() and os.path.exists(tmp.name):
            os.remove(tmp.name)
        raise e

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
        "example": "https://graceful-gusti-bayzid-simr-an-d83671b1.koyeb.app/https://example.com/file.zip",
        "supported_sites": "All direct download links including Google Drive workers"
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
        
    except requests.exceptions.HTTPError as e:
        return jsonify({
            "success": False,
            "error": f"HTTP Error: {str(e)}",
            "original_url": full_url,
            "solution": "This link may require authentication or is not a direct download link"
        }), 400
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
