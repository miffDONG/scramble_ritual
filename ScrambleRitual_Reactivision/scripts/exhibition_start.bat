@echo off
REM Scramble Ritual - EXHIBITION launcher.
REM Starts the exhibition-only web server (webui.exhibit_server), which runs the
REM marker tracker (reacTIVision.exe or Ritualvision.exe) as a child process,
REM receives TUIO and sends OSC to SuperCollider / TouchDesigner. Creates .venv
REM and installs requirements on first run, then opens the browser.
REM   exhibition_start.bat                          start with the tracker used last time
REM   exhibition_start.bat --tracker ritualvision   edge-dash markers (Ritualvision); remembered
REM   exhibition_start.bat --tracker reactivision   amoeba markers (reacTIVision); remembered
REM   exhibition_start.bat --no-rtv                 do not auto-start the tracker
REM   exhibition_start.bat --port 8765
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

if not exist "%USERPROFILE%\scramble_ritual\reactivision\reacTIVision.exe" (
  echo [warn] %USERPROFILE%\scramble_ritual\reactivision\reacTIVision.exe not found ^(only needed for --tracker reactivision^).
  echo        The server copies the reacTIVision-1.5.1-win64 folder there from Downloads on
  echo        first start ^(or set the exe path in the web UI^).
)

start "" http://localhost:8765
".venv\Scripts\python" -m webui.exhibit_server %*
if errorlevel 1 (
  echo.
  echo [exhibition_start.bat] server exited with error %errorlevel%. Press any key to close.
  pause >nul
)
