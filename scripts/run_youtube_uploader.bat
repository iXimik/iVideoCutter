@echo off
cd /d "%~dp0.."
".venv\Scripts\python.exe" "run_youtube_uploader.py" >> "logs\youtube_scheduler.log" 2>&1
