@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 배선 확인: CH1~CH5 차례로 1초씩
".venv\Scripts\python.exe" osc_audio_bridge.py --test
pause
