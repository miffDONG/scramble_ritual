@echo off
REM Scramble Ritual - TEST launcher (virtual objects, no reacTIVision).
REM Windows entry point. Finds a system Python 3, then hands over to
REM scripts\launch.py which detects the OS, checks / builds .venv and
REM installs requirements.txt, then starts webui.exhibit_server --test.
REM   test_start.bat              http://localhost:8765
REM   test_start.bat --port 8766
REM On macOS use test_start.command (or: python3 scripts/launch.py test).
setlocal
cd /d "%~dp0.."

if not "%OS%"=="Windows_NT" (
  echo [error] This .bat is for Windows. On macOS run scripts/test_start.command
  pause
  exit /b 1
)

set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo [error] Python 3.10+ not found. Install it from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)

%PY% scripts\launch.py test %*
if errorlevel 1 (
  echo.
  echo [test_start.bat] exited with error %errorlevel%. Press any key to close.
  pause >nul
)
