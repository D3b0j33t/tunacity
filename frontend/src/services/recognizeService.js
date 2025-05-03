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

  async downloadSong(title, artist) {
    try {
      const response = await axios.post(`${API_URL}/download`, { title, artist });
      return response.data;
    } catch (error) {
      throw new Error('Failed to download song');
    }
  }
};
