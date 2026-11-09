@echo off
rem Swap in the newly built exe (close Ritualvision first).
cd /d "%~dp0"
if not exist "build\next\Ritualvision.exe" ( echo no new build & pause & exit /b 1 )
move /y "build\next\Ritualvision.exe" "Ritualvision.exe" && echo updated Ritualvision.exe
pause
