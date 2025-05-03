import express from 'express';
import cors from 'cors';
import multer from 'multer';
import { v4 as uuidv4 } from 'uuid';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs/promises';
import ytdl from 'ytdl-core';
import ffmpeg from 'fluent-ffmpeg';
import got from 'got';
import ProgressBar from 'progress';
import { Shazam } from 'node-shazam';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const shazam = new Shazam();

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

const upload = multer({
  storage,
  limits: { fileSize: 10 * 1024 * 1024 }
});

app.use(cors());
app.use(express.json());
app.use(express.static('frontend/build'));

async function recognizeSong(audioPath) {
  try {
    const result = await shazam.recognize(audioPath);
    return result;
  } catch (error) {
    console.error('Recognition error:', error);
    throw error;
  }
}

async function downloadSong(videoId, outputPath) {
  return new Promise((resolve, reject) => {
    const video = ytdl(videoId, { quality: 'highestaudio' });
    const bar = new ProgressBar('Downloading [:bar] :percent :etas', { total: 100 });
    
    ffmpeg(video)
      .toFormat('mp3')
      .on('progress', progress => {
        bar.update(progress.percent / 100);
      })
      .on('end', () => resolve(outputPath))
      .on('error', reject)
      .save(outputPath);
  });
}

app.post('/api/recognize', upload.single('audio'), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ success: false, message: 'No audio file provided' });
  }
  
  try {
    const result = await recognizeSong(req.file.path);
    if (!result || !result.track) {
      return res.status(404).json({ success: false, message: 'Song not recognized' });
    }

    const songInfo = {
      title: result.track.title,
      artist: result.track.subtitle,
      youtubeId: await searchYouTube(`${result.track.title} ${result.track.subtitle}`),
    };

    res.json({
      success: true,
      song: songInfo
    });
  } catch (error) {
    res.status(500).json({ success: false, message: 'Error processing audio' });
  }
});

app.post('/api/download', async (req, res) => {
  const { videoId, title } = req.body;
  if (!videoId) {
    return res.status(400).json({ success: false, message: 'No video ID provided' });
  }

  try {
    const outputPath = path.join(DOWNLOAD_DIR, `${sanitizeFilename(title)}.mp3`);
    await downloadSong(videoId, outputPath);
    
    res.json({
      success: true,
      downloadUrl: `/downloads/${path.basename(outputPath)}`
    });
  } catch (error) {
    res.status(500).json({ success: false, message: 'Download failed' });
  }
});

const PORT = process.env.PORT || 8080;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
