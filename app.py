from flask import Flask, render_template, request, jsonify, send_file, Response, after_this_request
# from flask_talisman import Talismanan
import os
import asyncio
from shazamio import Shazam
import yt_dlp
import re
import requests 
from urllib.parse import quote
import uuid
import logging
import time
import threading
from werkzeug.utils import secure_filename
import contextlib
from threading import Lock
import urllib.parse
from dotenv import load_dotenv
import subprocess
import shutil

# Load environment variables
load_dotenv()

# Dynamically find ffmpeg binary path
ffmpeg_path = os.environ.get('FFMPEG_PATH') or shutil.which("ffmpeg")
if ffmpeg_path:
    os.environ["PATH"] = os.path.dirname(ffmpeg_path) + os.pathsep + os.environ.get("PATH", "")
    os.environ["FFMPEG_BINARY"] = ffmpeg_path
else:
    logger.warning("FFmpeg not found in PATH")
    os.environ["FFMPEG_BINARY"] = "ffmpeg"

# Dynamically find ffprobe binary path
ffprobe_path = os.environ.get('FFPROBE_PATH') or shutil.which("ffprobe")
if not ffprobe_path and ffmpeg_path:
    # Look in same directory as ffmpeg
    candidate = os.path.join(os.path.dirname(ffmpeg_path), "ffprobe")
    if os.path.exists(candidate) and os.access(candidate, os.X_OK):
        ffprobe_path = candidate

if ffprobe_path:
    os.environ["PATH"] = os.path.dirname(ffprobe_path) + os.pathsep + os.environ.get("PATH", "")
    os.environ["FFPROBE_BINARY"] = ffprobe_path
else:
    logger.warning("FFprobe not found in PATH")
    os.environ["FFPROBE_BINARY"] = "ffprobe"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates', static_folder='static')

# Security headers configuration
# Talisman(app,p,
#     content_security_policy={={
#         'default-src': ["'self'", "'unsafe-inline'", "'unsafe-eval'", , 
#                        "https://cdnjs.cloudflare.com", , 
#                        "https://fonts.googleapis.com", , 
#                        "https://fonts.gstatic.com"],],
#         'media-src': ["'self'", "blob:"],],
#         'connect-src': ["'self'"],],
#         'img-src': ["'self'", "data:", "https:"],],
#         'script-src': ["'self'", "'unsafe-inline'", "'unsafe-eval'"],],
#         'style-src': ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com", , 
#                      "https://cdnjs.cloudflare.com"],],
#         'font-src': ["'self'", "https://fonts.gstatic.com", , 
#                     "https://cdnjs.cloudflare.com"]"]
#     },},
#     feature_policy={={
#         'microphone': "'self'",",
#         'autoplay': "'self'"'"
#     } }
# ) )

# Configuration
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', '/tmp/uploads')
DOWNLOAD_FOLDER = os.environ.get('DOWNLOAD_FOLDER', '/tmp/downloads')
MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))
SECRET_KEY = os.environ.get('SECRET_KEY', os.urandom(24).hex())

app.config['SECRET_KEY'] = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH

ALLOWED_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'webm'}

# Create folders if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

download_progress = {}
progress_lock = Lock()

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def check_ffmpeg():
    try:
        # Check if ffmpeg is available
        subprocess.run([ffmpeg_path or "ffmpeg", '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except FileNotFoundError:
        logger.warning("FFmpeg not found in PATH")
        return False

# Check for FFmpeg availability
if not check_ffmpeg():
    logger.warning("FFmpeg not found. Some features may not work correctly.")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/recognize', methods=['POST'])
def recognize():
    if 'audio' not in request.files:
        return jsonify({"success": False, "message": "No audio file provided"}), 400
    
    audio_file = request.files['audio']
    if audio_file.filename == '':
        return jsonify({"success": False, "message": "No audio file selected"}), 400
    
    if not allowed_file(audio_file.filename):
        return jsonify({"success": False, "message": "File type not allowed. Please upload an audio file."}), 400
    
    # Create a unique filename to prevent conflicts
    filename = secure_filename(audio_file.filename)
    unique_filename = f"{uuid.uuid4()}_{filename}"
    file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
    
    try:
        audio_file.save(file_path)
        logger.info(f"Audio saved to {file_path}")
        
        # Recognize the song
        result = asyncio.run(recognize_song(file_path))
        
        # Clean up the uploaded file
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Temporary file {file_path} removed successfully.")
        
        if result and 'track' in result:
            song_title = result['track']['title']
            artist = result['track']['subtitle']
            
            # Get YouTube link
            yt_result = search_youtube(song_title, artist)
            youtube_url = yt_result['video_url'] if yt_result else None
            
            # Get Spotify link
            spotify_url = search_spotify(song_title, artist)
            
            return jsonify({
                "success": True,
                "songTitle": song_title,
                "artist": artist,
                "message": "Song recognized successfully!",
                "youtubeUrl": youtube_url,
                "spotifyUrl": spotify_url
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": "Could not recognize the song. Please try again with a clearer recording."
            }), 400
            
    except Exception as e:
        logger.error(f"Error during recognition: {str(e)}")
        if os.path.exists(file_path):
            os.remove(file_path)
        return jsonify({
            "success": False,
            "message": "An error occurred while processing the audio."
        }), 500

@app.route('/youtube-search', methods=['POST'])
def youtube_search_endpoint():
    data = request.json
    if not data or 'title' not in data or 'artist' not in data:
        return jsonify({"success": False, "message": "Missing song information"}), 400
    
    song_title = data['title']
    artist = data['artist']
    
    try:
        result = search_youtube(song_title, artist)
        
        if not result or 'video_id' not in result:
            return jsonify({
                "success": False,
                "message": "Couldn't find this song on YouTube"
            }), 404
            
        return jsonify({
            "success": True,
            "videoId": result['video_id'],
            "videoUrl": result['video_url']
        }), 200
    
    except Exception as e:
        logger.error(f"Error during YouTube search: {str(e)}")
        return jsonify({
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }), 500

@app.route('/download', methods=['POST'])
def download():
    data = request.json
    if not data or 'title' not in data or 'artist' not in data:
        return jsonify({"success": False, "message": "Missing song information"}), 400
    
    song_title = data['title']
    artist = data['artist']
    
    try:
        # Generate a session ID for this download
        session_id = str(uuid.uuid4())
        
        # Get video ID from request if provided, otherwise search for it
        video_id = data.get('videoId')
        if not video_id:
            result = search_youtube(song_title, artist)
            if not result or 'video_id' not in result:
                return jsonify({
                    "success": False,
                    "message": "Couldn't find this song on YouTube"
                }), 404
            video_id = result['video_id']
            
        download_path = download_song(song_title, artist, session_id, video_id)
        
        if download_path and os.path.exists(download_path):
            # Return download info
            filename = os.path.basename(download_path)
            download_url = f"/get_download/{session_id}/{filename}"
            
            return jsonify({
                "success": True,
                "downloadUrl": download_url,
                "filename": filename
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": "Failed to download the song. Please try again."
            }), 500
    
    except Exception as e:
        logger.error(f"Error during download: {str(e)}")
        return jsonify({
            "success": False,
            "message": f"An error occurred: {str(e)}"
        }), 500

@app.route('/get_download/<session_id>/<filename>')
def get_download(session_id, filename):
    download_dir = os.path.join(DOWNLOAD_FOLDER, session_id)
    file_path = os.path.join(download_dir, filename)
    
    if os.path.exists(file_path):
        # Schedule cleanup of files after download completes
        @after_this_request
        def cleanup(response):
            try:
                threading.Thread(target=lambda: cleanup_download(file_path, download_dir)).start()
                logger.info(f"Scheduled cleanup for {file_path} and {download_dir}")
            except Exception as e:
                logger.error(f"Error in cleanup scheduling: {str(e)}")
            return response
            
        return send_file(
            file_path,
            as_attachment=True,
            download_name=filename
        )
    else:
        logger.warning(f"File not found: {file_path}")
        return jsonify({
            "success": False,
            "message": "File not found or already downloaded"
        }), 404
        
def cleanup_download(file_path, dir_path):
    time.sleep(60)  # 60 seconds delay
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"File {file_path} removed successfully.")
        if os.path.exists(dir_path) and not os.listdir(dir_path):
            os.rmdir(dir_path)
            logger.info(f"Directory {dir_path} removed successfully.")
    except Exception as e:
        logger.error(f"Error cleaning up files: {str(e)}")

# Song recognition function
async def recognize_song(file_path):
    shazam = Shazam()
    try:
        # Using the correct method from ShazamIO library
        out = await shazam.recognize_song(file_path)
        if out and out.get('track'):
            return out
        return None
    except Exception as e:
        logger.error(f"Error recognizing song: {str(e)}")
        return None

# Filename sanitization
def sanitize_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', "", filename)

# Search YouTube for a song and return the video ID
def search_youtube(song_title, artist):
    search_query = f'{song_title} {artist} lyrics'
    query = quote(search_query)
    
    try:
        # Search for video on YouTube
        url = f"https://www.youtube.com/results?search_query={query}"
        
        # Add a timeout to prevent hanging
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        video_id_match = re.search(r"watch\?v=(\S{11})", response.text)
        
        if not video_id_match:
            logger.warning(f"No YouTube video found for query: {search_query}")
            return None
            
        video_id = video_id_match.group(1)
        return {
            "video_id": video_id,
            "video_url": f"https://www.youtube.com/watch?v={video_id}"
        }
    except Exception as e:
        logger.error(f"Error searching YouTube: {str(e)}")
        return None

def search_spotify(song_title, artist):
    try:
        query = urllib.parse.quote(f"{song_title} {artist}")
        return f"https://open.spotify.com/search/{query}"
    except Exception as e:
        logger.error(f"Error creating Spotify search URL: {str(e)}")
        return None

# Download song function
def download_song(song_title, artist, session_id, video_id):
    try:
        # Always use the dynamically detected ffmpeg_path
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        download_dir = os.path.join(DOWNLOAD_FOLDER, session_id)
        os.makedirs(download_dir, exist_ok=True)

        sanitized_title = sanitize_filename(f"{song_title} - {artist}")
        output_template = os.path.join(download_dir, f"{sanitized_title}.%(ext)s")

        def progress_hook(d):
            if d['status'] == 'downloading':
                with progress_lock:
                    download_progress[session_id] = {
                        'progress': d.get('downloaded_bytes', 0) / d.get('total_bytes', 1) * 100 if d.get('total_bytes') else 0,
                        'speed': d.get('speed', 0),
                        'eta': d.get('eta', 0)
                    }

        ydl_opts = {
            'format': 'bestaudio',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'progress_hooks': [progress_hook],
            'prefer_ffmpeg': True,
            'ffmpeg_location': ffmpeg_path or "ffmpeg",
            'socket_timeout': 30,
            'no_warnings': True,
            'quiet': True,
            'extract_audio': True,
            'audio_format': 'mp3',
            'audio_quality': '192K',
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'geo_bypass': True,
            'force_generic_extractor': False
        }

        logger.info(f"Starting download for {video_url}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info_dict = ydl.extract_info(video_url, download=True)
                if not info_dict:
                    logger.error("No video information extracted")
                    return None

                downloaded_file = os.path.splitext(ydl.prepare_filename(info_dict))[0] + ".mp3"

                if not os.path.exists(downloaded_file):
                    logger.error(f"Downloaded file not found at {downloaded_file}")
                    return None

                logger.info(f"Successfully downloaded to {downloaded_file}")
                return downloaded_file

            except Exception as e:
                logger.error(f"YoutubeDL error: {str(e)}")
                return None

    except Exception as e:
        logger.error(f"Error during song download: {str(e)}")
        return None
    finally:
        with progress_lock:
            if session_id in download_progress:
                del download_progress[session_id]

@app.route('/download-progress/<session_id>')
def get_download_progress(session_id):
    with progress_lock:
        progress = download_progress.get(session_id, {})
    return jsonify(progress)

# Periodic cleanup function (can be executed with a scheduler in production)
def cleanup_old_downloads():
    try:
        for root, dirs, files in os.walk(DOWNLOAD_FOLDER):
            for file in files:
                file_path = os.path.join(root, file)
                # Remove files older than 1 hour
                if os.path.isfile(file_path) and (time.time() - os.path.getmtime(file_path)) > 3600:
                    os.remove(file_path)
        
        # Remove empty directories
        for root, dirs, files in os.walk(DOWNLOAD_FOLDER, topdown=False):
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")

@app.route('/healthz')
def healthz():
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    if os.environ.get('RAILWAY_ENVIRONMENT') == 'production':
        from waitress import serve
        serve(app, host='0.0.0.0', port=port)
    else:
        app.run(host='0.0.0.0', port=port, debug=False)