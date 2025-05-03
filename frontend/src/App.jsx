import React, { useState } from 'react';
import AudioRecorder from './components/AudioRecorder';
import SongInfo from './components/SongInfo';
import Visualizer from './components/Visualizer';
import { RecognizeService } from './services/recognizeService';

function App() {
  const [recognizedSong, setRecognizedSong] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleAudioSubmit = async (audioBlob) => {
    setIsLoading(true);
    setError(null);
    
    try {
      const result = await RecognizeService.recognizeSong(audioBlob);
      setRecognizedSong(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 to-black text-white">
      <div className="container mx-auto px-4 py-8">
        <header className="text-center mb-12">
          <h1 className="text-5xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-teal-400 to-blue-500">
            Tunacity
          </h1>
          <p className="mt-2 text-gray-400">Identify any song in seconds</p>
        </header>

        <main className="max-w-2xl mx-auto">
          <Visualizer />
          <AudioRecorder onRecordingComplete={handleAudioSubmit} />
          
          {isLoading && (
            <div className="text-center mt-8">
              <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-teal-500 mx-auto"></div>
              <p className="mt-4 text-gray-400">Analyzing audio...</p>
            </div>
          )}

          {error && (
            <div className="mt-8 p-4 bg-red-900/50 rounded-lg text-center">
              <p className="text-red-400">{error}</p>
            </div>
          )}

          {recognizedSong && <SongInfo song={recognizedSong} />}
        </main>
      </div>
    </div>
  );
}

export default App;
