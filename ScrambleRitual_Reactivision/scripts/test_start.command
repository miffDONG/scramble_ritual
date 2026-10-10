#!/bin/bash
# Scramble Ritual - TEST launcher (virtual objects, no reacTIVision) - macOS.
# Double-click this file. On first run it installs a project-local uv,
# downloads managed CPython, creates .venv, and installs requirements.txt.
set -u

PY_PIN=3.13
UV_INSTALL_URL=https://astral.sh/uv/install.sh

say()  { printf '%s\n' "$*"; }
step() { printf '\n[%s] %s\n' "$1" "$2"; }
die() {
  printf '\n[error] %s\n' "$1" >&2
  shift
  for line in "$@"; do printf '        %s\n' "$line" >&2; done
  if [ -t 0 ]; then
    printf '\n엔터를 누르면 닫힙니다. '
    read -r _
  fi
  exit 1
}

# --------------------------------------------------------------------------
# 1) Bootstrap paths and repair permissions commonly lost in ZIP/AirDrop.
# --------------------------------------------------------------------------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 1
SELF="$HERE/$(basename "${BASH_SOURCE[0]}")"
ROOT="$(cd "$HERE/.." && pwd)" || exit 1

# The extracted folder can arrive as read-only. Its owner can restore the
# write bit without administrator privileges. The execute bit helps next run.
chmod u+rwx "$ROOT" "$HERE" 2>/dev/null || true
chmod u+x,go-w "$SELF" 2>/dev/null || true
[ -w "$ROOT" ] || die \
  "프로젝트 폴더에 쓸 수 없습니다." \
  "압축을 완전히 푼 뒤 폴더를 다운로드 또는 문서 폴더로 옮겨 다시 실행하세요." \
  "읽기 전용 디스크나 압축 파일 미리보기 안에서는 실행할 수 없습니다."
cd "$ROOT" || die "프로젝트 폴더로 이동하지 못했습니다: $ROOT"

# Once this script has been explicitly opened, clear the propagated quarantine
# attribute so the project-local tools do not trigger repeated Gatekeeper blocks.
if xattr "$ROOT" "$HERE" "$SELF" 2>/dev/null | grep -q com.apple.quarantine; then
  xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null \
    && say "[setup] Gatekeeper 격리 속성을 정리했습니다."
fi

# If Terminal is running through Rosetta, restart natively so uv downloads the
# correct Apple Silicon Python and wheels.
if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
  say "[setup] Rosetta 환경을 감지해 네이티브 arm64로 다시 실행합니다."
  exec arch -arm64 "$SELF" "$@"
fi

ARCH="$(uname -m)"
case "$ARCH" in
  arm64|x86_64) ;;
  *) die "지원하지 않는 Mac 아키텍처입니다: $ARCH" ;;
esac
say "[info] macOS $(sw_vers -productVersion 2>/dev/null || echo '?') / ${ARCH}"

# --------------------------------------------------------------------------
# 2) Install uv locally. No preinstalled Python or administrator password is
#    required. uv and the managed Python remain inside this project folder.
# --------------------------------------------------------------------------
BOOTSTRAP="$ROOT/.bootstrap"
UV_BIN_DIR="$BOOTSTRAP/bin"
UV="$UV_BIN_DIR/uv"
export UV_PYTHON_INSTALL_DIR="$BOOTSTRAP/python"
export UV_CACHE_DIR="$BOOTSTRAP/cache"
export UV_NO_MODIFY_PATH=1

install_uv() {
  step setup "실행 환경 도구 설치 (최초 1회)"
  command -v curl >/dev/null 2>&1 || die \
    "macOS 기본 다운로드 도구(curl)를 찾지 못했습니다."
  mkdir -p "$UV_BIN_DIR" "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR" || die \
    "프로젝트 내부에 .bootstrap 폴더를 만들지 못했습니다."

  local installer
  installer="$(mktemp "${TMPDIR:-/tmp}/scramble-uv.XXXXXX")" || die \
    "임시 설치 파일을 만들지 못했습니다."
  if ! curl -LsSf "$UV_INSTALL_URL" -o "$installer"; then
    rm -f "$installer"
    die "uv 설치 파일을 내려받지 못했습니다." \
      "인터넷 연결을 확인한 뒤 다시 더블클릭하세요."
  fi
  if ! env UV_UNMANAGED_INSTALL="$UV_BIN_DIR" UV_NO_MODIFY_PATH=1 sh "$installer"; then
    rm -f "$installer"
    die "uv 설치에 실패했습니다. 위 로그를 공유해 주세요."
  fi
  rm -f "$installer"
  [ -x "$UV" ] || die "uv 실행 파일이 만들어지지 않았습니다: $UV"
}

[ -x "$UV" ] || install_uv
say "[setup] uv: $("$UV" --version 2>&1)"

# --------------------------------------------------------------------------
# 3) Create/repair .venv and synchronize dependencies.
# --------------------------------------------------------------------------
[ -f requirements.txt ] || die "requirements.txt를 찾지 못했습니다: $ROOT"
VENV_PY="$ROOT/.venv/bin/python"
STAMP="$ROOT/.venv/.requirements.sha256"
REQ_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"

venv_usable() {
  [ -x "$VENV_PY" ] || return 1
  local got
  got="$("$VENV_PY" -c 'import platform, sys; print(f"{sys.version_info.major}.{sys.version_info.minor} {platform.machine()}")' 2>/dev/null)" || return 1
  [ "$got" = "$PY_PIN $ARCH" ]
}

create_venv() {
  step setup ".venv 생성 (관리형 Python ${PY_PIN})"
  rm -rf "$ROOT/.venv"
  "$UV" venv --managed-python --python "$PY_PIN" "$ROOT/.venv" || die \
    "Python ${PY_PIN}을 내려받아 .venv를 만들지 못했습니다." \
    "인터넷 연결을 확인한 뒤 다시 더블클릭하세요."
  [ -x "$VENV_PY" ] || die ".venv/bin/python이 만들어지지 않았습니다."
}

install_deps() {
  step setup "필수 패키지 설치 (최초 실행은 수 분 걸릴 수 있습니다)"
  "$UV" pip install --python "$VENV_PY" -r requirements.txt || die \
    "필수 패키지 설치에 실패했습니다." \
    "인터넷 연결을 확인하고 위 오류 로그를 공유해 주세요."
  printf '%s' "$REQ_HASH" > "$STAMP"
}

selfcheck() {
  "$VENV_PY" - <<'CHECK'
import importlib.metadata as md
import platform

import cv2
import flask
import numpy
import serial
import sounddevice
from pythonosc.udp_client import SimpleUDPClient  # noqa: F401

print(
    f"[check] Python {platform.python_version()} / {platform.machine()} | "
    f"OpenCV {cv2.__version__} | NumPy {numpy.__version__} | "
    f"Flask {md.version('flask')}"
)
CHECK
}

ready=0
attempt=1
while [ "$attempt" -le 2 ]; do
  if ! venv_usable; then
    [ -e "$ROOT/.venv" ] && say "[setup] 현재 Mac에서 사용할 수 없는 .venv를 다시 만듭니다."
    create_venv
  fi
  if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$REQ_HASH" ]; then
    install_deps
  fi
  if selfcheck; then
    ready=1
    break
  fi
  if [ "$attempt" = "1" ]; then
    say "[warn] 환경 점검에 실패해 .venv를 한 번 다시 만듭니다."
    rm -rf "$ROOT/.venv"
  fi
  attempt=$((attempt + 1))
done
[ "$ready" = "1" ] || die \
  "환경을 새로 만든 뒤에도 패키지를 불러오지 못했습니다." \
  "터미널에 표시된 오류를 그대로 공유해 주세요."

# --------------------------------------------------------------------------
# 4) Start the test server and open the browser after the port is ready.
# --------------------------------------------------------------------------
ARGS=("$@")
has_host=0
PORT=8765
i=0
while [ "$i" -lt "${#ARGS[@]}" ]; do
  case "${ARGS[$i]}" in
    --host|--host=*) has_host=1 ;;
    --port)
      j=$((i + 1))
      [ "$j" -lt "${#ARGS[@]}" ] && PORT="${ARGS[$j]}"
      ;;
    --port=*) PORT="${ARGS[$i]#--port=}" ;;
  esac
  i=$((i + 1))
done
[ "$has_host" -eq 1 ] || ARGS+=(--host 127.0.0.1)

URL="http://127.0.0.1:${PORT}"
if [ -z "${SR_NO_BROWSER:-}" ]; then
  (
    for _ in $(seq 1 150); do
      if nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then
        open "$URL"
        exit 0
      fi
      sleep 0.2
    done
  ) &
fi

step start "테스트 서버 실행 — ${URL}  (종료: Ctrl+C)"
"$VENV_PY" -m webui.exhibit_server --test "${ARGS[@]}"
status=$?

if [ "$status" -ne 0 ] && [ "$status" -ne 130 ] && [ "$status" -ne 143 ]; then
  printf '\n[test_start.command] 서버가 오류로 종료했습니다 (exit %s).\n' "$status"
  say "  - 포트 ${PORT}를 다른 프로그램이 사용 중인지 확인해 주세요."
  if [ -t 0 ]; then
    printf '\n엔터를 누르면 닫힙니다. '
    read -r _
  fi
fi
exit "$status"
