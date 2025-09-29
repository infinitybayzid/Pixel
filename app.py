import os
import requests
import tempfile
import urllib.parse
from flask import Flask, request, jsonify

app = Flask(__name__)

# -----------------------
# কনফিগারেশন - সবকিছু ফাইলের ভিতরে
# -----------------------
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
# Routes
# -----------------------
@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "PixelDrain Uploader",
        "message": "Use /upload/<url> to upload files to PixelDrain",
        "usage": "Visit https://your-app.koyeb.app/upload/https://example.com/file.zip"
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

@app.route('/upload/', defaults={'url_path': ''})
@app.route('/upload/<path:url_path>')
def upload_file(url_path):
    """
    URL format: https://your-app.koyeb.app/upload/https://example.com/file.zip
    """
    if not url_path:
        return jsonify({
            "error": "URL is required",
            "usage": "Visit /upload/https://example.com/file.zip"
        }), 400
    
    # Reconstruct the full URL
    full_url = url_path
    if not full_url.startswith(('http://', 'https://')):
        full_url = 'https://' + full_url
    
    try:
        # Download file
        filepath = download_file(full_url)
        filename = safe_filename(full_url)
        
        # Upload to PixelDrain
        view_link, direct_link = upload_via_put(filepath, filename)
        
        # Clean up temporary file
        if os.path.exists(filepath):
            os.remove(filepath)
        
        # Return JSON response
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

@app.route('/api/upload', methods=['POST'])
def api_upload():
    """
    JSON API for file upload
    POST data: {"url": "https://example.com/file.zip"}
    """
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({
            "error": "URL is required in JSON body",
            "example": {"url": "https://example.com/file.zip"}
        }), 400
    
    file_url = data['url']
    
    try:
        # Download file
        filepath = download_file(file_url)
        filename = safe_filename(file_url)
        
        # Upload to PixelDrain
        view_link, direct_link = upload_via_put(filepath, filename)
        
        # Clean up temporary file
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
            "success": False,
            "error": str(e)
        }), 500

@app.route('/web', methods=['GET', 'POST'])
def web_upload():
    """
    Web form for file upload
    """
    if request.method == 'POST':
        file_url = request.form.get('file_url')
        if file_url:
            # Redirect to the direct URL method
            encoded_url = urllib.parse.quote(file_url, safe='')
            return f'''
            <script>
                window.location.href = "/upload/{encoded_url}";
            </script>
            <p>Redirecting... <a href="/upload/{encoded_url}">Click here if not redirected</a></p>
            '''
    
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>PixelDrain Uploader</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
            .container { background: #f5f5f5; padding: 20px; border-radius: 10px; }
            input[type="url"] { width: 70%; padding: 10px; margin-right: 10px; }
            button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; }
            .result { margin-top: 20px; padding: 15px; border-radius: 5px; }
            .success { background: #d4edda; color: #155724; }
            .error { background: #f8d7da; color: #721c24; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📁 PixelDrain Uploader</h1>
            <form method="post">
                <input type="url" name="file_url" placeholder="Enter file URL (e.g., https://example.com/file.zip)" required>
                <button type="submit">Upload to PixelDrain</button>
            </form>
            <p><strong>Usage:</strong> You can also directly visit:<br>
            <code>https://your-app.koyeb.app/upload/https://example.com/file.zip</code></p>
        </div>
    </body>
    </html>
    '''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
