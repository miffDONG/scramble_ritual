@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p CH=스윕할 채널 번호(2~4, 가운데 3채널): 
".venv\Scripts\python.exe" osc_audio_bridge.py --sweep %CH%
pause
