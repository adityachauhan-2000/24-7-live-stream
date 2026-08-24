@echo off
title 24/7 YouTube Stream Server
cd /d "%~dp0"
echo Starting 24/7 YouTube Live Studio Server on http://127.0.0.1:8000...
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
