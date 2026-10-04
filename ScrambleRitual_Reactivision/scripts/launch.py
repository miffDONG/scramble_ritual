#!/usr/bin/env python3
"""
Cross-platform launcher for the Scramble Ritual servers.

    python scripts/launch.py test      [--port N]   -> webui.exhibit_server --test
    python scripts/launch.py exhibit   [--port N]   -> webui.exhibit_server

Steps (same on every OS, paths differ):
  1. detect the OS (Windows / macOS / Linux) and the matching venv layout
  2. check whether .venv exists and has the requirements installed
  3. if not: create .venv with this interpreter and pip-install requirements.txt
  4. open the browser and run the server with the venv's python

Called by scripts/test_start.bat (Windows) and scripts/test_start.command
(macOS); can also be run directly with any Python 3.10+.
"""

import os
import platform
import subprocess
import sys
import time
import venv
import webbrowser

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENV = os.path.join(ROOT, ".venv")
REQ = os.path.join(ROOT, "requirements.txt")
STAMP = os.path.join(VENV, ".requirements.stamp")   # mtime of requirements.txt at last install

MODES = {
    "test":    ["-m", "webui.exhibit_server", "--test"],
    "exhibit": ["-m", "webui.exhibit_server"],
}


def say(msg):
    print(f"[launch] {msg}", flush=True)


def detect_os():
    s = platform.system()
    if s == "Windows":
        return "windows"
    if s == "Darwin":
        return "macos"
    return "linux"


def venv_python(osname):
    if osname == "windows":
        return os.path.join(VENV, "Scripts", "python.exe")
    return os.path.join(VENV, "bin", "python3")


def requirements_ok():
    """venv exists AND requirements.txt has not changed since the last install."""
    try:
        with open(STAMP) as f:
            return f.read().strip() == str(int(os.path.getmtime(REQ)))
    except OSError:
        return False


def build_env(osname, py):
    if not os.path.exists(py):
        say(f"{osname}: .venv 없음 - 생성 중 ({sys.executable})")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
        if not os.path.exists(py):
            sys.exit(f"[error] venv 생성 실패: {py} 없음")
    say("requirements 설치 중 ...")
    cmd = [py, "-m", "pip", "install", "--disable-pip-version-check", "-r", REQ]
    if subprocess.call(cmd, cwd=ROOT) != 0:
        sys.exit("[error] pip install 실패. 위 로그를 확인하세요.")
    with open(STAMP, "w") as f:
        f.write(str(int(os.path.getmtime(REQ))))
    say("환경 구축 완료")


def main(argv):
    if not argv or argv[0] not in MODES:
        sys.exit(f"usage: launch.py {{{'|'.join(MODES)}}} [--port N] [server args...]")
    mode, rest = argv[0], argv[1:]
    if sys.version_info < (3, 10):
        sys.exit(f"[error] Python 3.10+ 필요 (현재 {platform.python_version()})")

    osname = detect_os()
    py = venv_python(osname)
    say(f"OS: {osname} ({platform.platform()}) / Python {platform.python_version()}")

    if os.path.exists(py) and requirements_ok():
        say("환경 확인: .venv 준비됨")
    else:
        build_env(osname, py)

    port = "8765"
    if "--port" in rest:
        i = rest.index("--port")
        if i + 1 < len(rest):
            port = rest[i + 1]
    if osname == "windows" and mode == "exhibit":
        exe = os.path.join(os.path.expanduser("~"), "scramble_ritual", "reactivision",
                           "reacTIVision.exe")
        if not os.path.exists(exe):
            say(f"[warn] {exe} 없음 - 서버가 Downloads에서 복사하거나 웹 UI에서 경로 지정")

    url = f"http://localhost:{port}"
    say(f"{mode} 서버 시작 -> {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    os.chdir(ROOT)
    rc = subprocess.call([py] + MODES[mode] + rest, cwd=ROOT)
    if rc != 0:
        say(f"서버가 오류 {rc}로 종료됨")
        if osname == "windows":
            time.sleep(0.2)
            input("아무 키나 누르면 닫힙니다...")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
