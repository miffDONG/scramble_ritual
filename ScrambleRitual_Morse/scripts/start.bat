@echo off
REM Scramble Ritual Morse - web tuner launcher (double-click friendly).
REM Creates .venv and installs requirements on first run, then starts the
REM live-tuning web UI and opens the browser.
REM   start.bat                start on the synthetic scene
REM   start.bat --camera 0     start on camera 0
REM   start.bat --video ..\sampleVideo\morseCode.MOV
setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
  echo [setup] .venv not found - creating with py -3 ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo [error] could not create .venv. Install Python 3 from python.org first.
    pause
    exit /b 1
  )
  echo [setup] installing requirements...
  ".venv\Scripts\python" -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo [error] pip install failed. Check the log above.
    pause
    exit /b 1
  )
)

start "" http://localhost:8765
".venv\Scripts\python" -m webui.server %*
if errorlevel 1 (
  echo.
  echo [start.bat] server exited with error %errorlevel%. Press any key to close.
  pause >nul
)
