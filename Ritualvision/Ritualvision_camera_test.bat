@echo off
rem Double-click camera test: lists cameras, then runs Ritualvision with its preview window.
rem The window stays open afterwards so any error message can be read.
cd /d "%~dp0"
echo === cameras ===
Ritualvision.exe --list-cameras
echo.
echo === Ritualvision (q or Esc in the preview window to quit) ===
Ritualvision.exe %*
echo.
pause
