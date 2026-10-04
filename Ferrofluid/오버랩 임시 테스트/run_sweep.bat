@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PWM Sweep Test
set PY=python
where python >nul 2>nul || set PY=py
%PY% -c "import serial, numpy" >nul 2>nul
if errorlevel 1 %PY% -m pip install pyserial numpy
%PY% audio_amp_bridge.py sweep
pause
