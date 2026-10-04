#!/bin/bash
# Scramble Ritual - TEST launcher (virtual objects, no reacTIVision) - macOS.
# Double-click in Finder (first time: right-click > Open), or run from Terminal.
# Finds python3, then hands over to scripts/launch.py which detects the OS,
# checks / builds .venv (bin/python3 layout) and installs requirements.txt,
# then starts webui.exhibit_server --test.
#   ./scripts/test_start.command              http://localhost:8765
#   ./scripts/test_start.command --port 8766
cd "$(dirname "$0")/.." || exit 1

PY=""
for c in python3 python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "[error] Python 3.10+ not found."
  echo "        Install with:  brew install python   (https://brew.sh)"
  echo "        or from https://www.python.org/downloads/macos/"
  read -r -p "Press Enter to close..."
  exit 1
fi

"$PY" scripts/launch.py test "$@"
rc=$?
if [ $rc -ne 0 ]; then
  echo
  echo "[test_start.command] exited with error $rc."
  read -r -p "Press Enter to close..."
fi
exit $rc
