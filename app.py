import os
import re
import requests
import tempfile
import urllib.parse
import time
import threading
from flask import Flask, request, jsonify
from collections import OrderedDict
import hashlib

app = Flask(__name__)

# কনফিগারেশন
PIXELDRAIN_API_KEY = "2a112291-e9f6-42a3-a03e-9b49b14d68e6"
WORKER_URL = "https://cinedrive.blmbd.workers.dev/direct.aspx"
CACHE_DURATION = 1800  # 30 minutes in seconds

# গ্লোবাল স্টোরেজ
cache_store = {}
status_cache_store = {}
processing_queue = OrderedDict()
current_processing = None
queue_lock = threading.Lock()

class ProcessingStatus:
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    ERROR = "error"

def safe_filename(url):
    """সেফ ফাইলনেম জেনারেট করা"""
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path) or "file"
    name = urllib.parse.unquote(name)
    
    # স্পেশাল ক্যারেক্টার রিমুভ করা
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    
    if len(name) > 255:
        name = name[:255]
    return name

def is_google_drive_url(url):
    """গুগল ড্রাইভ URL ডিটেক্ট করা"""
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
    """গুগল ড্রাইভ URL কনভার্ট করা"""
    return f"{WORKER_URL}?id={file_id}"

def generate_file_key(url):
    """ইউনিক ফাইল কিং জেনারেট করা"""
    google_drive_id = is_google_drive_url(url)
    if google_drive_id:
        return f"gd_{google_drive_id}"
    
    filename = safe_filename(url)
    file_hash = hashlib.md5(f"{url}_{filename}".encode()).hexdigest()
    return f"file_{file_hash}"

def update_status_cache(url, status_data):
    """স্ট্যাটাস ক্যাশে আপডেট করা"""
    status_cache_store[url] = {
        **status_data,
        'cache_timestamp': time.time(),
        'expires_at': time.time() + CACHE_DURATION
    }

def get_status_cache(url):
    """স্ট্যাটাস ক্যাশে থেকে ডাটা নেওয়া"""
    if url in status_cache_store:
        cache_data = status_cache_store[url]
        if time.time() < cache_data['expires_at']:
            return cache_data
        else:
            del status_cache_store[url]
    return None

def update_processing_status(url, status, progress=0, message="", result=None, error=None):
    """প্রসেসিং স্ট্যাটাস আপডেট করা"""
    status_data = {
        'url': url,
        'status': status,
        'progress': progress,
        'message': message,
        'last_updated': time.time(),
        'timestamp': time.time()
    }
    
    if result:
        status_data['result'] = result
    if error:
        status_data['error'] = error
    
    # ক্যাশে সেভ করা
    update_status_cache(url, status_data)
    
    return status_data

def download_file_with_progress(url, status_url):
    """প্রোগ্রেস ট্র্যাকিং সহ ডাউনলোড"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'identity',  # Progress tracking এর জন্য
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
    
    update_processing_status(status_url, ProcessingStatus.DOWNLOADING, 10, 
                           "🔗 Connecting to download source...")
    
    try:
        resp = requests.get(url, stream=True, timeout=60, headers=headers)
        resp.raise_for_status()
        
        # ফাইল ইনফো
        total_size = int(resp.headers.get('content-length', 0))
        filename = None
        
        if 'Content-Disposition' in resp.headers:
            content_disposition = resp.headers['Content-Disposition']
            filename_match = re.findall('filename="(.+)"', content_disposition)
            if filename_match:
                filename = filename_match[0]
        
        # টেম্প ফাইল তৈরি
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=filename or '')
        downloaded_size = 0
        chunk_size = 8192 * 4  # Larger chunks for better performance
        
        update_processing_status(status_url, ProcessingStatus.DOWNLOADING, 20, 
                               "📥 Starting download...")
        
        for chunk in resp.iter_content(chunk_size=chunk_size):
            if chunk:
                tmp.write(chunk)
                downloaded_size += len(chunk)
                
                # প্রোগ্রেস আপডেট
                if total_size > 0:
                    progress = 20 + (downloaded_size / total_size) * 50
                    status_message = f"📥 Downloading: {downloaded_size/(1024*1024):.1f}MB / {total_size/(1024*1024):.1f}MB ({progress:.0f}%)"
                    
                    # প্রতি 5% এ আপডেট করবে
                    if int(progress) % 5 == 0 or downloaded_size == total_size:
                        update_processing_status(status_url, ProcessingStatus.DOWNLOADING, 
                                               int(progress), status_message)
                else:
                    # Unknown size
                    progress = 20 + min(50, downloaded_size % 50)
                    status_message = f"📥 Downloading: {downloaded_size/(1024*1024):.1f}MB"
                    update_processing_status(status_url, ProcessingStatus.DOWNLOADING, 
                                           progress, status_message)
        
        tmp.close()
        
        # ডাউনলোড কমপ্লিট
        final_message = f"✅ Download completed: {downloaded_size/(1024*1024):.1f}MB"
        update_processing_status(status_url, ProcessingStatus.UPLOADING, 70, final_message)
        
        return tmp.name, filename
        
    except Exception as e:
        if 'tmp' in locals():
            tmp.close()
            if os.path.exists(tmp.name):
                os.remove(tmp.name)
        raise e

def upload_via_put(filepath, filename, status_url):
    """প্রোগ্রেস ট্র্যাকিং সহ আপলোড"""
    quoted = urllib.parse.quote(filename, safe='')
    url = f"https://pixeldrain.com/api/file/{quoted}"
    auth = ("", PIXELDRAIN_API_KEY) if PIXELDRAIN_API_KEY else None
    
    file_size = os.path.getsize(filepath)
    uploaded_size = 0
    
    update_processing_status(status_url, ProcessingStatus.UPLOADING, 70, 
                           "📤 Preparing upload...")
    
    def read_with_progress(file_object, chunk_size=8192 * 4):
        nonlocal uploaded_size
        while True:
            data = file_object.read(chunk_size)
            if not data:
                break
            uploaded_size += len(data)
            
            # প্রোগ্রেস আপডেট
            if file_size > 0:
                progress = 70 + (uploaded_size / file_size) * 25
                status_message = f"📤 Uploading: {uploaded_size/(1024*1024):.1f}MB / {file_size/(1024*1024):.1f}MB ({progress:.0f}%)"
                
                # প্রতি 5% এ আপডেট
                if int(progress) % 5 == 0 or uploaded_size == file_size:
                    update_processing_status(status_url, ProcessingStatus.UPLOADING, 
                                           int(progress), status_message)
            
            yield data
    
    try:
        with open(filepath, "rb") as f:
            resp = requests.put(url, data=read_with_progress(f), auth=auth, 
                               headers={"Content-Type": "application/octet-stream"}, 
                               timeout=300)
        
        resp.raise_for_status()
        j = resp.json()
        
        if "id" in j:
            file_id = j['id']
            view_link = f"https://pixeldrain.com/u/{file_id}"
            direct_link = f"https://pixeldrain.com/api/file/{file_id}?download"
            return view_link, direct_link
        else:
            raise RuntimeError(f"Upload response parsing failed: {resp.text}")
            
    except Exception as e:
        raise e

def process_queue():
    """কিউ প্রসেসিং থ্রেড"""
    global current_processing
    
    while True:
        with queue_lock:
            if processing_queue and current_processing is None:
                current_processing = next(iter(processing_queue.keys()))
                url = current_processing
                processing_queue.pop(url, None)
            else:
                current_processing = None
                time.sleep(2)
                continue
        
        if url:
            try:
                # প্রসেসিং শুরু
                update_processing_status(url, ProcessingStatus.DOWNLOADING, 5, 
                                       "🚀 Processing started...")
                
                # গুগল ড্রাইভ ডিটেক্ট
                google_drive_id = is_google_drive_url(url)
                download_url = url
                is_google_drive = False
                
                if google_drive_id:
                    is_google_drive = True
                    download_url = convert_to_worker_url(google_drive_id)
                    update_processing_status(url, ProcessingStatus.DOWNLOADING, 8, 
                                           "🔍 Google Drive link detected")
                
                # ডাউনলোড
                filepath, detected_filename = download_file_with_progress(download_url, url)
                
                # ফাইলনেম সেট
                if detected_filename:
                    filename = detected_filename
                elif is_google_drive:
                    filename = f"google_drive_{google_drive_id}.bin"
                else:
                    filename = safe_filename(url)
                
                # আপলোড
                view_link, direct_link = upload_via_put(filepath, filename, url)
                
                # ক্লিনআপ
                if os.path.exists(filepath):
                    os.remove(filepath)
                
                # ফাইনাল রেজাল্ট
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
                
                # মেইন ক্যাশে সেভ
                cache_store[url] = {
                    'response': response_data,
                    'timestamp': time.time(),
                    'expires_at': time.time() + CACHE_DURATION
                }
                
                # স্ট্যাটাস ক্যাশে সেভ
                update_processing_status(url, ProcessingStatus.COMPLETED, 100, 
                                       "✅ Upload completed successfully", response_data)
                
            except Exception as e:
                error_msg = f"❌ Processing failed: {str(e)}"
                update_processing_status(url, ProcessingStatus.ERROR, 0, error_msg, error=error_msg)
                
            finally:
                with queue_lock:
                    if current_processing == url:
                        current_processing = None
        
        time.sleep(1)

# কিউ প্রসেসিং থ্রেড শুরু
processing_thread = threading.Thread(target=process_queue, daemon=True)
processing_thread.start()

@app.route('/')
def home():
    """হোম পেজ"""
    with queue_lock:
        queue_info = {
            "current_processing": current_processing,
            "queue_size": len(processing_queue),
            "queued_items": list(processing_queue.keys())[:5]  # First 5 items
        }
    
    return jsonify({
        "service": "🚀 Advanced PixelDrain Uploader",
        "version": "2.0.0",
        "features": [
            "Smart Queue System",
            "Duplicate Request Filter", 
            "Real-time Progress Tracking",
            "Dual Cache System",
            "Google Drive Support",
            "Professional Error Handling"
        ],
        "usage": "GET https://your-domain.koyeb.app/your-file-url",
        "status_endpoint": "GET https://your-domain.koyeb.app/your-file-url/status",
        "queue_info": queue_info,
        "cache_info": {
            "result_cache": len(cache_store),
            "status_cache": len(status_cache_store),
            "cache_duration_minutes": CACHE_DURATION // 60
        }
    })

@app.route('/health')
def health():
    """হেলথ চেক এন্ডপয়েন্ট"""
    with queue_lock:
        health_info = {
            "status": "healthy",
            "timestamp": time.time(),
            "current_processing": current_processing,
            "queue_size": len(processing_queue),
            "cache_sizes": {
                "results": len(cache_store),
                "status": len(status_cache_store)
            }
        }
    
    return jsonify(health_info)

@app.route('/stats')
def stats():
    """স্ট্যাটিসটিক্স এন্ডপয়েন্ট"""
    with queue_lock:
        stats_info = {
            "total_processed": len([s for s in status_cache_store.values() if s.get('status') == ProcessingStatus.COMPLETED]),
            "total_errors": len([s for s in status_cache_store.values() if s.get('status') == ProcessingStatus.ERROR]),
            "current_queue": len(processing_queue),
            "cache_hit_ratio": f"{(len(cache_store) / (len(status_cache_store) + 1)) * 100:.1f}%",
            "uptime": "N/A"  # You can add uptime calculation
        }
    
    return jsonify(stats_info)

@app.route('/<path:url_path>')
def upload_file(url_path):
    """মেইন আপলোড এন্ডপয়েন্ট"""
    if not url_path:
        return jsonify({"error": "URL path is required"}), 400
    
    # URL রিকনস্ট্রাক্ট
    if not url_path.startswith(('http://', 'https://')):
        full_url = 'https://' + url_path
    else:
        full_url = url_path

    # ক্যাশে চেক
    if full_url in cache_store:
        cache_data = cache_store[full_url]
        if time.time() < cache_data['expires_at']:
            response_data = cache_data['response'].copy()
            response_data['cached'] = True
            response_data['cache_expires_in'] = int(cache_data['expires_at'] - time.time())
            return jsonify(response_data)

    with queue_lock:
        # ডুপ্লিকেট চেক
        file_key = generate_file_key(full_url)
        all_processing_urls = list(processing_queue.keys()) + ([current_processing] if current_processing else [])
        
        for processing_url in all_processing_urls:
            if generate_file_key(processing_url) == file_key:
                queue_position = list(processing_queue.keys()).index(processing_url) + 1 if processing_url in processing_queue else 0
                return jsonify({
                    "success": False,
                    "error": "This file is already being processed",
                    "status": "duplicate",
                    "queue_position": queue_position,
                    "check_status": f"{request.host_url.rstrip('/')}/{urllib.parse.quote(full_url, safe='')}/status",
                    "message": f"File is already in processing queue (position: {queue_position})"
                }), 409
        
        # কিউতে এড
        if full_url not in processing_queue and full_url != current_processing:
            processing_queue[full_url] = True
            queue_position = len(processing_queue)
            
            # স্ট্যাটাস ইনিশিয়ালাইজ
            update_processing_status(full_url, ProcessingStatus.QUEUED, 0, 
                                   f"⏳ Waiting in queue (position: {queue_position})")
        else:
            queue_position = list(processing_queue.keys()).index(full_url) + 1 if full_url in processing_queue else 0

    return jsonify({
        "success": True,
        "status": "queued",
        "queue_position": queue_position,
        "estimated_wait_time": queue_position * 120,  # 2 minutes per file estimate
        "check_status": f"{request.host_url.rstrip('/')}/{urllib.parse.quote(full_url, safe='')}/status",
        "message": f"✅ File added to processing queue. Position: {queue_position}",
        "note": "Use the status endpoint to track progress"
    })

@app.route('/<path:url_path>/status')
def check_status(url_path):
    """স্ট্যাটাস চেক এন্ডপয়েন্ট"""
    if not url_path:
        return jsonify({"error": "URL path is required"}), 400
    
    # URL রিকনস্ট্রাক্ট
    if not url_path.startswith(('http://', 'https://')):
        full_url = 'https://' + url_path
    else:
        full_url = url_path

    # স্ট্যাটাস ক্যাশে চেক
    status_data = get_status_cache(full_url)
    if status_data:
        response_data = {
            "url": full_url,
            "status": status_data['status'],
            "progress": status_data['progress'],
            "message": status_data['message'],
            "last_updated": status_data['last_updated'],
            "cache_expires_in": int(status_data['expires_at'] - time.time())
        }
        
        # কিউ পজিশন
        with queue_lock:
            if full_url in processing_queue:
                response_data["queue_position"] = list(processing_queue.keys()).index(full_url) + 1
            elif full_url == current_processing:
                response_data["queue_position"] = 0
        
        if status_data['status'] == ProcessingStatus.COMPLETED and status_data.get('result'):
            response_data["result"] = status_data['result']
            response_data["cached"] = True
            
        elif status_data['status'] == ProcessingStatus.ERROR:
            response_data["error"] = status_data.get('error', 'Unknown error occurred')
        
        return jsonify(response_data)

    # রেজাল্ট ক্যাশে চেক
    if full_url in cache_store:
        cache_data = cache_store[full_url]
        if time.time() < cache_data['expires_at']:
            return jsonify({
                "url": full_url,
                "status": ProcessingStatus.COMPLETED,
                "progress": 100,
                "message": "✅ File is available from cache",
                "result": cache_data['response'],
                "cached": True,
                "cache_expires_in": int(cache_data['expires_at'] - time.time())
            })

    # কিউ স্ট্যাটাস চেক
    with queue_lock:
        if full_url in processing_queue:
            queue_position = list(processing_queue.keys()).index(full_url) + 1
            return jsonify({
                "url": full_url,
                "status": ProcessingStatus.QUEUED,
                "progress": 0,
                "message": f"⏳ Waiting in queue (position: {queue_position})",
                "queue_position": queue_position,
                "estimated_wait_time": queue_position * 120
            })
        elif full_url == current_processing:
            return jsonify({
                "url": full_url,
                "status": ProcessingStatus.DOWNLOADING,
                "progress": 0,
                "message": "🔄 File is currently being processed",
                "queue_position": 0
            })

    return jsonify({
        "error": "URL not found",
        "message": "This URL has not been submitted for processing or the cache has expired",
        "solution": "Submit the URL first using the main endpoint"
    }), 404

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Endpoint not found",
        "usage": {
            "upload": "GET /https://example.com/file.zip",
            "status": "GET /https://example.com/file.zip/status", 
            "stats": "GET /stats",
            "health": "GET /health"
        },
        "example": "https://your-domain.koyeb.app/https://drive.google.com/file/d/1idVjz4I5G6EbH2F-2SwXvFRqdhIIyVZe/view",
        "supported_google_drive_formats": [
            "https://drive.google.com/file/d/FILE_ID/view",
            "https://drive.google.com/uc?id=FILE_ID", 
            "https://drive.google.com/open?id=FILE_ID",
            "https://drive.google.com/uc?export=download&id=FILE_ID"
        ]
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "error": "Internal server error",
        "message": "Something went wrong on our side",
        "solution": "Please try again later or check the status endpoint"
    }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
