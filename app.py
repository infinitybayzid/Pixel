import os
import re
import requests
import tempfile
import urllib.parse
import time
import threading
from flask import Flask, request, jsonify
from collections import OrderedDict

app = Flask(__name__)

# আপনার মূল কোড থেকে কনফিগারেশন
PIXELDRAIN_API_KEY = "2a112291-e9f6-42a3-a03e-9b49b14d68e6"
WORKER_URL = "https://cinedrive.blmbd.workers.dev/direct.aspx"

# গ্লোবাল ভেরিয়েবলস
cache_store = {}
processing_queue = OrderedDict()
current_processing = None
queue_lock = threading.Lock()
status_store = {}

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
            return match.group(1)
    return None

def convert_to_worker_url(file_id):
    """গুগল ড্রাইভ ফাইল ID কে ওয়ার্কার URL এ কনভার্ট করা"""
    return f"{WORKER_URL}?id={file_id}"

def get_file_key(url):
    """ইউনিক ফাইল আইডেন্টিফায়ার তৈরি করা"""
    google_drive_id = is_google_drive_url(url)
    if google_drive_id:
        return f"gd_{google_drive_id}"
    
    # সাধারণ URL এর জন্য ফাইলের নাম ব্যবহার
    filename = safe_filename(url)
    return f"file_{hash(filename)}"

def update_status(url, status, progress=0, message="", result=None):
    """স্ট্যাটাস আপডেট করা"""
    status_store[url] = {
        'status': status,
        'progress': progress,
        'message': message,
        'result': result,
        'last_updated': time.time(),
        'timestamp': time.time()
    }

def download_file_with_progress(url, status_url):
    """প্রোগ্রেস সহ ডাউনলোড ফাংশন"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Referer': 'https://drive.google.com/'
    }
    
    if 'workers.dev' in url:
        headers.update({
            'Origin': 'https://drive.google.com',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site'
        })
    
    update_status(status_url, "downloading", 10, "Starting download...")
    
    resp = requests.get(url, stream=True, timeout=60, headers=headers)
    resp.raise_for_status()
    
    # ফাইলের সাইজ জানা থাকলে প্রোগ্রেস ক্যালকুলেট করা
    total_size = int(resp.headers.get('content-length', 0))
    
    filename = None
    if 'Content-Disposition' in resp.headers:
        content_disposition = resp.headers['Content-Disposition']
        filename_match = re.findall('filename="(.+)"', content_disposition)
        if filename_match:
            filename = filename_match[0]
    
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=filename or '')
    downloaded_size = 0
    
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                tmp.write(chunk)
                downloaded_size += len(chunk)
                
                # প্রোগ্রেস আপডেট (30-70% পর্যন্ত)
                if total_size > 0:
                    progress = 30 + (downloaded_size / total_size) * 40
                    update_status(status_url, "downloading", int(progress), 
                                 f"Downloading: {downloaded_size/(1024*1024):.1f}MB / {total_size/(1024*1024):.1f}MB")
                else:
                    progress = 30 + (downloaded_size % 30)  # আননোন সাইজের জন্য
                    update_status(status_url, "downloading", progress, 
                                 f"Downloading: {downloaded_size/(1024*1024):.1f}MB")
        
        tmp.close()
        update_status(status_url, "uploading", 70, "Download completed, starting upload...")
        return tmp.name, filename
        
    except Exception as e:
        tmp.close()
        if os.path.exists(tmp.name):
            os.remove(tmp.name)
        raise e

def upload_via_put(filepath, filename, status_url):
    """আপলোড ফাংশন"""
    quoted = urllib.parse.quote(filename, safe='')
    url = f"https://pixeldrain.com/api/file/{quoted}"
    auth = None
    if PIXELDRAIN_API_KEY:
        auth = ("", PIXELDRAIN_API_KEY)
    
    file_size = os.path.getsize(filepath)
    uploaded_size = 0
    
    def read_in_chunks(file_object, chunk_size=8192):
        nonlocal uploaded_size
        while True:
            data = file_object.read(chunk_size)
            if not data:
                break
            uploaded_size += len(data)
            
            # প্রোগ্রেস আপডেট (70-95% পর্যন্ত)
            if file_size > 0:
                progress = 70 + (uploaded_size / file_size) * 25
                update_status(status_url, "uploading", int(progress),
                             f"Uploading: {uploaded_size/(1024*1024):.1f}MB / {file_size/(1024*1024):.1f}MB")
            
            yield data
    
    with open(filepath, "rb") as f:
        resp = requests.put(url, data=read_in_chunks(f), auth=auth, 
                           headers={"Content-Type":"application/octet-stream"}, timeout=300)
    
    resp.raise_for_status()
    j = resp.json()
    
    if "id" in j:
        file_id = j['id']
        view_link = f"https://pixeldrain.com/u/{file_id}"
        direct_link = f"https://pixeldrain.com/api/file/{file_id}?download"
        return view_link, direct_link
    else:
        raise RuntimeError(f"Could not parse upload response: {resp.text}")

def process_queue():
    """কিউ প্রসেসিং থ্রেড"""
    global current_processing
    
    while True:
        with queue_lock:
            if processing_queue and current_processing is None:
                current_processing = next(iter(processing_queue.keys()))
                url, callback = processing_queue.popitem(last=False)
            else:
                current_processing = None
                time.sleep(1)
                continue
        
        if url:
            try:
                # প্রসেসিং শুরু
                update_status(url, "processing", 5, "Starting processing...")
                
                # গুগল ড্রাইভ URL ডিটেক্ট করা
                google_drive_id = is_google_drive_url(url)
                download_url = url
                is_google_drive = False
                
                if google_drive_id:
                    is_google_drive = True
                    download_url = convert_to_worker_url(google_drive_id)
                
                # ডাউনলোড ফাইল
                filepath, detected_filename = download_file_with_progress(download_url, url)
                
                # ফাইলের নাম নির্ধারণ
                if detected_filename:
                    filename = detected_filename
                elif is_google_drive:
                    filename = f"google_drive_{google_drive_id}.bin"
                else:
                    filename = safe_filename(url)
                
                # আপলোড ফাইল
                view_link, direct_link = upload_via_put(filepath, filename, url)
                
                # টেম্প ফাইল ডিলিট
                if os.path.exists(filepath):
                    os.remove(filepath)
                
                # রেজাল্ট প্রস্তুত
                response_data = {
                    "success": True,
                    "original_url": url,
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
                cache_store[url] = {
                    'response': response_data,
                    'timestamp': time.time()
                }
                
                # স্ট্যাটাস আপডেট
                update_status(url, "completed", 100, "Upload completed successfully", response_data)
                
            except Exception as e:
                error_msg = str(e)
                update_status(url, "error", 0, f"Processing failed: {error_msg}")
                
            finally:
                with queue_lock:
                    current_processing = None
        
        time.sleep(1)

# কিউ প্রসেসিং থ্রেড শুরু করুন
processing_thread = threading.Thread(target=process_queue, daemon=True)
processing_thread.start()

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
    with queue_lock:
        queue_info = {
            "current_processing": current_processing,
            "queue_size": len(processing_queue),
            "queued_items": list(processing_queue.keys())
        }
    
    return jsonify({
        "status": "healthy",
        "cache_size": len(cache_store),
        "queue_info": queue_info,
        "timestamp": time.time()
    })

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
    if full_url in cache_store:
        cache_data = cache_store[full_url]
        if time.time() - cache_data['timestamp'] < 1800:  # 30 minutes
            response_data = cache_data['response']
            response_data['cached'] = True
            return jsonify(response_data)

    with queue_lock:
        # ডুপ্লিকেট রিকোয়েস্ট চেক
        file_key = get_file_key(full_url)
        for queued_url in list(processing_queue.keys()) + ([current_processing] if current_processing else []):
            if get_file_key(queued_url) == file_key:
                return jsonify({
                    "success": False,
                    "error": "This file is already in queue or being processed",
                    "status": "duplicate",
                    "queue_position": list(processing_queue.keys()).index(queued_url) + 1 if queued_url in processing_queue else 0,
                    "message": "Please wait for the current processing to complete"
                }), 409
        
        # কিউতে যোগ করা
        if full_url not in processing_queue and full_url != current_processing:
            processing_queue[full_url] = None
            queue_position = len(processing_queue)
            
            # স্ট্যাটাস ইনিশিয়ালাইজ
            update_status(full_url, "queued", 0, f"Waiting in queue (position: {queue_position})")
        else:
            queue_position = list(processing_queue.keys()).index(full_url) + 1 if full_url in processing_queue else 0

    return jsonify({
        "success": True,
        "status": "queued",
        "queue_position": queue_position,
        "message": f"File added to processing queue. Position: {queue_position}",
        "check_status": f"{request.host_url}{full_url}/status"
    })

@app.route('/<path:url_path>/status')
def check_status(url_path):
    """ফাইলের স্ট্যাটাস চেক করা"""
    if not url_path:
        return jsonify({"error": "URL path is required"}), 400
    
    # URL রিকনস্ট্রাক্ট করা
    if not url_path.startswith(('http://', 'https://')):
        full_url = 'https://' + url_path
    else:
        full_url = url_path

    # স্ট্যাটাস চেক
    if full_url in status_store:
        status_data = status_store[full_url]
        
        # কিউ পজিশন চেক
        queue_position = None
        with queue_lock:
            if full_url in processing_queue:
                queue_position = list(processing_queue.keys()).index(full_url) + 1
            elif full_url == current_processing:
                queue_position = 0  # Currently processing
        
        response_data = {
            "url": full_url,
            "status": status_data['status'],
            "progress": status_data['progress'],
            "message": status_data['message'],
            "last_updated": status_data['last_updated']
        }
        
        if queue_position is not None:
            response_data["queue_position"] = queue_position
        
        if status_data['status'] == 'completed' and status_data['result']:
            response_data["result"] = status_data['result']
        elif status_data['status'] == 'error':
            response_data["error"] = status_data['message']
        
        return jsonify(response_data)
    
    # ক্যাশে চেক
    elif full_url in cache_store:
        cache_data = cache_store[full_url]
        if time.time() - cache_data['timestamp'] < 1800:
            return jsonify({
                "url": full_url,
                "status": "completed",
                "progress": 100,
                "message": "File is available in cache",
                "result": cache_data['response'],
                "cached": True
            })
    
    # কিউতে আছে কিনা চেক
    with queue_lock:
        if full_url in processing_queue:
            queue_position = list(processing_queue.keys()).index(full_url) + 1
            return jsonify({
                "url": full_url,
                "status": "queued",
                "progress": 0,
                "message": f"Waiting in queue (position: {queue_position})",
                "queue_position": queue_position
            })
        elif full_url == current_processing:
            return jsonify({
                "url": full_url,
                "status": "processing",
                "progress": 0,
                "message": "File is currently being processed",
                "queue_position": 0
            })
    
    return jsonify({
        "error": "URL not found in queue or cache",
        "message": "This URL has not been submitted for processing or has expired"
    }), 404

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
