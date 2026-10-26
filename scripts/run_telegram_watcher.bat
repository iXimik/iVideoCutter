@echo off
cd /d "%~dp0.."
".venv\Scripts\python.exe" "run_telegram_watcher.py" >> "logs\telegram_scheduler.log" 2>&1
