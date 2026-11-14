@echo off
chcp 65001 >nul
cd /d "%~dp0"
set /p CH=스윕할 채널 번호(1~25): 
".venv\Scripts\python.exe" osc_audio_bridge.py --sweep %CH%
pause
