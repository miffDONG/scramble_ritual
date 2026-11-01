"""The tracker program (reacTIVision.exe or Ritualvision.exe) as a managed child process.

Never raises from start(): a missing exe / busy camera shows up in status()["error"].
Which program runs is the engine chosen by the `tracker` setting (tracker/engines).
"""

import atexit
import os
import re
import subprocess
import threading
import time
from collections import deque

from tracker.engines import get_engine
from tracker.engines.base import resolve_mode
from tracker.engines.reactivision import no_window_flags

_RE_FORMAT = re.compile(r"format:\s*(\d+)x(\d+),\s*(\d+)\s*fps", re.I)
_RE_CAMERA = re.compile(r"^\s*camera:\s*(.+?)\s*$", re.I)
_RE_ERROR = re.compile(r"no camera|could not|couldn't|cannot|failed|error|not found", re.I)


def parse_stdout_line(line):
    """-> ("format", (w, h, fps)) | ("camera", name) | ("error", line) | None"""
    m = _RE_FORMAT.search(line)
    if m:
        return "format", (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _RE_CAMERA.match(line)
    if m:
        return "camera", m.group(1)
    if _RE_ERROR.search(line):
        return "error", line.strip()
    return None


def kill_tree(pid):
    """A one-file bundle (Ritualvision.exe) is a launcher plus the real process. Stopping
    only the launcher leaves the child holding the camera, so stop the whole tree."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                       creationflags=no_window_flags())


def close_windows(pid):
    """Post WM_CLOSE to the visible windows of process `pid` (Windows only), as if the user
    closed them: reacTIVision then exits normally and saves its settings. Returns the
    number of windows asked to close (0 with -n, which has no window)."""
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    for hwnd in found:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)          # WM_CLOSE
    return len(found)


class TrackerProcess:
    """Owns one tracker child process."""

    CLOSE_WAIT_S = 3.0         # normal exit (settings saved) before it is forced

    def __init__(self, exe="", work_dir=None):
        self._lock = threading.RLock()
        self.exe_override = exe           # reacTIVision only (--rtv-exe)
        self.work_dir = work_dir          # None = the exe folder (normal)
        self.proc = None
        self.log = deque(maxlen=300)
        self.format = None            # (w, h, fps) as reported by the tracker
        self.format_source = None     # "stdout" | "configured"
        self.camera_name = ""
        self.error = None
        self.exe = ""
        self.engine = "reactivision"
        self.engine_label = "reacTIVision"
        self._kill_tree = False
        self._close_window_first = False
        self.xml = {}
        self.started_at = None
        self._reader = None
        atexit.register(self.stop)

    # -- lifecycle ------------------------------------------------------

    def is_running(self):
        with self._lock:
            return self.proc is not None and self.proc.poll() is None

    def start(self, cfg):
        with self._lock:
            if self.is_running():
                self.stop()
            self.error = None
            self.format = None
            self.format_source = None
            self.camera_name = ""
            eng = get_engine(cfg)
            self.engine, self.engine_label, self._kill_tree = eng.name, eng.label, eng.kill_tree
            self._close_window_first = eng.close_window_first
            exe = eng.find_exe(cfg, self.exe_override)
            if not exe:
                self.error = eng.missing_exe_message()
                self._log(self.error)
                return self.status()
            exe = eng.prepare(exe, self._log)
            self.exe = exe
            work_dir = self.work_dir or os.path.dirname(exe)
            modes = eng.list_modes(exe)
            full = resolve_mode(cfg, modes["cameras"])
            try:
                self.xml = eng.write_configs(full, work_dir)
            except Exception as exc:
                self.error = f"XML 쓰기 실패: {exc}"
                self._log(self.error)
                return self.status()
            self.format = (int(full["rtv_width"]), int(full["rtv_height"]),
                           int(full["rtv_fps"]))
            self.format_source = "configured"
            # no -c: the tracker loads its XML files from its own folder (cwd).
            # reacTIVision: "-n" must be the ONLY argument when used.
            args = eng.args(full, exe)
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            try:
                self.proc = subprocess.Popen(
                    args, cwd=os.path.dirname(exe), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                    errors="replace", creationflags=flags)
            except Exception as exc:
                self.proc = None
                self.error = f"실행 실패: {exc}"
                self._log(self.error)
                return self.status()
            self.started_at = time.time()
            self._log(f"$ {' '.join(args)}")
            self._reader = threading.Thread(target=self._pump, args=(self.proc,),
                                            daemon=True)
            self._reader.start()
        time.sleep(0.3)                    # an immediate exit = bad camera/config
        with self._lock:
            if self.proc is not None and self.proc.poll() is not None and not self.error:
                self.error = (f"{self.engine_label} 즉시 종료 (code {self.proc.returncode}) - "
                              "카메라가 다른 프로그램에 잡혀 있거나 설정이 잘못됨")
        return self.status()

    def stop(self, timeout=3.0):
        with self._lock:
            p = self.proc
            self.proc = None
        if p is None:
            return
        try:
            if p.poll() is None and self._close_window_first:
                try:
                    if close_windows(p.pid):
                        p.wait(self.CLOSE_WAIT_S)
                        self._log("closed normally (settings saved by the tracker)")
                except subprocess.TimeoutExpired:
                    self._log("did not close in time; forcing")
                except Exception as exc:
                    self._log(f"close error: {exc}")
            if p.poll() is None and self._kill_tree:
                kill_tree(p.pid)
                p.wait(timeout)
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(1.0)
        except Exception as exc:
            self._log(f"stop error: {exc}")
        with self._lock:
            self.format = None
            self.format_source = None
            self._log("stopped")

    def restart(self, cfg):
        self.stop()
        time.sleep(0.5)                    # let DirectShow release the device
        return self.start(cfg)

    # -- internals ------------------------------------------------------

    def _log(self, line):
        self.log.append(f"{time.strftime('%H:%M:%S')} {line}")

    def _pump(self, proc):
        try:
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                if not line.strip():
                    continue
                self._log(line)
                kind = parse_stdout_line(line)
                if not kind:
                    continue
                with self._lock:
                    if kind[0] == "format":
                        self.format = kind[1]
                        self.format_source = "stdout"
                    elif kind[0] == "camera":
                        self.camera_name = kind[1]
                    elif kind[0] == "error" and not self.error:
                        self.error = kind[1]
        except Exception as exc:
            self._log(f"reader error: {exc}")
        rc = proc.poll()
        if rc is None:
            try:
                rc = proc.wait(1.0)
            except Exception:
                rc = None
        with self._lock:
            if rc not in (None, 0) and not self.error and self.proc is proc:
                self.error = f"{self.engine_label} 종료 (code {rc})"
            self._log(f"exited rc={rc}")

    def status(self):
        with self._lock:
            running = self.proc is not None and self.proc.poll() is None
            fmt = self.format
            return {
                "running": running,
                "pid": self.proc.pid if running else None,
                "exe": self.exe,
                "engine": self.engine,
                "engine_label": self.engine_label,
                "format": f"{fmt[0]}x{fmt[1]}@{fmt[2]}" if fmt else None,
                "format_wh": [fmt[0], fmt[1]] if fmt else None,
                "format_source": self.format_source,
                "camera": self.camera_name,
                "error": self.error,
                "log": list(self.log)[-40:],
                "work_dir": self.work_dir,
                "xml": dict(self.xml),
                "uptime": (round(time.time() - self.started_at, 1)
                           if running and self.started_at else 0),
            }


#: the old name (the process used to be reacTIVision only)
ReactivisionProcess = TrackerProcess
