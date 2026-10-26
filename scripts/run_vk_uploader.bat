@echo off
cd /d "%~dp0.."
".venv\Scripts\python.exe" "run_vk_uploader.py" >> "logs\vk_scheduler.log" 2>&1
