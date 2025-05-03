import express from 'express';
import cors from 'cors';
import multer from 'multer';
import { v4 as uuidv4 } from 'uuid';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs/promises';
import ytDlpWrap from 'yt-dlp-wrap';
import ffmpeg from 'fluent-ffmpeg';
import fetch from 'node-fetch';
import FormData from 'form-data';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const YTDlpWrap = ytDlpWrap.default;
const ytdlp = new YTDlpWrap();

// Fix directory paths for Railway
const UPLOAD_DIR = process.env.UPLOAD_DIR || path.join(process.cwd(), 'tmp/uploads');
const DOWNLOAD_DIR = process.env.DOWNLOAD_DIR || path.join(process.cwd(), 'tmp/downloads');

await fs.mkdir(UPLOAD_DIR, { recursive: true });
await fs.mkdir(DOWNLOAD_DIR, { recursive: true });

// Add health check endpoint
app.get('/api/health', (req, res) => {
  res.json({ status: 'healthy' });
});

// Add error handling middleware
app.use((err, req, res, next) => {
  console.error('Error:', err);
  res.status(500).json({ 
    success: false, 
    message: 'Internal server error' 
  });
});

// Update storage configuration
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, UPLOAD_DIR),
  filename: (req, file, cb) => cb(null, `${uuidv4()}.wav`)
});

// Update file size limit and add file type validation
const upload = multer({
  storage,
  limits: { fileSize: 5 * 1024 * 1024 }, // 5MB limit
  fileFilter: (req, file, cb) => {
    if (file.mimetype.startsWith('audio/')) {
      cb(null, true);
    } else {
      cb(new Error('Only audio files are allowed'));
    }
  }
});

app.use(cors());
app.use(express.json());
app.use(express.static('frontend/build'));

// Add sanitizeFilename function
function sanitizeFilename(filename) {
  return filename
    .replace(/[\\/:*?"<>|]/g, '')
    .replace(/\s+/g, '_')
    .slice(0, 255);
}

// Add audio format conversion utility
async function convertToWav(inputPath, outputPath) {
  return new Promise((resolve, reject) => {
    ffmpeg(inputPath)
      .toFormat('wav')
      .outputOptions('-acodec pcm_s16le')
      .outputOptions('-ar 44100')
      .outputOptions('-ac 1')
      .on('end', resolve)
      .on('error', reject)
      .save(outputPath);
  });
}

// Update recognizeSong function with better audio handling
async function recognizeSong(audioPath) {
  try {
    const wavPath = audioPath.replace(/\.[^/.]+$/, '.wav');
    await convertToWav(audioPath, wavPath);
    
    const form = new FormData();
    form.append('file', await fs.readFile(wavPath));
    form.append('api_token', process.env.SHAZAM_API_KEY);
    form.append('timeout', '15');

    const response = await fetch('https://api.shazam.com/v1/detect', {
      method: 'POST',
      body: form,
      headers: {
        ...form.getHeaders(),
        'User-Agent': 'TunacityApp/1.0'
      }
    });

    await cleanupFile(wavPath);

    if (!response.ok) {
      throw new Error(`Shazam API error: ${response.status} ${response.statusText}`);
    }

    const data = await response.json();
    
    if (!data.track) {
      throw new Error('Song not recognized. Please try again with a clearer audio sample.');
    }

    return {
      title: data.track.title,
      artist: data.track.subtitle,
      album: data.track.album?.title,
      releaseDate: data.track.releasedate,
      artwork: data.track.images?.coverart
    };
  } catch (error) {
    console.error('Recognition error:', error);
    throw error;
  }
}

async function searchAndDownload(query) {
  try {
    // Sanitize search query
    const sanitizedQuery = query.replace(/[^\w\s]/g, '');
    
    const searchResults = await ytdlp.execPromise([
      `ytsearch1:${sanitizedQuery}`,
      '--get-id',
      '--get-title',
      '--no-playlist'
    ]);
    
    const [videoId, title] = searchResults.trim().split('\n');
    const outputPath = path.join(DOWNLOAD_DIR, `${sanitizeFilename(title || 'download')}.mp3`);
    
    await ytdlp.execPromise([
      `https://www.youtube.com/watch?v=${videoId}`,
      '-x',
      '--audio-format', 'mp3',
      '--audio-quality', '0',
      '-o', outputPath
    ]);

    return { videoId, title: title || 'Unknown', outputPath };
  } catch (error) {
    console.error('Download error:', error);
    throw error;
  }
}

async function searchYouTube(query) {
  try {
    const searchUrl = `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`;
    const response = await fetch(searchUrl);
    const html = await response.text();
    const videoIdMatch = html.match(/watch\?v=(\S{11})/);
    return videoIdMatch ? videoIdMatch[1] : null;
  } catch (error) {
    console.error('YouTube search error:', error);
    throw error;
  }
}

async function downloadAudio(videoId, outputPath) {
  try {
    await ytdlp.execPromise([
      `https://www.youtube.com/watch?v=${videoId}`,
      '-x',
      '--audio-format', 'mp3',
      '--audio-quality', '0',
      '-o', outputPath
    ]);
    return outputPath;
  } catch (error) {
    console.error('Download error:', error);
    throw error;
  }
}

// Add cleanup utility function
async function cleanupFile(filePath) {
  try {
    await fs.access(filePath);
    await fs.unlink(filePath);
  } catch (error) {
    console.error(`Error cleaning up file ${filePath}:`, error);
  }
}

// Update recognize endpoint with better error handling
app.post('/api/recognize', upload.single('audio'), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ 
      success: false, 
      message: 'No audio file provided' 
    });
  }
  
  try {
    const songData = await recognizeSong(req.file.path);
    const query = `${songData.title} ${songData.artist}`;
    const videoId = await searchYouTube(query);
    
    if (!videoId) {
      throw new Error('No matching video found');
    }

    const spotifyUrl = `https://open.spotify.com/search/${encodeURIComponent(query)}`;

    res.json({
      success: true,
      song: {
        ...songData,
        videoId,
        youtubeUrl: `https://www.youtube.com/watch?v=${videoId}`,
        spotifyUrl
      }
    });
  } catch (error) {
    res.status(error.status || 500).json({ 
      success: false, 
      message: error.message || 'Error processing audio'
    });
  } finally {
    if (req.file) {
      await cleanupFile(req.file.path);
    }
  }
});

// Update download endpoint with progress tracking
app.post('/api/download', async (req, res) => {
  const { videoId, title } = req.body;
  
  if (!videoId) {
    return res.status(400).json({ success: false, message: 'No video ID provided' });
  }

  const sessionId = uuidv4();
  const outputPath = path.join(DOWNLOAD_DIR, `${sanitizeFilename(title || 'download')}.mp3`);

  try {
    const dlProcess = ytdlp.exec([
      `https://www.youtube.com/watch?v=${videoId}`,
      '-x',
      '--audio-format', 'mp3',
      '--audio-quality', '0',
      '-o', outputPath,
      '--progress'
    ]);

    dlProcess.on('progress', (progress) => {
      // Store progress in memory/cache for status endpoint
      console.log(`Download progress: ${progress.percent}%`);
    });

    await new Promise((resolve, reject) => {
      dlProcess.on('error', reject);
      dlProcess.on('close', resolve);
    });

    res.json({
      success: true,
      downloadUrl: `/downloads/${path.basename(outputPath)}`,
      sessionId
    });
  } catch (error) {
    console.error('Download error:', error);
    res.status(500).json({ success: false, message: 'Download failed' });
  }
});

// Add download progress endpoint
app.get('/api/download/:sessionId/progress', (req, res) => {
  const { sessionId } = req.params;
  // Return progress from memory/cache
  res.json({ progress: 0 }); // Implement actual progress tracking
});

// Serve downloaded files
app.use('/downloads', express.static(DOWNLOAD_DIR));

const PORT = process.env.PORT || 8080;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
