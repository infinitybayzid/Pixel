import os
import re
import requests
import tempfile
import urllib.parse
import threading
import time
import hashlib
from collections import deque
from flask import Flask, request, jsonify

app = Flask(__name__)

# আপনার মূল কোড থেকে কনফিগারেশন
PIXELDRAIN_API_KEY = "2a112291-e9f6-42a3-a03e-9b49b14d68e6"
WORKER_URL = "https://cinedrive.blmbd.workers.dev/direct.aspx"

# Global queue and lock system
request_queue = deque()
queue_lock = threading.Lock()
processing_lock = threading.Lock()
currently_processing = None

# Request tracking
pending_requests = {}  # {request_id: {status, result, url, filename}}
request_id_counter = 0
id_lock = threading.Lock()

# Duplicate detection
url_to_request_id = {}  # Track URLs to prevent duplicates

def generate_request_id():
    global request_id_counter
    with id_lock:
        request_id_counter += 1
        return f"req_{request_id_counter}_{int(time.time())}"

def get_url_hash(url):
    """Generate hash for URL to detect duplicates"""
    return hashlib.md5(url.encode()).hexdigest()

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

def download_file(url, request_id):
    """ডাউনলোড ফাংশন with progress tracking"""
    try:
        # Update status to downloading
        with queue_lock:
            if request_id in pending_requests:
                pending_requests[request_id]['status'] = 'downloading'
        
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
        
        resp = requests.get(url, stream=True, timeout=300, headers=headers)
        resp.raise_for_status()
        
        # ফাইলের নাম ডিটেক্ট করা
        filename = None
        if 'Content-Disposition' in resp.headers:
            content_disposition = resp.headers['Content-Disposition']
            filename_match = re.findall('filename="(.+)"', content_disposition)
            if filename_match:
                filename = filename_match[0]
        
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=filename or '')
        
        # Progress tracking during download
        total_size = int(resp.headers.get('content-length', 0))
        downloaded_size = 0
        
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                tmp.write(chunk)
                downloaded_size += len(chunk)
                
                # Update progress (optional - can be removed if not needed)
                if total_size > 0:
                    progress = (downloaded_size / total_size) * 100
                    with queue_lock:
                        if request_id in pending_requests:
                            pending_requests[request_id]['progress'] = f"{progress:.1f}%"
        
        tmp.close()
        return tmp.name, filename
        
    except Exception as e:
        # Clean up temporary file if exists
        if 'tmp' in locals() and os.path.exists(tmp.name):
            os.remove(tmp.name)
        raise e

def upload_via_put(filepath, filename, request_id):
    """আপলোড ফাংশন with progress tracking"""
    try:
        # Update status to uploading
        with queue_lock:
            if request_id in pending_requests:
                pending_requests[request_id]['status'] = 'uploading'
        
        quoted = urllib.parse.quote(filename, safe='')
        url = f"https://pixeldrain.com/api/file/{quoted}"
        auth = None
        if PIXELDRAIN_API_KEY:
            auth = ("", PIXELDRAIN_API_KEY)
        
        file_size = os.path.getsize(filepath)
        uploaded_size = 0
        
        def read_in_chunks(file_object, chunk_size=8192):
            while True:
                data = file_object.read(chunk_size)
                if not data:
                    break
                yield data
        
        with open(filepath, "rb") as f:
            # Create a custom generator to track upload progress
            def generate_with_progress():
                nonlocal uploaded_size
                f.seek(0)
                for chunk in read_in_chunks(f):
                    uploaded_size += len(chunk)
                    if file_size > 0:
                        progress = (uploaded_size / file_size) * 100
                        with queue_lock:
                            if request_id in pending_requests:
                                pending_requests[request_id]['progress'] = f"{progress:.1f}%"
                    yield chunk
            
            resp = requests.put(url, data=generate_with_progress(), auth=auth, 
                              headers={"Content-Type":"application/octet-stream"}, 
                              timeout=600)  # Increased timeout for large files
        
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
        raise e

def process_queue():
    """কিউ প্রসেসিং - একবারে একটি রিকোয়েস্ট প্রসেস করে"""
    global currently_processing
    
    while True:
        time.sleep(1)  # Check queue every second
        
        with queue_lock:
            if not request_queue:
                currently_processing = None
                continue
            
            # Get next request from queue
            request_id = request_queue[0]
            if request_id not in pending_requests:
                request_queue.popleft()
                continue
        
        # Process the request with lock to ensure only one at a time
        with processing_lock:
            request_data = None
            with queue_lock:
                if request_queue and request_id in pending_requests:
                    request_data = pending_requests[request_id]
                    currently_processing = request_id
                    request_queue.popleft()  # Remove from queue as we're processing it
                else:
                    continue
            
            if not request_data:
                continue
            
            try:
                # Update status to processing
                with queue_lock:
                    pending_requests[request_id]['status'] = 'processing'
                
                url = request_data['url']
                google_drive_id = request_data.get('google_drive_id')
                download_url = url
                
                # Handle Google Drive URLs
                if google_drive_id:
                    download_url = convert_to__worker_url(google_drive_id)
                
                # Download file
                filepath, detected_filename = download_file(download_url, request_id)
                
                # Determine filename
                if detected_filename:
                    filename = detected_filename
                elif google_drive_id:
                    filename = f"google_drive_{google_drive_id}.bin"
                else:
                    filename = safe_filename(url)
                
                # Upload to PixelDrain
                view_link, direct_link = upload_via_put(filepath, filename, request_id)
                
                # Clean up temp file
                if os.path.exists(filepath):
                    os.remove(filepath)
                
                # Mark as completed
                with queue_lock:
                    pending_requests[request_id].update({
                        'status': 'completed',
                        'result': {
                            "success": True,
                            "original_url": url,
                            "filename": filename,
                            "view_link": view_link,
                            "direct_download": direct_link,
                            "message": "File uploaded successfully to PixelDrain",
                            "google_drive_id": google_drive_id,
                            "worker_url_used": download_url if google_drive_id else None,
                            "source": "google_drive_via_worker" if google_drive_id else "direct"
                        }
                    })
                
                # Clean up URL tracking after successful processing
                url_hash = get_url_hash(url)
                if url_hash in url_to_request_id and url_to_request_id[url_hash] == request_id:
                    del url_to_request_id[url_hash]
                
            except requests.exceptions.RequestException as e:
                # Handle download/upload errors
                with queue_lock:
                    pending_requests[request_id].update({
                        'status': 'failed',
                        'result': {
                            "success": False,
                            "error": f"Download/Upload error: {str(e)}",
                            "original_url": url,
                            "google_drive_id": google_drive_id
                        }
                    })
                
                # Clean up URL tracking on failure
                url_hash = get_url_hash(url)
                if url_hash in url_to_request_id and url_to_request_id[url_hash] == request_id:
                    del url_to_request_id[url_hash]
                    
            except Exception as e:
                # Handle other errors
                with queue_lock:
                    pending_requests[request_id].update({
                        'status': 'failed',
                        'result': {
                            "success": False,
                            "error": f"Processing error: {str(e)}",
                            "original_url": url,
                            "google_drive_id": google_drive_id
                        }
                    })
                
                # Clean up URL tracking on failure
                url_hash = get_url_hash(url)
                if url_hash in url_to_request_id and url_to_request_id[url_hash] == request_id:
                    del url_to_request_id[url_hash]

# Start queue processing thread
queue_thread = threading.Thread(target=process_queue, daemon=True)
queue_thread.start()

@app.route('/')
def home():
    return jsonify({
        "service": "PixelDrain Uploader with Advanced Queue System",
        "usage": "Visit https://your-domain.koyeb.app/example.com",
        "example": "https://graceful-gusti-bayzid-simr-an-d83671b1.koyeb.app/https://drive.google.com/file/d/1idVjz4I5G6EbH2F-2SwXvFRqdhIIyVZe/view",
        "supported_formats": [
            "https://drive.google.com/file/d/FILE_ID/view",
            "https://drive.google.com/uc?id=FILE_ID",
            "https://drive.google.com/open?id=FILE_ID",
            "https://drive.google.com/uc?export=download&id=FILE_ID"
        ],
        "features": [
            "Queue system - processes one file at a time",
            "Duplicate detection - prevents processing same URL multiple times",
            "Progress tracking - see download/upload status",
            "Request status checking - check your request status anytime"
        ]
    })

@app.route('/health')
def health():
    queue_status = {
        "queue_length": len(request_queue),
        "currently_processing": currently_processing,
        "pending_requests": len(pending_requests)
    }
    return jsonify({"status": "healthy", "queue": queue_status})

@app.route('/status/<request_id>')
def check_status(request_id):
    """Check the status of a specific request"""
    with queue_lock:
        if request_id in pending_requests:
            status_data = pending_requests[request_id].copy()
            return jsonify(status_data)
        else:
            return jsonify({
                "error": "Request ID not found",
                "message": "The request may have been completed or expired"
            }), 404

@app.route('/queue')
def queue_status():
    """Get current queue status"""
    with queue_lock:
        queue_info = {
            "currently_processing": currently_processing,
            "queued_requests": list(request_queue),
            "pending_requests_count": len(pending_requests),
            "queue_details": []
        }
        
        # Add details for each pending request
        for req_id, req_data in pending_requests.items():
            queue_info["queue_details"].append({
                "request_id": req_id,
                "status": req_data.get('status', 'unknown'),
                "url": req_data.get('url', 'unknown'),
                "position": list(request_queue).index(req_id) if req_id in request_queue else 'processing'
            })
    
    return jsonify(queue_info)

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

    # Duplicate detection
    url_hash = get_url_hash(full_url)
    with queue_lock:
        if url_hash in url_to_request_id:
            existing_request_id = url_to_request_id[url_hash]
            if existing_request_id in pending_requests:
                existing_status = pending_requests[existing_request_id]['status']
                return jsonify({
                    "success": False,
                    "error": f"Duplicate request detected",
                    "message": "This URL is already being processed",
                    "existing_request_id": existing_request_id,
                    "existing_status": existing_status,
                    "check_status_url": f"/status/{existing_request_id}"
                }), 409

    # Generate new request
    request_id = generate_request_id()
    google_drive_id = is_google_drive_url(full_url)
    
    # Add to tracking
    with queue_lock:
        pending_requests[request_id] = {
            'status': 'queued',
            'url': full_url,
            'google_drive_id': google_drive_id,
            'filename': None,
            'progress': '0%',
            'added_time': time.time(),
            'result': None
        }
        url_to_request_id[url_hash] = request_id
        request_queue.append(request_id)
    
    # Return immediate response with request ID
    response_data = {
        "success": True,
        "message": "Request added to queue",
        "request_id": request_id,
        "queue_position": len(request_queue),
        "status": "queued",
        "check_status_url": f"/status/{request_id}",
        "original_url": full_url,
        "estimated_wait_time": f"{len(request_queue) * 2} minutes"  # Rough estimate
    }
    
    if google_drive_id:
        response_data["google_drive_id"] = google_drive_id
        response_data["source"] = "google_drive"
    
    return jsonify(response_data)

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
        ],
        "additional_endpoints": [
            "/status/<request_id> - Check request status",
            "/queue - View current queue",
            "/health - Service health check"
        ]
    }), 404

# Cleanup old requests (older than 1 hour)
def cleanup_old_requests():
    while True:
        time.sleep(3600)  # Run every hour
        current_time = time.time()
        with queue_lock:
            expired_requests = []
            for req_id, req_data in pending_requests.items():
                if current_time - req_data['added_time'] > 3600:  # 1 hour
                    expired_requests.append(req_id)
            
            for req_id in expired_requests:
                # Clean up URL tracking
                if req_id in pending_requests:
                    url_hash = get_url_hash(pending_requests[req_id]['url'])
                    if url_hash in url_to_request_id and url_to_request_id[url_hash] == req_id:
                        del url_to_request_id[url_hash]
                del pending_requests[req_id]

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_requests, daemon=True)
cleanup_thread.start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
