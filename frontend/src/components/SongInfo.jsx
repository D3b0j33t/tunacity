import React from 'react';

const SongInfo = ({ song, onDownload }) => {
  if (!song) return null;

  return (
    <div className="song-info" style={{ display: 'block' }}>
      <h3>Recognized Song</h3>
      <div className="song-details">
        <div className="song-detail">
          <span className="detail-label">Title</span>
          <span className="detail-value">{song.title}</span>
        </div>
        
        <div className="song-detail">
          <span className="detail-label">Artist</span>
          <span className="detail-value">{song.artist}</span>
        </div>
      </div>
      
      <div className="action-buttons">
        <a 
          href={song.spotifyUrl} 
          className="service-btn spotify-btn" 
          target="_blank" 
          rel="noopener noreferrer"
        >
          <i className="fab fa-spotify"></i> Play on Spotify
        </a>
        <a 
          href={song.youtubeUrl} 
          className="service-btn youtube-link" 
          target="_blank" 
          rel="noopener noreferrer"
        >
          <i className="fab fa-youtube"></i> Watch on YouTube
        </a>
        {onDownload && (
          <button 
            onClick={onDownload} 
            className="download-mp3-btn"
          >
            <i className="fas fa-download"></i> Download MP3
          </button>
        )}
      </div>
    </div>
  );
};

export default SongInfo;
