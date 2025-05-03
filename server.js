import express from 'express';
import cors from 'cors';
import multer from 'multer';
import { v4 as uuidv4 } from 'uuid';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs/promises';
import { YTDlpWrap } from 'yt-dlp-wrap';
import ffmpeg from 'fluent-ffmpeg';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
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

async function searchAndDownload(query) {
  try {
    const searchResults = await ytdlp.execPromise([
      'ytsearch1:' + query,
      '--get-id',
      '--get-title'
    ]);
    
    const [videoId, title] = searchResults.split('\n');
    const outputPath = path.join(DOWNLOAD_DIR, `${sanitizeFilename(title)}.mp3`);
    
    await ytdlp.execPromise([
      `https://www.youtube.com/watch?v=${videoId}`,
      '-x',
      '--audio-format', 'mp3',
      '-o', outputPath
    ]);

    return { videoId, title, outputPath };
  } catch (error) {
    console.error('Download error:', error);
    throw error;
  }
}

app.post('/api/recognize', upload.single('audio'), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ success: false, message: 'No audio file provided' });
  }
  
  try {
    // For now, just return success as we'll implement audio recognition later
    res.json({
      success: true,
      message: 'Audio received',
      file: req.file
    });
  } catch (error) {
    res.status(500).json({ success: false, message: 'Error processing audio' });
  }
});

app.post('/api/download', async (req, res) => {
  const { query } = req.body;
  if (!query) {
    return res.status(400).json({ success: false, message: 'No search query provided' });
  }

  try {
    const { videoId, title, outputPath } = await searchAndDownload(query);
    res.json({
      success: true,
      downloadUrl: `/downloads/${path.basename(outputPath)}`,
      title,
      videoId
    });
  } catch (error) {
    res.status(500).json({ success: false, message: 'Download failed' });
  }
});

const PORT = process.env.PORT || 8080;
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
