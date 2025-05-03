import React, { useEffect, useRef } from 'react';

const Visualizer = ({ isRecording, audioData }) => {
  const canvasRef = useRef(null);
  const animationRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    let animation;

    const drawIdleWave = () => {
      const width = canvas.width;
      const height = canvas.height;
      const now = Date.now() / 1000;

      ctx.fillStyle = 'rgb(0, 0, 0)';
      ctx.fillRect(0, 0, width, height);
      
      ctx.beginPath();
      ctx.strokeStyle = 'rgba(0, 255, 153, 0.5)';
      ctx.lineWidth = 2;

      for (let x = 0; x < width; x++) {
        const y = height/2 + Math.sin(x * 0.02 + now) * 20;
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }

      ctx.stroke();
      animation = requestAnimationFrame(drawIdleWave);
    };

    drawIdleWave();
    return () => cancelAnimationFrame(animation);
  }, []);

  return (
    <canvas
      ref={canvasRef}
      width={600}
      height={100}
      className="w-full rounded-lg bg-black"
    />
  );
};

export default Visualizer;
