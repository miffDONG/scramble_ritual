@echo off
rem Builds Ritualvision.exe next to this file (one file, console window for the log).
rem First time only:  python -m venv .venv  &&  .venv\Scripts\python -m pip install numpy opencv-python pyinstaller
cd /d "%~dp0"
".venv\Scripts\pyinstaller.exe" --noconfirm --onefile --console --name Ritualvision ^
  --add-data "%~dp0ids.json;." ^
  --distpath "%~dp0." --workpath "%~dp0build" --specpath "%~dp0build" ^
  "%~dp0run.py"
