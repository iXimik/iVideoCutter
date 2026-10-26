@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [iVideoCutter] Missing .venv\Scripts\python.exe
    echo Install Python 3.12, create the venv and install requirements.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "main.py"
if errorlevel 1 (
    echo.
    echo [iVideoCutter] Failed to start. See logs\startup_errors.log
    pause
)
