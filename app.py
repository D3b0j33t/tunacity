from flask import Flask, render_template, request, jsonify, send_file, after_this_request
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
from threading import Lock
import urllib.parse
from dotenv import load_dotenv
import subprocess
import shutil
import sqlite3
from datetime import datetime
from waitress import serve
import tempfile
import psutil
import hashlib
from functools import wraps
from werkzeug.security import safe_join

# Configure logging first before any other initialization
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Railway-specific configuration
if os.environ.get('RAILWAY_ENVIRONMENT') == 'production':
    UPLOAD_FOLDER = '/tmp'  # Use Railway's temporary directory
    DOWNLOAD_FOLDER = '/tmp'
    ffmpeg_path = "/root/.nix-profile/bin/ffmpeg"  # Updated path from logs
    ffprobe_path = "/root/.nix-profile/bin/ffprobe"  # Updated path from logs
    
    # Set additional Railway-specific configurations
    os.environ['FFMPEG_BINARY'] = ffmpeg_path
    os.environ['FFPROBE_BINARY'] = ffprobe_path
    
    # Configure for Railway's environment
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PREFERRED_URL_SCHEME='https'
    )
    
    # Configure logging for Railway
    logging.getLogger('waitress').setLevel(logging.INFO)
else:
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', './tmp/uploads')
    DOWNLOAD_FOLDER = os.environ.get('DOWNLOAD_FOLDER', './tmp/downloads')
    ffmpeg_path = os.environ.get('FFMPEG_PATH') or shutil.which("ffmpeg")
    ffprobe_path = os.environ.get('FFPROBE_PATH') or shutil.which("ffprobe")

# Create folders if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Ensure binary paths are set
if ffmpeg_path:
    os.environ["FFMPEG_BINARY"] = ffmpeg_path
    os.environ["PATH"] = os.path.dirname(ffmpeg_path) + os.pathsep + os.environ.get("PATH", "")
    logger.info(f"FFmpeg path set to: {ffmpeg_path}")
if ffprobe_path:
    os.environ["FFPROBE_BINARY"] = ffprobe_path
    os.environ["PATH"] = os.path.dirname(ffprobe_path) + os.pathsep + os.environ.get("PATH", "")
    logger.info(f"FFprobe path set to: {ffprobe_path}")

# Initialize Flask app
app = Flask(__name__, template_folder='templates', static_folder='static')

# App configuration
MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))
SECRET_KEY = os.environ.get('SECRET_KEY', os.urandom(24).hex())
app.config['SECRET_KEY'] = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['DOWNLOAD_FOLDER'] = DOWNLOAD_FOLDER

# Global variables
ALLOWED_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'webm'}
download_progress = {}
progress_lock = Lock()

def check_ffmpeg():
    """Verify FFmpeg and FFprobe are available and working"""
    try:
        for cmd_path, name in [(ffmpeg_path or "ffmpeg", "FFmpeg"), (ffprobe_path or "ffprobe", "FFprobe")]:
            result = subprocess.run(
                [cmd_path, '-version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=5
            )
            logger.info(f"{name} version: {result.stdout.decode('utf-8').splitlines()[0]}")
        return True
    except (subprocess.SubprocessError, FileNotFoundError, TimeoutError) as e:
        logger.error(f"FFmpeg/FFprobe check failed: {str(e)}")
        return False

def initialize_database():
    """Initialize the SQLite database"""
    try:
        db_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "song_library.db")
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create songs table if it doesn't exist
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT,
            file_path TEXT,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            play_count INTEGER DEFAULT 0,
            download_url TEXT,
            metadata TEXT
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialized at {db_path}")
        return db_path
    except Exception as e:
        logger.error(f"Database initialization failed: {str(e)}")
        # Fallback to in-memory database
        return ":memory:"

def allowed_file(filename):
    """Check if the uploaded file has an allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def sanitize_filename(filename):
    """Remove potentially dangerous characters from filenames"""
    # First replace spaces with underscores
    filename = re.sub(r'\s+', '_', filename)
    # Then remove any remaining special characters
    return re.sub(r'[\\/*?:"<>|]', "", filename)

# Check for FFmpeg availability early
ffmpeg_available = check_ffmpeg()
if not ffmpeg_available:
    logger.warning("FFmpeg or FFprobe not found or not working. Download functionality will be limited.")

# Initialize database
db_path = initialize_database()
logger.info(f"Using database at: {db_path}")

@app.after_request
def add_header(response):
    """Add security headers to all responses"""
    # Enable microphone access
    response.headers['Feature-Policy'] = 'microphone *'
    response.headers['Permissions-Policy'] = 'microphone=*'
    
    # Basic security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # Allow CORS for API endpoints
    if request.path.startswith(('/recognize', '/youtube-search', '/download', '/download-progress', '/get_download')):
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    
    return response

@app.route('/')
def index():
    """Serve the main application page"""
    try:
        return render_template('index.html')
    except Exception as e:
        logger.error(f"Error rendering index template: {str(e)}")
        return "Error loading the application. Please check the server logs.", 500

# Add error handling decorator
def handle_errors(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {f.__name__}: {str(e)}", exc_info=True)
            return jsonify({
                "success": False,
                "message": "An internal error occurred. Please try again."
            }), 500
    return wrapper

@app.route('/recognize', methods=['POST'])
@handle_errors
def recognize():
    """Endpoint to recognize a song from an audio file"""
    if 'audio' not in request.files:
        return jsonify({"success": False, "message": "No audio file provided"}), 400
    
    audio_file = request.files['audio']
    if audio_file.filename == '':
        return jsonify({"success": False, "message": "No audio file selected"}), 400
    
    if not allowed_file(audio_file.filename):
        return jsonify({"success": False, "message": "File type not allowed. Please upload an audio file."}), 400
    
    # Create a unique filename to prevent conflicts
    try:
        filename = secure_filename(audio_file.filename)
        unique_filename = f"{uuid.uuid4()}_{filename}"
        file_path = safe_join(UPLOAD_FOLDER, unique_filename)
        if file_path is None:
            return jsonify({"success": False, "message": "Invalid filename"}), 400
        
        audio_file.save(file_path)
        logger.info(f"Audio saved to {file_path}")
        
        # Recognize the song
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(recognize_song(file_path))
        loop.close()
        
        # Clean up the uploaded file
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Temporary file {file_path} removed successfully")
        except Exception as cleanup_err:
            logger.warning(f"Failed to clean up file {file_path}: {cleanup_err}")
        
        if result and 'track' in result:
            song_title = result['track']['title']
            artist = result['track']['subtitle']
            
            # Get YouTube link
            yt_result = search_youtube(song_title, artist)
            youtube_url = yt_result['video_url'] if yt_result else None
            
            # Get Spotify link
            spotify_url = search_spotify(song_title, artist)
            
            # Store song information in the database
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO songs (title, artist, download_url, metadata)
                    VALUES (?, ?, ?, ?)
                ''', (song_title, artist, youtube_url, str(result)))
                conn.commit()
                conn.close()
                logger.info(f"Added song to database: {song_title} by {artist}")
            except Exception as db_err:
                logger.error(f"Failed to store song in database: {db_err}")
            
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
        logger.error(f"Error during recognition: {str(e)}", exc_info=True)
        # Clean up any files in case of errors
        try:
            if 'file_path' in locals() and os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
            
        return jsonify({
            "success": False,
            "message": "An error occurred while processing the audio. Please try again."
        }), 500

@app.route('/youtube-search', methods=['POST'])
def youtube_search_endpoint():
    """Endpoint to search for a song on YouTube"""
    try:
        data = request.json
        if not data or 'title' not in data or 'artist' not in data:
            return jsonify({"success": False, "message": "Missing song information"}), 400
        
        song_title = data['title']
        artist = data['artist']
        
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
            "message": "An error occurred while searching YouTube. Please try again."
        }), 500

@app.route('/download', methods=['POST'])
def download():
    """Endpoint to download a song from YouTube"""
    try:
        data = request.json
        if not data or 'title' not in data or 'artist' not in data:
            return jsonify({"success": False, "message": "Missing song information"}), 400
        
        song_title = data['title']
        artist = data['artist']
        
        # Check if FFmpeg is available
        if not ffmpeg_available:
            return jsonify({
                "success": False,
                "message": "Download functionality unavailable. FFmpeg not found on the server."
            }), 503
        
        # Generate a session ID for this download
        session_id = str(uuid.uuid4())
        
        # Get video ID from YouTube search
        result = search_youtube(song_title, artist)
        if not result or 'video_id' not in result:
            return jsonify({
                "success": False,
                "message": "Couldn't find this song on YouTube"
            }), 404
            
        video_id = result['video_id']
        logger.info(f"Found YouTube video ID: {video_id} for {song_title} - {artist}")
        
        # Start the download in a background thread to avoid blocking the request
        def background_download():
            try:
                download_path = download_song(song_title, artist, session_id, video_id)
                
                # Update the database with the download path if successful
                if download_path and os.path.exists(download_path):
                    try:
                        conn = sqlite3.connect(db_path)
                        cursor = conn.cursor()
                        download_url = f"/get_download/{session_id}/{os.path.basename(download_path)}"
                        cursor.execute('''
                            UPDATE songs 
                            SET file_path = ?, download_url = ?
                            WHERE title = ? AND artist = ?
                        ''', (download_path, download_url, song_title, artist))
                        conn.commit()
                        conn.close()
                    except Exception as db_err:
                        logger.error(f"Failed to update download info in database: {db_err}")
            except Exception as download_err:
                logger.error(f"Background download failed: {download_err}")
        
        # Start the download thread
        threading.Thread(target=background_download).start()
        
        # Return immediately with the session ID
        download_dir = os.path.join(DOWNLOAD_FOLDER, session_id)
        os.makedirs(download_dir, exist_ok=True)
        
        return jsonify({
            "success": True,
            "message": "Download started. Check progress using the provided session ID.",
            "downloadUrl": f"/get_download/{session_id}/",
            "sessionId": session_id
        }), 202  # 202 Accepted
    
    except Exception as e:
        logger.error(f"Error initiating download: {str(e)}")
        return jsonify({
            "success": False,
            "message": "An error occurred while preparing your download. Please try again."
        }), 500

@app.route('/get_download/<session_id>/<path:filename>')
def get_download(session_id, filename):
    """Serve a downloaded file and clean up after download"""
    try:
        download_dir = os.path.join(DOWNLOAD_FOLDER, session_id)
        file_path = os.path.join(download_dir, filename)
        
        if not os.path.exists(file_path):
            logger.warning(f"File not found: {file_path}")
            
            # Check if the download is still in progress
            with progress_lock:
                if session_id in download_progress:
                    progress = download_progress.get(session_id, {}).get('progress', 0)
                    return jsonify({
                        "success": False,
                        "message": f"Download in progress: {progress:.1f}% complete. Please try again later."
                    }), 202
            
            return jsonify({
                "success": False,
                "message": "File not found or already downloaded"
            }), 404
        
        # Schedule cleanup of files after download completes
        @after_this_request
        def cleanup(response):
            try:
                # Start a background thread to clean up later
                threading.Thread(target=lambda: cleanup_download(file_path, download_dir)).start()
                logger.info(f"Scheduled cleanup for {file_path} and {download_dir}")
            except Exception as e:
                logger.error(f"Error in cleanup scheduling: {str(e)}")
            return response
        
        # Safe filename for download
        safe_filename = sanitize_filename(filename)
        
        # Update play count in database
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE songs 
                SET play_count = play_count + 1
                WHERE file_path = ?
            ''', (file_path,))
            conn.commit()
            conn.close()
        except Exception as db_err:
            logger.warning(f"Failed to update play count: {db_err}")
        
        return send_file(
            file_path,
            as_attachment=True,
            download_name=safe_filename
        )
    
    except Exception as e:
        logger.error(f"Error serving download: {str(e)}")
        return jsonify({
            "success": False,
            "message": "An error occurred while serving your download"
        }), 500

@app.route('/download-progress/<session_id>')
def get_download_progress(session_id):
    """Get the current progress of a download"""
    with progress_lock:
        progress = download_progress.get(session_id, {})
    return jsonify(progress)

@app.route('/healthz')
def healthz():
    status = {
        "status": "OK",
        "checks": {
            "ffmpeg": ffmpeg_available,
            "database": check_database(),
            "disk": check_disk_space(),
            "memory": check_memory(),
        },
        "timestamp": datetime.now().isoformat()
    }
    
    status["status"] = "OK" if all(status["checks"].values()) else "DEGRADED"
    return jsonify(status), 200 if status["status"] == "OK" else 503

def check_database():
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False

def check_disk_space():
    try:
        total, used, free = shutil.disk_usage("/")
        return (free / total) > 0.10  # Require 10% free space
    except Exception:
        return False

def check_memory():
    try:
        memory = psutil.virtual_memory()
        return memory.available > 500 * 1024 * 1024  # Require 500MB free
    except Exception:
        return False

def cleanup_download(file_path, dir_path):
    """Clean up downloaded files after a delay"""
    time.sleep(300)  # 5 minutes delay to ensure download completes
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"File {file_path} removed successfully")
        if os.path.exists(dir_path) and not os.listdir(dir_path):
            os.rmdir(dir_path)
            logger.info(f"Directory {dir_path} removed successfully")
    except Exception as e:
        logger.error(f"Error cleaning up files: {str(e)}")

async def recognize_song(file_path):
    """Use ShazamIO to recognize a song from an audio file"""
    try:
        shazam = Shazam()
        # Update to use the new recognize method instead of recognize_song
        out = await shazam.recognize(file_path)
        if out and out.get('track'):
            return out
        return None
    except Exception as e:
        logger.error(f"Error recognizing song: {str(e)}")
        return None

def search_youtube(song_title, artist):
    """Search YouTube for a song and return video information"""
    search_query = f'{song_title} {artist} lyrics'
    query = quote(search_query)
    
    try:
        # Search for video on YouTube
        url = f"https://www.youtube.com/results?search_query={query}"
        
        # Add timeout and user agent
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Try to find video ID with multiple patterns
        video_id = None
        patterns = [
            r'watch\?v=(\S{11})',
            r'youtu.be/(\S{11})',
            r'/embed/(\S{11})',
            r'/v/(\S{11})',
        ]
        
        for pattern in patterns:
            video_id_match = re.search(pattern, response.text)
            if video_id_match:
                video_id = video_id_match.group(1)
                break
        
        if not video_id:
            logger.warning(f"No YouTube video found for query: {search_query}")
            return None
            
        return {
            "video_id": video_id,
            "video_url": f"https://www.youtube.com/watch?v={video_id}"
        }
    except requests.RequestException as e:
        logger.error(f"Request error searching YouTube: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Error searching YouTube: {str(e)}")
        return None

def search_spotify(song_title, artist):
    """Create a Spotify search URL for a song"""
    try:
        query = urllib.parse.quote(f"{song_title} {artist}")
        return f"https://open.spotify.com/search/{query}"
    except Exception as e:
        logger.error(f"Error creating Spotify search URL: {str(e)}")
        return None

def progress_hook(d, session_id):
    """Track the progress of downloads"""
    try:
        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            speed = d.get('speed', 0)
            eta = d.get('eta', 0)
            
            if total > 0:
                progress = (downloaded / total) * 100
            else:
                progress = 0
                
            with progress_lock:
                download_progress[session_id] = {
                    'progress': progress,
                    'speed': speed,
                    'eta': eta,
                    'downloaded': downloaded,
                    'total': total,
                    'status': 'downloading'
                }
                
            logger.info(f"Download progress: {progress:.1f}% @ {speed/1024:.1f}KB/s")
        
        elif d['status'] == 'finished':
            with progress_lock:
                download_progress[session_id] = {
                    'progress': 100,
                    'speed': 0,
                    'eta': 0,
                    'downloaded': 1,
                    'total': 1,
                    'status': 'finished'
                }
            logger.info("Download finished")
            
    except Exception as e:
        logger.error(f"Error in progress hook: {str(e)}")

def download_song(song_title, artist, session_id, video_id):
    """Download a song from YouTube and convert it to MP3"""
    try:
        with progress_lock:
            download_progress[session_id] = {
                'progress': 0,
                'speed': 0,
                'eta': 0,
                'status': 'starting'
            }
        
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        download_dir = os.path.join(DOWNLOAD_FOLDER, session_id)
        os.makedirs(download_dir, exist_ok=True)

        # Create a safe filename
        sanitized_title = sanitize_filename(f"{song_title} - {artist}")
        output_template = os.path.join(download_dir, f"{sanitized_title}.%(ext)s")

        logger.info(f"Starting download from {video_url} to {output_template}")

        # Configure yt-dlp
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': 192,
            }],
            'progress_hooks': [lambda d: progress_hook(d, session_id)],
            'prefer_ffmpeg': True,
            'ffmpeg_location': ffmpeg_path,
            'quiet': False,
            'verbose': True,
            'no_warnings': False,
            'ignoreerrors': True,
            'retries': 10,
            'retry_sleep': 5,
            'socket_timeout': 30,
            'http_chunk_size': 10485760,  # 10MB chunks
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36',
            }
        }

        # Try different download approaches in order of preference
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                if info:
                    # Get the path to the downloaded file
                    downloaded_file = os.path.splitext(ydl.prepare_filename(info))[0] + ".mp3"
                    if os.path.exists(downloaded_file):
                        logger.info(f"Download completed: {downloaded_file}")
                        return downloaded_file
        except Exception as e:
            logger.error(f"Primary download approach failed: {str(e)}")
            
            # Try fallback approach
            try:
                ydl_opts['format'] = 'bestaudio[ext=m4a]'
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(video_url, download=True)
                    downloaded_file = os.path.splitext(ydl.prepare_filename(info))[0] + ".mp3"
                    if os.path.exists(downloaded_file):
                        logger.info(f"Fallback download completed: {downloaded_file}")
                        return downloaded_file
            except Exception as e2:
                logger.error(f"Fallback download failed: {str(e2)}")
                
                # Final attempt with worstaudio
                try:
                    ydl_opts['format'] = 'worstaudio'
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(video_url, download=True)
                        downloaded_file = os.path.splitext(ydl.prepare_filename(info))[0] + ".mp3"
                        if os.path.exists(downloaded_file):
                            logger.info(f"Final fallback download completed: {downloaded_file}")
                            return downloaded_file
                except Exception as e3:
                    logger.error(f"All download attempts failed: {str(e3)}")
                    
        raise Exception("Failed to download song after multiple attempts")

    except Exception as e:
        logger.error(f"Error during song download: {str(e)}", exc_info=True)
        return None
    finally:
        # Make sure we always clean up progress even if download fails
        threading.Timer(
            300,  # Clean up progress after 5 minutes
            lambda: progress_lock.acquire() and progress_lock.release() if session_id in download_progress and progress_lock.acquire() and download_progress.pop(session_id, None) is not None else None
        ).start()

# Run periodic cleanup every hour
def start_cleanup_scheduler():
    """Start a background thread that periodically cleans up old downloads"""
    def run_cleanup():
        while True:
            try:
                logger.info("Running scheduled cleanup of old downloads")
                cleanup_old_downloads()
            except Exception as e:
                logger.error(f"Error in scheduled cleanup: {str(e)}")
            time.sleep(3600)  # Run every hour
            
    threading.Thread(target=run_cleanup, daemon=True).start()

def cleanup_old_downloads():
    """Remove old downloads to free up disk space"""
    try:
        for root, dirs, files in os.walk(DOWNLOAD_FOLDER):
            for file in files:
                file_path = os.path.join(root, file)
                # Remove files older than 24 hours
                if os.path.isfile(file_path) and (time.time() - os.path.getmtime(file_path)) > 86400:
                    try:
                        os.remove(file_path)
                        logger.info(f"Removed old file: {file_path}")
                    except Exception as e:
                        logger.warning(f"Failed to remove old file {file_path}: {str(e)}")
        
        # Remove empty directories
        for root, dirs, files in os.walk(DOWNLOAD_FOLDER, topdown=False):
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                if not os.listdir(dir_path):
                    try:
                        os.rmdir(dir_path)
                        logger.info(f"Removed empty directory: {dir_path}")
                    except Exception as e:
                        logger.warning(f"Failed to remove directory {dir_path}: {str(e)}")
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")

# Start the cleanup scheduler
start_cleanup_scheduler()

if __name__ == '__main__':
    # Log startup information
    logger.info("BeatSnatch starting up...")
    logger.info(f"Using UPLOAD_FOLDER: {UPLOAD_FOLDER}")
    logger.info(f"Using DOWNLOAD_FOLDER: {DOWNLOAD_FOLDER}")
    
    # Get the port from environment or use default
    port = int(os.environ.get('PORT', 8080))
    
    # Railway deployment detection
    if os.environ.get('RAILWAY_ENVIRONMENT') == 'production':
        logger.info(f"Starting production server on port {port}")
        logger.info(f"FFMPEG path: {ffmpeg_path}")
        logger.info(f"FFPROBE path: {ffprobe_path}")
        
        # Use waitress for production
        try:
            serve(app, host='0.0.0.0', port=port, threads=8)
        except ImportError:
            logger.warning("Waitress not installed, falling back to Flask's built-in server (not recommended for production)")
            app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
    else:
        # Development mode
        logger.info(f"Starting development server on port {port}")
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
