@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Audio Amplitude -^> Electromagnet

set PY=python
where python >nul 2>nul || set PY=py

%PY% -c "import pyaudiowpatch, serial, numpy" >nul 2>nul
if errorlevel 1 (
  echo [setup] installing packages...
  %PY% -m pip install pyaudiowpatch pyserial numpy
)

%PY% audio_amp_bridge.py
pause
