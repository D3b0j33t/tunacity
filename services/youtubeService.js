import got from 'got';

export async function searchYouTube(query) {
  try {
    const searchUrl = `https://www.youtube.com/results?search_query=${encodeURIComponent(query)}`;
    const response = await got(searchUrl);
    
    const videoIdMatch = response.body.match(/watch\?v=(\S{11})/);
    return videoIdMatch ? videoIdMatch[1] : null;
  } catch (error) {
    console.error('YouTube search error:', error);
    throw error;
  }
}

function sanitizeFilename(filename) {
  return filename.replace(/[\\/*?:"<>|]/g, '');
}
