import axios from 'axios';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8080';

export const RecognizeService = {
  async recognizeSong(audioBlob) {
    const formData = new FormData();
    formData.append('audio', audioBlob);

    try {
      const response = await axios.post(`${API_URL}/recognize`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });

      if (!response.data.success) {
        throw new Error(response.data.message);
      }

      return response.data;
    } catch (error) {
      throw new Error(error.response?.data?.message || 'Failed to recognize song');
    }
  },

  async downloadSong(videoId, title) {
    try {
      const response = await axios.post(`${API_URL}/download`, { videoId, title });
      
      if (response.data.sessionId) {
        // Poll for progress
        const progressInterval = setInterval(async () => {
          try {
            const progressResponse = await axios.get(
              `${API_URL}/download/${response.data.sessionId}/progress`
            );
            // Update UI with progress
            if (progressResponse.data.progress === 100) {
              clearInterval(progressInterval);
            }
          } catch (error) {
            clearInterval(progressInterval);
          }
        }, 1000);
      }

      return response.data;
    } catch (error) {
      throw new Error('Failed to download song');
    }
  }
};
