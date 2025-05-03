@echo off
start /B python app.py
timeout /t 8 /nobreak >nul
start http://127.0.0.1:5000/
ngrok http 5000