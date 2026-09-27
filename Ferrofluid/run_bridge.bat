@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup_venv.bat
".venv\Scripts\python.exe" osc_audio_bridge.py
pause
