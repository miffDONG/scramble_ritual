@echo off
REM Scramble Ritual - EXHIBITION launcher (reacTIVision edition).
REM Starts the exhibition-only web server (webui.exhibit_server), which runs
REM reacTIVision.exe as a child process, receives TUIO and sends OSC to
REM SuperCollider / TouchDesigner. Creates .venv and installs requirements on
REM first run, then opens the browser.
REM   exhibition_start.bat              normal start (reacTIVision auto-starts)
REM   exhibition_start.bat --no-rtv     do not auto-start reacTIVision
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
  echo [warn] %USERPROFILE%\scramble_ritual\reactivision\reacTIVision.exe not found.
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
