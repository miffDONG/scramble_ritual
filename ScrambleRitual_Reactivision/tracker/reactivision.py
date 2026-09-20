"""
reacTIVision as a managed child process + TUIO receiver.

The exhibition server does not touch the camera itself. It
  1. writes camera.xml / reacTIVision.xml INTO THE EXE FOLDER (reacTIVision
     1.5.1 only loads them implicitly from there: an explicit
     <camera config=...> tag, or -c pointing at another folder, breaks its
     path resolution; the distribution's originals are kept as *.orig),
  2. launches reacTIVision.exe with no arguments (-n alone when headless),
  3. receives TUIO 1.1 (/tuio/2Dobj) on UDP 127.0.0.1:<port>,
and hands the fiducial objects to the pipeline (ROI normalize -> tension -> OSC).

Pure stdlib + python-osc: no cv2 / Flask here so tests can import it cheaply.

Config keys (all `rtv_*`, see RTV_DEFAULTS) map 1:1 onto the XML attributes:

  camera.xml <camera id>                      rtv_camera
             <capture width height fps compress>   rtv_width rtv_height rtv_fps rtv_compress
             <settings brightness ... focus>       rtv_brightness ... rtv_focus
             <frame width height xoff yoff>        rtv_frame_width rtv_frame_height rtv_xoff rtv_yoff
  reacTIVision.xml <tuio host port>           rtv_tuio_port (host fixed 127.0.0.1)
             <finger size sensitivity>             rtv_finger_size rtv_finger_sensitivity
             <fiducial engine tree>                rtv_engine rtv_tree ("" = default.trees,
                                                   "small" -> symbols/amoeba/small.trees)
             <image display equalize fullscreen>   rtv_display rtv_equalize rtv_fullscreen
             <threshold gradient tile threads>     rtv_gradient rtv_tile rtv_threads
             <calibration file invert>             rtv_calib_file rtv_calib_invert
"""

import atexit
import math
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr


PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
#: reacTIVision lives OUTSIDE the project (and outside OneDrive): our own copy
#: of the distribution, whose camera.xml / reacTIVision.xml we overwrite.
INSTALL_DIR = os.path.join(os.path.expanduser("~"), "scramble_ritual", "reactivision")
#: server-side scratch (modes cache) next to the exe
RTV_WORK_DIR = INSTALL_DIR
EXE_CANDIDATES = [
    os.path.join(INSTALL_DIR, "reacTIVision.exe"),
    os.path.join(os.path.expanduser("~"), "Downloads", "reacTIVision-1.5.1-win64",
                 "reacTIVision-1.5.1-win64", "reacTIVision.exe"),
]

RTV_DEFAULTS = {
    "rtv_exe": "",               # "" = 자동 탐색 (~/scramble_ritual/reactivision → Downloads)
    "rtv_autostart": True,       # 서버 시작 시 reacTIVision 자동 실행
    "rtv_no_window": False,      # -n : reacTIVision 창 없이 실행
    # camera.xml
    "rtv_camera": 0,
    "rtv_width": 0,              # 0 = 자동 (MJPG·120fps 가능한 최대 해상도)
    "rtv_height": 0,
    "rtv_fps": 120,
    "rtv_compress": True,        # true = MJPG, false = YUY2(비압축)
    "rtv_brightness": "default", # default | min | max | 정수
    "rtv_contrast": "default",
    "rtv_gain": "default",
    "rtv_shutter": "default",
    "rtv_exposure": "default",   # DirectShow log2 초. 120fps는 -7(1/128s) 이하
    "rtv_sharpness": "default",
    "rtv_gamma": "default",
    "rtv_focus": "min",
    "rtv_frame_width": "max",    # 원본 프레임 크롭 (max = 전체)
    "rtv_frame_height": "max",
    "rtv_xoff": 0,
    "rtv_yoff": 0,
    # reacTIVision.xml
    "rtv_tuio_port": 3333,
    "rtv_finger_size": 0,        # 0 = 손가락 추적 끔
    "rtv_finger_sensitivity": 75,
    "rtv_engine": "amoeba",      # amoeba | classic
    "rtv_tree": "",              # "" = default.trees, "small" = symbols/amoeba/small.trees
    "rtv_display": "src",        # src | dest | none
    "rtv_equalize": False,
    "rtv_fullscreen": False,
    "rtv_gradient": 32,          # 이진화 gradient gate
    "rtv_tile": 10,              # 이진화 tile size
    "rtv_threads": "max",
    "rtv_calib_file": "default.grid",
    "rtv_calib_invert": "",      # "" | x | y | a 조합 (예: xya)
    # pipeline side
    "rtv_angle_offset": 0.0,     # tilt 0° 기준 보정(도)
    "rtv_stale_s": 1.0,          # TUIO가 이 시간 이상 끊기면 오브제 없음 처리
}

_SETTING_KEYS = ("brightness", "contrast", "gain", "shutter", "exposure",
                 "sharpness", "gamma", "focus")


def find_exe(override=""):
    """The reacTIVision.exe to run: an explicit path if it exists, else the
    first existing candidate (the install folder first, then Downloads)."""
    if override and os.path.isfile(override):
        return os.path.abspath(override)
    for c in EXE_CANDIDATES:
        if os.path.isfile(c):
            return c
    return None


def ensure_local_install(exe, install_dir=INSTALL_DIR):
    """We overwrite camera.xml / reacTIVision.xml next to the exe, so never
    run a copy that is not ours: if `exe` lives elsewhere (e.g. Downloads),
    copy its folder into `install_dir` once and use that copy."""
    if not exe:
        return None
    exe_dir = os.path.abspath(os.path.dirname(exe))
    if os.path.normcase(exe_dir) == os.path.normcase(os.path.abspath(install_dir)):
        return exe
    local_exe = os.path.join(install_dir, os.path.basename(exe))
    if not os.path.isfile(local_exe):
        import shutil
        shutil.copytree(exe_dir, install_dir, dirs_exist_ok=True)
    return local_exe if os.path.isfile(local_exe) else exe


def original_xml_paths(exe_dir):
    """(camera.xml, reacTIVision.xml) to IMPORT from: the *.orig backups once
    we have overwritten the live files, else the live files."""
    out = []
    for n in ("camera.xml", "reacTIVision.xml"):
        live = os.path.join(exe_dir, n)
        out.append(live + ".orig" if os.path.isfile(live + ".orig") else live)
    return tuple(out)


# ---------------------------------------------------------------- -l parsing

_RE_CAM = re.compile(r"^\s*(\d+):\s+(.+?)\s*$")
_RE_FMT = re.compile(r"^\s*format:\s*(\S+)")
_RE_MODE = re.compile(r"^\s*(\d+)x(\d+)\s+([\d.|]+)\s*fps")


def _num(s):
    v = float(s)
    return int(v) if v.is_integer() else v


def parse_list_output(text):
    """`reacTIVision -l` stdout -> cameras with their formats/modes:
    [{"index", "name", "formats": [{"codec", "modes": [{"w","h","fps":[...]}]}]}]
    The MIDI device section is ignored."""
    cams = []
    cam = fmt = None
    in_cams = False
    for line in text.splitlines():
        if "MIDI" in line and "device" in line:
            break
        if re.search(r"camera[s]? found", line):
            in_cams = True
            continue
        if not in_cams:
            continue
        m = _RE_MODE.match(line)
        if m and fmt is not None:
            fps = [_num(p) for p in m.group(3).split("|") if p]
            fmt["modes"].append({"w": int(m.group(1)), "h": int(m.group(2)),
                                 "fps": fps})
            continue
        m = _RE_FMT.match(line)
        if m and cam is not None:
            fmt = {"codec": m.group(1), "modes": []}
            cam["formats"].append(fmt)
            continue
        m = _RE_CAM.match(line)
        if m:
            cam = {"index": int(m.group(1)), "name": m.group(2), "formats": []}
            cams.append(cam)
            fmt = None
    return cams


def default_mode(camera, want_fps=120):
    """Pick the exhibition default: MJPG (compressed, high fps) modes that
    offer `want_fps`, the largest resolution among them; otherwise the largest
    resolution of any codec at its max fps. Returns
    {"w","h","fps","codec","compress"} or None for a camera with no modes."""
    if not camera or not camera.get("formats"):
        return None
    fmts = sorted(camera["formats"], key=lambda f: 0 if f["codec"] == "MJPG" else 1)
    best = None
    for f in fmts:
        for m in f["modes"]:
            if want_fps in m["fps"]:
                cand = (m["w"] * m["h"], f["codec"] == "MJPG")
                if best is None or cand > best[0]:
                    best = (cand, f, m, want_fps)
    if best is None:
        for f in fmts:
            for m in f["modes"]:
                if not m["fps"]:
                    continue
                cand = (m["w"] * m["h"], f["codec"] == "MJPG")
                if best is None or cand > best[0]:
                    best = (cand, f, m, max(m["fps"]))
    if best is None:
        return None
    _, f, m, fps = best
    return {"w": m["w"], "h": m["h"], "fps": fps, "codec": f["codec"],
            "compress": f["codec"] == "MJPG"}


_MODES_CACHE = {}
#: last complete enumeration, kept on disk so the resolution/fps lists still
#: work while the camera is busy (e.g. our own reacTIVision child is running)
MODES_FILE = os.path.join(RTV_WORK_DIR, "modes.json")   # next to the exe


def _no_window_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _has_modes(cameras):
    return any(m for c in cameras for f in c["formats"] for m in f["modes"])


def _load_modes_file(path=MODES_FILE):
    try:
        import json
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if _has_modes(d.get("cameras", [])) else None
    except Exception:
        return None


def _save_modes_file(out, path=MODES_FILE):
    try:
        import json
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"cameras": out["cameras"], "t": out["t"]}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def list_modes(exe, refresh=False, timeout=15):
    """Run `reacTIVision -l` (cached per exe) and return
    {"cameras": [...], "raw": str, "t": float, "error": str|None, "stale": bool}.

    While another program holds the camera, -l lists it WITHOUT any modes. In
    that case the last complete enumeration (reactivision/modes.json) is
    returned with stale=True, and nothing is cached in memory so the next call
    re-enumerates once the device is free."""
    if not exe:
        return {"cameras": [], "raw": "", "t": time.time(), "stale": False,
                "error": "reacTIVision.exe 없음"}
    if not refresh and exe in _MODES_CACHE:
        return _MODES_CACHE[exe]
    try:
        r = subprocess.run([exe, "-l"], cwd=os.path.dirname(exe),
                           capture_output=True, text=True, timeout=timeout,
                           errors="replace", creationflags=_no_window_flags())
        raw = (r.stdout or "") + (r.stderr or "")
        out = {"cameras": parse_list_output(raw), "raw": raw,
               "t": time.time(), "error": None, "stale": False}
    except Exception as exc:
        out = {"cameras": [], "raw": "", "t": time.time(), "error": str(exc),
               "stale": False}
    if _has_modes(out["cameras"]):
        _MODES_CACHE[exe] = out
        _save_modes_file(out)
        return out
    saved = _load_modes_file()
    if saved:
        names = {c["name"] for c in out["cameras"]}
        return {"cameras": saved["cameras"], "raw": out["raw"], "t": saved.get("t", 0),
                "stale": True,
                "error": ("카메라가 사용 중이라 모드 목록을 못 읽음 - 마지막 목록 사용"
                          if names else out["error"])}
    if out["cameras"] and not out["error"]:
        out["error"] = "카메라 모드 목록이 비어 있음 - 카메라가 다른 프로그램에 잡혀 있을 수 있음"
    return out


def resolve_mode(cfg, cameras):
    """Copy of cfg with rtv_width/height/fps/compress filled from the camera's
    default mode when width/height are 0 (auto)."""
    out = dict(cfg)
    if int(out.get("rtv_width") or 0) > 0 and int(out.get("rtv_height") or 0) > 0:
        return out
    cam = next((c for c in cameras if c["index"] == int(out.get("rtv_camera", 0))),
               cameras[0] if cameras else None)
    mode = default_mode(cam, int(out.get("rtv_fps") or 120)) if cam else None
    if mode:
        out.update(rtv_width=mode["w"], rtv_height=mode["h"], rtv_fps=mode["fps"],
                   rtv_compress=mode["compress"])
    else:                                   # no enumeration: a safe guess
        out.update(rtv_width=1280, rtv_height=800)
    return out


# ---------------------------------------------------------------- XML

def _setting(v):
    """XML attribute text for a camera setting: default/min/max or an int."""
    if v is None or v == "":
        return "default"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(int(v))
    s = str(v).strip()
    if s in ("default", "min", "max", "auto"):
        return s
    try:
        return str(int(float(s)))
    except ValueError:
        return s


def _bool(v):
    return "true" if v else "false"


def build_camera_xml(cfg):
    c = {**RTV_DEFAULTS, **cfg}
    settings = " ".join(f'{k}="{_setting(c["rtv_" + k])}"' for k in _SETTING_KEYS)
    return (
        '<?xml version="1.0" encoding="ISO-8859-1" ?>\n'
        '<portvideo>\n'
        f'    <camera id="{int(c["rtv_camera"])}">\n'
        f'        <capture width="{int(c["rtv_width"])}" height="{int(c["rtv_height"])}" '
        f'fps="{_setting(c["rtv_fps"])}" compress="{_bool(c["rtv_compress"])}" />\n'
        f'        <settings {settings} />\n'
        f'        <frame width="{_setting(c["rtv_frame_width"])}" '
        f'height="{_setting(c["rtv_frame_height"])}" '
        f'xoff="{int(c["rtv_xoff"])}" yoff="{int(c["rtv_yoff"])}" />\n'
        '    </camera>\n'
        '</portvideo>\n'
    )


def tree_attr(value):
    """`rtv_tree` -> the <fiducial tree> attribute reacTIVision expects: a
    path relative to the exe folder. "" / default -> None (omit the attribute
    -> built-in default.trees); "small" / "small.trees" ->
    symbols/amoeba/small.trees; anything containing a slash is used as is."""
    v = str(value or "").strip().replace("\\", "/")
    if v in ("", "default", "default.trees"):
        return None
    if "/" in v:
        return v
    if not v.endswith(".trees"):
        v += ".trees"
    return "symbols/amoeba/" + v


def build_reactivision_xml(cfg, camera_xml_path=None):
    """reacTIVision.xml text. camera.xml is NOT referenced by a <camera
    config> tag: reacTIVision 1.5.1 fails to open the file whenever that tag
    is present, but loads camera.xml from its own folder when it is absent.
    (`camera_xml_path` is accepted for compatibility and ignored.)"""
    c = {**RTV_DEFAULTS, **cfg}
    invert = str(c["rtv_calib_invert"]).strip() or " "
    tree = tree_attr(c["rtv_tree"])
    fid = (f'    <fiducial engine="{c["rtv_engine"]}"'
           + (f' tree={quoteattr(tree)}' if tree else "") + ' />\n')
    return (
        '<?xml version="1.0" encoding="ISO-8859-1" ?>\n'
        '<reactivision>\n'
        '    <!-- generated by webui.exhibit_server; edit via the web UI -->\n'
        f'    <tuio host="127.0.0.1" port="{int(c["rtv_tuio_port"])}" />\n'
        f'    <finger size="{int(c["rtv_finger_size"])}" '
        f'sensitivity="{int(c["rtv_finger_sensitivity"])}" />\n'
        + fid +
        f'    <image display="{c["rtv_display"]}" equalize="{_bool(c["rtv_equalize"])}" '
        f'fullscreen="{_bool(c["rtv_fullscreen"])}" />\n'
        f'    <threshold gradient="{int(c["rtv_gradient"])}" tile="{int(c["rtv_tile"])}" '
        f'threads="{_setting(c["rtv_threads"])}" />\n'
        f'    <calibration file="{c["rtv_calib_file"]}" invert="{invert}" />\n'
        '</reactivision>\n'
    )


def _attr(el, name, conv=str):
    if el is None or name not in el.attrib:
        return None
    v = el.attrib[name]
    try:
        return conv(v)
    except (TypeError, ValueError):
        return v


def _int_or_str(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return str(v)


def _xbool(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def _root(path):
    try:
        return ET.parse(path).getroot()
    except Exception:
        return None


def import_xml(camera_xml_path, rtv_xml_path):
    """Read an existing camera.xml + reacTIVision.xml pair into rtv_* keys
    (only tags that are present). Comments are ignored by ElementTree, so the
    commented-out reference block in reacTIVision.xml is not picked up."""
    out = {}
    root = _root(camera_xml_path) if camera_xml_path else None
    if root is not None:
        cam = root.find("camera")
        if cam is not None:
            v = _attr(cam, "id", int)
            if v is not None:
                out["rtv_camera"] = v
            cap = cam.find("capture")
            for k, a, conv in (("rtv_width", "width", int), ("rtv_height", "height", int),
                               ("rtv_fps", "fps", _int_or_str),
                               ("rtv_compress", "compress", _xbool)):
                v = _attr(cap, a, conv)
                if v is not None:
                    out[k] = v
            st = cam.find("settings")
            for k in _SETTING_KEYS:
                v = _attr(st, k, _int_or_str)
                if v is not None:
                    out["rtv_" + k] = v
            fr = cam.find("frame")
            for k, a, conv in (("rtv_frame_width", "width", _int_or_str),
                               ("rtv_frame_height", "height", _int_or_str),
                               ("rtv_xoff", "xoff", int), ("rtv_yoff", "yoff", int)):
                v = _attr(fr, a, conv)
                if v is not None:
                    out[k] = v
    root = _root(rtv_xml_path) if rtv_xml_path else None
    if root is not None:
        spec = (
            ("tuio", "port", "rtv_tuio_port", int),
            ("finger", "size", "rtv_finger_size", int),
            ("finger", "sensitivity", "rtv_finger_sensitivity", int),
            ("fiducial", "engine", "rtv_engine", str),
            ("fiducial", "tree", "rtv_tree", str),
            ("image", "display", "rtv_display", str),
            ("image", "equalize", "rtv_equalize", _xbool),
            ("image", "fullscreen", "rtv_fullscreen", _xbool),
            ("threshold", "gradient", "rtv_gradient", int),
            ("threshold", "tile", "rtv_tile", int),
            ("threshold", "threads", "rtv_threads", _int_or_str),
            ("calibration", "file", "rtv_calib_file", str),
            ("calibration", "invert", "rtv_calib_invert", lambda s: s.strip()),
        )
        for tag, a, key, conv in spec:
            v = _attr(root.find(tag), a, conv)
            if v is not None:
                out[key] = v
    return out


def write_configs(cfg, work_dir):
    """Write camera.xml + reacTIVision.xml for `cfg` into work_dir (normally
    the exe folder; atomic replace). A pre-existing file is backed up once as
    *.orig so the distribution's settings can still be imported. Returns the
    two absolute paths."""
    os.makedirs(work_dir, exist_ok=True)
    cam_path = os.path.abspath(os.path.join(work_dir, "camera.xml"))
    rtv_path = os.path.abspath(os.path.join(work_dir, "reacTIVision.xml"))
    for path, text in ((cam_path, build_camera_xml(cfg)),
                       (rtv_path, build_reactivision_xml(cfg))):
        if os.path.isfile(path) and not os.path.isfile(path + ".orig"):
            try:
                with open(path, "rb") as src, open(path + ".orig", "wb") as dst:
                    dst.write(src.read())
            except Exception:
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="latin-1", errors="replace") as f:
            f.write(text)
        os.replace(tmp, path)
    return cam_path, rtv_path


# ---------------------------------------------------------------- process

_RE_FORMAT = re.compile(r"format:\s*(\d+)x(\d+),\s*(\d+)\s*fps", re.I)
_RE_CAMERA = re.compile(r"^\s*camera:\s*(.+?)\s*$", re.I)
_RE_ERROR = re.compile(r"no camera|could not|couldn't|cannot|failed|error|not found", re.I)


def _parse_stdout_line(line):
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


class ReactivisionProcess:
    """Owns one reacTIVision.exe child. Never raises from start(): a missing
    exe / busy camera shows up in status()["error"] instead."""

    def __init__(self, exe="", work_dir=None):
        self._lock = threading.RLock()
        self.exe_override = exe
        self.work_dir = work_dir          # None = the exe folder (normal)
        self.proc = None
        self.log = deque(maxlen=300)
        self.format = None            # (w, h, fps) as reported by reacTIVision
        self.format_source = None     # "stdout" | "configured"
        self.camera_name = ""
        self.error = None
        self.exe = ""
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
            exe = find_exe(cfg.get("rtv_exe") or self.exe_override)
            if not exe:
                self.error = ("reacTIVision.exe 없음 - tools/reactivision/ 에 복사하거나 "
                              "rtv_exe 경로를 지정하세요")
                self._log(self.error)
                return self.status()
            try:
                exe = ensure_local_install(exe)
            except Exception as exc:
                self._log(f"local copy failed ({exc}); running in place")
            self.exe = exe
            work_dir = self.work_dir or os.path.dirname(exe)
            modes = list_modes(exe)
            full = resolve_mode(cfg, modes["cameras"])
            try:
                cam_path, rtv_path = write_configs(full, work_dir)
            except Exception as exc:
                self.error = f"XML 쓰기 실패: {exc}"
                self._log(self.error)
                return self.status()
            self.xml = {"camera": cam_path, "rtv": rtv_path}
            self.format = (int(full["rtv_width"]), int(full["rtv_height"]),
                           int(full["rtv_fps"]))
            self.format_source = "configured"
            # no -c: reacTIVision loads reacTIVision.xml + camera.xml from its
            # own folder (cwd). "-n" must be the ONLY argument when used.
            args = [exe] + (["-n"] if full.get("rtv_no_window") else [])
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
                self.error = (f"reacTIVision 즉시 종료 (code {self.proc.returncode}) - "
                              "카메라가 다른 프로그램에 잡혀 있거나 설정이 잘못됨")
        return self.status()

    def stop(self, timeout=3.0):
        with self._lock:
            p = self.proc
            self.proc = None
        if p is None:
            return
        try:
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
                kind = _parse_stdout_line(line)
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
                self.error = f"reacTIVision 종료 (code {rc})"
            self._log(f"exited rc={rc}")

    def status(self):
        with self._lock:
            running = self.proc is not None and self.proc.poll() is None
            fmt = self.format
            return {
                "running": running,
                "pid": self.proc.pid if running else None,
                "exe": self.exe,
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


# ---------------------------------------------------------------- TUIO

def tuio_angle_deg(rad, offset=0.0):
    """TUIO angle (radians, 0..2π, screen-clockwise positive) -> degrees
    wrapped to -180..180. Same convention as the tuner's `tilt` (image
    coordinates, +y down, clockwise positive), so no sign flip."""
    return ((math.degrees(float(rad)) + float(offset) + 180.0) % 360.0) - 180.0


@dataclass
class ExhibitObject:
    """One fiducial as the pipeline sees it. `code_id` is the reacTIVision
    symbol id (the object's identity); `track_id` the TUIO session id."""
    track_id: int
    code_id: int
    nx: float                 # TUIO x (0..1 of the camera frame)
    ny: float
    angle: float              # degrees, -180..180
    rad: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    va: float = 0.0
    motion_accel: float = 0.0
    rot_accel: float = 0.0
    cx: float = 0.0           # canvas pixels (filled by the pipeline)
    cy: float = 0.0
    flip: bool = False
    pitch: float | None = None
    status: str = "tuio"

    @property
    def bits(self):           # tuner-compat: identity as a string
        return str(self.code_id)

    @property
    def raw_id(self):
        return self.code_id

    @property
    def label(self):
        return f"ID{self.code_id:02d}"


class TuioReceiver:
    """TUIO 1.1 /tuio/2Dobj listener. reacTIVision sends one bundle per
    camera frame: alive, set*, fseq. A blocking (single-thread) OSC server
    keeps those in order; the frame is committed on `fseq`."""

    def __init__(self, host="127.0.0.1", port=3333, angle_offset=0.0):
        self.host = host
        self.port = int(port)
        self.angle_offset = float(angle_offset)
        self._cond = threading.Condition()
        self._objects = {}          # session id -> ExhibitObject (committed)
        self._pending = {}          # updates since the last fseq
        self._alive = None          # session ids in the current bundle
        self.frame_no = 0
        self.last_fseq = -1
        self.last_rx = 0.0
        self.fps = 0.0
        self._last_t = None
        self.error = None
        self._server = None
        self._thread = None
        #: optional callback(objects, fseq) run on the receiver thread right
        #: after each frame is committed — the lowest-latency hook for OSC
        self.on_frame = None
        self.on_frame_error = None

    # -- lifecycle ------------------------------------------------------

    def start(self):
        try:
            from pythonosc.dispatcher import Dispatcher
            from pythonosc.osc_server import BlockingOSCUDPServer
            disp = Dispatcher()
            disp.map("/tuio/2Dobj", self._on_obj)
            self._server = BlockingOSCUDPServer((self.host, self.port), disp)
        except Exception as exc:
            self.error = f"TUIO 포트 {self.port} 열기 실패: {exc}"
            self._server = None
            return self
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="tuio")
        self._thread.start()
        return self

    def stop(self):
        srv = self._server
        self._server = None
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(2.0)
            self._thread = None

    # -- OSC handler (server thread) -------------------------------------

    def _on_obj(self, addr, *args):
        if not args:
            return
        kind = args[0]
        if kind == "set" and len(args) >= 11:
            s, i, x, y, a, X, Y, A, m, r = args[1:11]
            self._pending[int(s)] = ExhibitObject(
                track_id=int(s), code_id=int(i), nx=float(x), ny=float(y),
                angle=tuio_angle_deg(a, self.angle_offset), rad=float(a),
                vx=float(X), vy=float(Y), va=float(A),
                motion_accel=float(m), rot_accel=float(r))
        elif kind == "alive":
            self._alive = {int(v) for v in args[1:]}
        elif kind == "fseq":
            self._commit(int(args[1]) if len(args) > 1 else -1)

    def _commit(self, fseq):
        with self._cond:
            merged = dict(self._objects)
            merged.update(self._pending)
            if self._alive is not None:
                merged = {s: o for s, o in merged.items() if s in self._alive}
            self._objects = merged
            self._pending = {}
            self._alive = None
            self.last_fseq = fseq
            self.frame_no += 1
            # perf_counter, not monotonic: on Windows monotonic ticks every
            # 15.6 ms, which caps a per-frame fps estimate at ~64
            now = time.perf_counter()
            if self._last_t is not None:
                dt = now - self._last_t
                if dt > 0:
                    self.fps += 0.1 * (1.0 / dt - self.fps)
            self._last_t = now
            self.last_rx = now
            self._cond.notify_all()
            objs = [replace(o) for _, o in sorted(merged.items())]
        cb = self.on_frame
        if cb is not None:                  # outside the lock: sending must not block readers
            try:
                cb(objs, fseq)
            except Exception as exc:        # never let a send error kill the receiver
                self.on_frame_error = str(exc)

    # -- pipeline side ----------------------------------------------------

    def wait_frame(self, timeout=0.1, since=None):
        """Block until a TUIO frame newer than `since` (default: now) is
        committed. Returns True on a new frame, False on timeout."""
        with self._cond:
            seen = self.frame_no if since is None else since
            self._cond.wait_for(lambda: self.frame_no != seen, timeout)
            return self.frame_no != seen

    def snapshot(self):
        with self._cond:
            return [replace(o) for _, o in sorted(self._objects.items())]

    def age(self):
        """Seconds since the last committed frame (inf before the first)."""
        if not self.last_rx:
            return float("inf")
        return time.perf_counter() - self.last_rx
