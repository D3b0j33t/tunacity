from flask import Flask, render_template, request, jsonify, send_file, Response, after_this_request
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
import tempfile
import contextlib
from threading import Lock
import urllib.parse
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates', static_folder='static')

# Configuration
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', '/tmp/uploads')
DOWNLOAD_FOLDER = os.environ.get('DOWNLOAD_FOLDER', '/tmp/downloads')
MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))
SECRET_KEY = os.environ.get('SECRET_KEY', 'your-secret-key-here')

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
        return await shazam.recognize(file_path)
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
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        download_dir = os.path.join(DOWNLOAD_FOLDER, session_id)
        os.makedirs(download_dir, exist_ok=True)
        
        sanitized_title = sanitize_filename(f"{song_title} - {artist}")
        output_template = os.path.join(download_dir, f"{sanitized_title}.%(ext)s")
        
        def progress_hook(d):
            if d['status'] == 'downloading':
                with progress_lock:
                    download_progress[session_id] = {
                        'progress': d.get('downloaded_bytes', 0) / d.get('total_bytes', 1) * 100,
                        'speed': d.get('speed', 0),
                        'eta': d.get('eta', 0)
                    }

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'progress_hooks': [progress_hook],
            'prefer_ffmpeg': True,
            'socket_timeout': 30,
        }
        
        with contextlib.redirect_stdout(None), contextlib.redirect_stderr(None):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(video_url, download=True)
                downloaded_file = os.path.splitext(ydl.prepare_filename(info_dict))[0] + ".mp3"
        
        with progress_lock:
            if session_id in download_progress:
                del download_progress[session_id]
        
        return downloaded_file if os.path.exists(downloaded_file) else None
            
    except Exception as e:
        logger.error(f"Error during song download: {str(e)}")
        with progress_lock:
            if session_id in download_progress:
                del download_progress[session_id]
        return None

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

if __name__ == '__main__':
    # Use PORT environment variable for compatibility with cloud platforms
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)