#!/bin/bash
# Scramble Ritual Morse - web tuner launcher for macOS (double-click friendly).
# Detects Intel vs Apple Silicon, picks a Python 3 built for that architecture,
# creates the venv and installs requirements on first run, then starts the
# live-tuning web UI and opens the browser.
#   ./start.command                start on the object table (sound / OSC)
#   ./start.command --sim          start on the synthetic card scene
#   ./start.command --camera 0     start on camera 0
#   ./start.command --video ../sampleVideo/morseCode.MOV
#
# SCRAMBLE_PYTHON=/path/to/python3 forces a specific interpreter.
set -u
shopt -s execfail 2>/dev/null || true

PY_MIN_MINOR=9          # oldest Python 3.x we accept
BREW_PY=python@3.12     # what we install when the machine has no usable Python

pause() {
  printf '\n'
  read -r -n 1 -s -p "Press any key to close." _ 2>/dev/null || true
  printf '\n'
}

die() {
  printf '\n[error] %s\n' "$*" >&2
  pause
  exit 1
}

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" \
  || die "could not find the project root next to this script."

[ "$(uname -s)" = "Darwin" ] \
  || die "this launcher is for macOS. On Windows use scripts\\start.bat instead."

# ---------------------------------------------------------------- architecture
# A Terminal (or IDE) running under Rosetta reports x86_64 even on an M-series
# Mac, and every wheel we then install is the slow x86_64 build. Re-exec once,
# natively, before anything touches the venv.
if [ "${SCRAMBLE_ARCH_REEXEC:-0}" != "1" ] \
   && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
  if arch -arm64 /usr/bin/true >/dev/null 2>&1; then
    echo "[arch] running under Rosetta - re-executing natively on arm64 ..."
    export SCRAMBLE_ARCH_REEXEC=1
    exec arch -arm64 "${BASH_SOURCE[0]}" "$@"
  fi
  echo "[warn] running under Rosetta and 'arch -arm64' is unavailable;"
  echo "       continuing as x86_64 - wheels will be the slower Intel builds."
fi

ARCH="$(uname -m)"
case "$ARCH" in
  arm64)  BREW_PREFIX=/opt/homebrew; ARCH_LABEL="Apple Silicon (arm64)" ;;
  x86_64) BREW_PREFIX=/usr/local;    ARCH_LABEL="Intel (x86_64)" ;;
  *)      die "unsupported architecture: $ARCH" ;;
esac
echo "[arch] $ARCH_LABEL / Homebrew prefix: $BREW_PREFIX"

# --------------------------------------------------------------------- python
# Usable = runs, is Python 3.9+, and is built for THIS architecture. The last
# check is what rejects an Intel Homebrew python on Apple Silicon (and any
# Rosetta python), which would otherwise pull x86_64 wheels into the venv.
py_ok() {
  local c="$1" machine major minor out
  [ -n "$c" ] || return 1
  command -v "$c" >/dev/null 2>&1 || return 1
  out="$("$c" -c 'import platform,sys; print(platform.machine(), sys.version_info[0], sys.version_info[1])' 2>/dev/null)" || return 1
  set -- $out
  [ $# -eq 3 ] || return 1
  machine="$1"; major="$2"; minor="$3"
  [ "$machine" = "$ARCH" ] || return 1
  [ "$major" = "3" ] && [ "$minor" -ge "$PY_MIN_MINOR" ]
}

find_python() {
  local c
  for c in "${SCRAMBLE_PYTHON:-}" \
           "$BREW_PREFIX/bin/python3.13" "$BREW_PREFIX/bin/python3.12" \
           "$BREW_PREFIX/bin/python3.11" "$BREW_PREFIX/bin/python3" \
           python3.13 python3.12 python3.11 python3.10 python3 \
           /usr/bin/python3; do
    if py_ok "$c"; then
      PY="$(command -v "$c")"
      return 0
    fi
  done
  return 1
}

# Homebrew for THIS architecture only: an Intel brew under /usr/local on an
# M-series Mac would install an x86_64 python and we would reject it again.
brew_bin() {
  local b
  if [ -x "$BREW_PREFIX/bin/brew" ]; then
    echo "$BREW_PREFIX/bin/brew"
    return 0
  fi
  b="$(command -v brew 2>/dev/null)" || return 1
  if [ "$("$b" --prefix 2>/dev/null)" = "$BREW_PREFIX" ]; then
    echo "$b"
    return 0
  fi
  return 1
}

PY=""
if ! find_python; then
  echo "[setup] no $ARCH Python 3.$PY_MIN_MINOR+ found - installing $BREW_PY with Homebrew ..."
  if ! BREW="$(brew_bin)"; then
    echo "" >&2
    echo "[error] no Homebrew for $ARCH_LABEL and no usable Python 3.$PY_MIN_MINOR+." >&2
    echo "        Install Homebrew into $BREW_PREFIX :" >&2
    echo '          /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' >&2
    echo "        or install Python from https://www.python.org/downloads/macos/" >&2
    echo "        Then run this launcher again." >&2
    pause
    exit 1
  fi
  "$BREW" install "$BREW_PY" || die "'brew install $BREW_PY' failed. Check the log above."
  export PATH="$BREW_PREFIX/opt/$BREW_PY/libexec/bin:$BREW_PREFIX/bin:$PATH"
  find_python || die "$BREW_PY installed but no usable interpreter appeared. Try: $BREW doctor"
fi
echo "[python] $PY ($("$PY" -c 'import platform; print(platform.python_version(), platform.machine())'))"

# ----------------------------------------------------------------------- venv
# This repo is developed on Windows, where .venv holds Scripts/ and Lib/. That
# tree is useless here, so keep it (a shared folder may still need it) and use
# .venv-mac instead.
VENV=".venv"
if [ ! -x "$VENV/bin/python3" ] && [ -d "$VENV/Scripts" ]; then
  VENV=".venv-mac"
  echo "[setup] .venv is a Windows virtualenv - using $VENV on macOS."
fi

venv_ok() {
  [ -x "$VENV/bin/python3" ] || return 1
  [ "$("$VENV/bin/python3" -c 'import platform; print(platform.machine())' 2>/dev/null)" = "$ARCH" ]
}

if [ -d "$VENV" ] && ! venv_ok; then
  echo "[setup] $VENV does not run on this machine ($ARCH) - recreating it."
  rm -rf "$VENV" || die "could not remove $VENV."
fi

if [ ! -d "$VENV" ]; then
  echo "[setup] $VENV not found - creating with $PY ..."
  "$PY" -m venv "$VENV" || die "could not create $VENV with $PY."
fi

VPY="$VENV/bin/python3"
venv_ok || die "$VENV/bin/python3 is missing or not $ARCH. Delete $VENV and run again."

# Reinstall whenever requirements.txt changes, not only on first run.
REQ="requirements.txt"
STAMP="$VENV/.requirements.sha"
REQ_SHA="$(shasum -a 256 "$REQ" 2>/dev/null | awk '{print $1}')"
[ -n "$REQ_SHA" ] || die "$REQ not found - is this the ScrambleRitual_Morse folder?"
if [ "$REQ_SHA" != "$(cat "$STAMP" 2>/dev/null || true)" ]; then
  echo "[setup] installing requirements ..."
  "$VPY" -m pip install --disable-pip-version-check --upgrade pip \
    || die "could not upgrade pip inside $VENV."
  # pygrabber is marked sys_platform == "win32", so pip skips it here.
  "$VPY" -m pip install --disable-pip-version-check -r "$REQ" \
    || die "pip install failed. Check the log above."
  printf '%s\n' "$REQ_SHA" > "$STAMP"
fi

"$VPY" -c "import cv2, numpy, flask, pythonosc, serial" \
  || die "the venv is missing core packages. Delete $VENV and run again."

# sounddevice is imported lazily everywhere (tracker/granular.py,
# conductor/music.py), so a missing PortAudio costs sound, not the tuner.
if ! "$VPY" -c "import sounddevice" >/dev/null 2>&1; then
  echo "[warn] sounddevice/PortAudio unavailable - the web tuner runs, sound output does not."
  echo "       fix: $BREW_PREFIX/bin/brew install portaudio, then start again."
fi

echo "[note] macOS asks for camera permission the first time a camera opens."
echo "       If frames stay black: System Settings > Privacy & Security > Camera."

# ------------------------------------------------------------------------ run
PORT=8765
prev=""
for a in "$@"; do
  [ "$prev" = "--port" ] && PORT="$a"
  case "$a" in --port=*) PORT="${a#--port=}" ;; esac
  prev="$a"
done
URL="http://localhost:$PORT"

# Open the browser only once the server answers, so the first load is not a
# connection error.
(
  for _ in $(seq 1 60); do
    if nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then
      open "$URL"
      exit 0
    fi
    sleep 0.5
  done
  echo "[warn] nothing listening on $PORT yet - open $URL manually."
) &
OPENER=$!

"$VPY" -m webui.server "$@"
status=$?
kill "$OPENER" 2>/dev/null || true

if [ "$status" -ne 0 ]; then
  echo
  echo "[start.command] server exited with error $status."
  pause
fi
exit "$status"
