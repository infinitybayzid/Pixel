import os
import re
import requests
import tempfile
import urllib.parse
import queue
import threading
from flask import Flask, request, jsonify

app = Flask(__name__)

# ================== CONFIG ================== #
PIXELDRAIN_API_KEY = "2a112291-e9f6-42a3-a03e-9b49b14d68e6"
WORKER_URL = "https://cinedrive.blmbd.workers.dev/direct.aspx"

# ================== GLOBALS ================= #
task_queue = queue.Queue()
task_status = {}   # task_id -> {"filename":..., "status":..., "progress":..., "message":...}
processing_set = set()   # prevent duplicates

# =============== HELPERS ==================== #
def safe_filename(url):
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path) or "file"
    name = urllib.parse.unquote(name)
    if len(name) > 255:
        name = name[:255]
    return name

def is_google_drive_url(url):
    patterns = [
        r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
        r'https?://drive\.google\.com/uc\?id=([a-zA-Z0-9_-]+)',
        r'https?://docs\.google\.com/uc\?id=([a-zA-Z0-9_-]+)',
        r'https?://drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)',
        r'https?://drive\.google\.com/uc\?export=download&id=([a-zA-Z0-9_-]+)'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def convert_to_worker_url(file_id):
    return f"{WORKER_URL}?id={file_id}"

# ============ CORE FUNCTIONS =============== #
def download_file(url, task_id):
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Accept': '*/*'
    }
    if 'workers.dev' in url:
        headers.update({
            'Origin': 'https://drive.google.com',
        })
    resp = requests.get(url, stream=True, timeout=60, headers=headers)
    resp.raise_for_status()

    total = int(resp.headers.get("Content-Length", 0))
    downloaded = 0
    filename = None
    if 'Content-Disposition' in resp.headers:
        match = re.findall('filename="(.+)"', resp.headers['Content-Disposition'])
        if match:
            filename = match[0]

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=filename or '')
    for chunk in resp.iter_content(chunk_size=8192):
        if chunk:
            tmp.write(chunk)
            downloaded += len(chunk)
            if total:
                progress = int(downloaded / total * 100)
                task_status[task_id]["status"] = "downloading"
                task_status[task_id]["progress"] = progress
                task_status[task_id]["message"] = f"Downloading {progress}%"
    tmp.close()
    return tmp.name, filename

def upload_via_put(filepath, filename, task_id):
    quoted = urllib.parse.quote(filename, safe='')
    url = f"https://pixeldrain.com/api/file/{quoted}"
    auth = None
    if PIXELDRAIN_API_KEY:
        auth = ("", PIXELDRAIN_API_KEY)

    total = os.path.getsize(filepath)
    uploaded = 0

    with open(filepath, "rb") as f:
        with requests.put(url, data=f, auth=auth,
                          headers={"Content-Type": "application/octet-stream"},
                          timeout=300, stream=True) as resp:
            resp.raise_for_status()
            j = resp.json()
    if "id" in j:
        file_id = j['id']
        return {
            "view_link": f"https://pixeldrain.com/u/{file_id}",
            "direct_link": f"https://pixeldrain.com/api/file/{file_id}?download"
        }
    else:
        raise RuntimeError(f"Upload error: {resp.text}")

# ============ BACKGROUND WORKER ============= #
def worker():
    while True:
        task_id, url = task_queue.get()
        try:
            task_status[task_id]["status"] = "starting"
            task_status[task_id]["progress"] = 0
            task_status[task_id]["message"] = "Starting download..."

            google_drive_id = is_google_drive_url(url)
            is_google_drive = False
            download_url = url
            if google_drive_id:
                is_google_drive = True
                download_url = convert_to_worker_url(google_drive_id)

            filepath, detected_filename = download_file(download_url, task_id)
            filename = detected_filename or safe_filename(url)

            task_status[task_id]["status"] = "uploading"
            task_status[task_id]["progress"] = 0
            task_status[task_id]["message"] = "Uploading..."

            result = upload_via_put(filepath, filename, task_id)

            task_status[task_id].update({
                "status": "completed",
                "progress": 100,
                "message": "Completed successfully",
                "filename": filename,
                "view_link": result["view_link"],
                "direct_link": result["direct_link"]
            })

            if os.path.exists(filepath):
                os.remove(filepath)

        except Exception as e:
            task_status[task_id]["status"] = "error"
            task_status[task_id]["message"] = str(e)
        finally:
            processing_set.remove(task_id)
            task_queue.task_done()

threading.Thread(target=worker, daemon=True).start()

# =============== ROUTES ==================== #
@app.route("/")
def home():
    return jsonify({
        "service": "PixelDrain Uploader with Google Drive Support",
        "usage": "https://your-domain/<url>",
        "example": "https://example.com/https://drive.google.com/file/d/FILE_ID/view"
    })

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

@app.route("/<path:url_path>")
def enqueue(url_path):
    if not url_path.startswith(('http://', 'https://')):
        full_url = 'https://' + url_path
    else:
        full_url = url_path

    task_id = safe_filename(full_url)

    if task_id in processing_set:
        return jsonify({"success": False, "message": "This file is already being processed"}), 400

    processing_set.add(task_id)
    task_status[task_id] = {"status": "queued", "progress": 0, "message": "Waiting in queue"}
    task_queue.put((task_id, full_url))

    return jsonify({"success": True, "task_id": task_id, "status_url": f"/status/{task_id}"})

@app.route("/status/<task_id>")
def status(task_id):
    if task_id not in task_status:
        return jsonify({"error": "Task not found"}), 404
    s = task_status[task_id]
    s["waiting_in_queue"] = task_queue.qsize()
    return jsonify(s)

# =========================================== #
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
