"""reacTIVision 1.5.1 (amoeba fiducials).

The exhibition server does not touch the camera itself. It
  1. writes camera.xml / reacTIVision.xml INTO THE EXE FOLDER (reacTIVision
     1.5.1 only loads them implicitly from there: an explicit
     <camera config=...> tag, or -c pointing at another folder, breaks its
     path resolution; the distribution's originals are kept as *.orig),
  2. launches reacTIVision.exe with no arguments (-n alone when headless).

reacTIVision.xml keys (`rtv_*`):
  <tuio host port>                rtv_tuio_port (host fixed 127.0.0.1)
  <finger size sensitivity>       rtv_finger_size rtv_finger_sensitivity
  <fiducial engine tree>          rtv_engine rtv_tree ("" = default.trees,
                                  "small" -> symbols/amoeba/small.trees)
  <image display equalize fullscreen>   rtv_display rtv_equalize rtv_fullscreen
  <threshold gradient tile threads>     rtv_gradient rtv_tile rtv_threads
  <calibration file invert>       rtv_calib_file rtv_calib_invert
"""

import json
import os
import re
import subprocess
import time
from xml.sax.saxutils import quoteattr

from tracker.engines.base import (
    COMMON_DEFAULTS, TrackerEngine, attr, bool_text, build_camera_xml, import_camera_xml,
    int_or_str, original_xml_path, setting_text, write_files, xbool, xml_root,
)

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

RTV_ONLY_DEFAULTS = {
    "rtv_exe": "",               # "" = 자동 탐색 (~/scramble_ritual/reactivision → Downloads)
    "rtv_finger_size": 0,        # 0 = 손가락 추적 끔
    "rtv_finger_sensitivity": 75,
    "rtv_engine": "amoeba",      # amoeba | classic
    "rtv_tree": "",              # "" = default.trees, "small" = symbols/amoeba/small.trees
    "rtv_fullscreen": False,
    "rtv_gradient": 32,          # 이진화 gradient gate
    "rtv_tile": 10,              # 이진화 tile size
    "rtv_threads": "max",
    "rtv_calib_file": "default.grid",
    "rtv_calib_invert": "",      # "" | x | y | a 조합 (예: xya)
}


def no_window_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
    return (original_xml_path(exe_dir, "camera.xml"),
            original_xml_path(exe_dir, "reacTIVision.xml"))


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


_MODES_CACHE = {}
#: last complete enumeration, kept on disk so the resolution/fps lists still
#: work while the camera is busy (e.g. our own tracker child is running)
MODES_FILE = os.path.join(RTV_WORK_DIR, "modes.json")   # next to the exe


def _has_modes(cameras):
    return any(m for c in cameras for f in c["formats"] for m in f["modes"])


def _load_modes_file(path=MODES_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if _has_modes(d.get("cameras", [])) else None
    except Exception:
        return None


def _save_modes_file(out, path=MODES_FILE):
    try:
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
                           errors="replace", creationflags=no_window_flags())
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


# ---------------------------------------------------------------- XML

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
    c = {**COMMON_DEFAULTS, **RTV_ONLY_DEFAULTS, **cfg}
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
        f'    <image display="{c["rtv_display"]}" equalize="{bool_text(c["rtv_equalize"])}" '
        f'fullscreen="{bool_text(c["rtv_fullscreen"])}" />\n'
        f'    <threshold gradient="{int(c["rtv_gradient"])}" tile="{int(c["rtv_tile"])}" '
        f'threads="{setting_text(c["rtv_threads"])}" />\n'
        f'    <calibration file="{c["rtv_calib_file"]}" invert="{invert}" />\n'
        '</reactivision>\n'
    )


def import_reactivision_xml(rtv_xml_path):
    """reacTIVision.xml -> rtv_* keys (only tags that are present). Comments are ignored
    by ElementTree, so the commented-out reference block is not picked up."""
    out = {}
    root = xml_root(rtv_xml_path) if rtv_xml_path else None
    if root is None:
        return out
    spec = (
        ("tuio", "port", "rtv_tuio_port", int),
        ("finger", "size", "rtv_finger_size", int),
        ("finger", "sensitivity", "rtv_finger_sensitivity", int),
        ("fiducial", "engine", "rtv_engine", str),
        ("fiducial", "tree", "rtv_tree", str),
        ("image", "display", "rtv_display", str),
        ("image", "equalize", "rtv_equalize", xbool),
        ("image", "fullscreen", "rtv_fullscreen", xbool),
        ("threshold", "gradient", "rtv_gradient", int),
        ("threshold", "tile", "rtv_tile", int),
        ("threshold", "threads", "rtv_threads", int_or_str),
        ("calibration", "file", "rtv_calib_file", str),
        ("calibration", "invert", "rtv_calib_invert", lambda s: s.strip()),
    )
    for tag, a, key, conv in spec:
        v = attr(root.find(tag), a, conv)
        if v is not None:
            out[key] = v
    return out


def import_xml(camera_xml_path, rtv_xml_path):
    """Read an existing camera.xml + reacTIVision.xml pair into rtv_* keys."""
    return {**import_camera_xml(camera_xml_path), **import_reactivision_xml(rtv_xml_path)}


def write_configs(cfg, work_dir):
    """Write camera.xml + reacTIVision.xml for `cfg` into work_dir (normally
    the exe folder). Returns the two absolute paths."""
    p = write_files(work_dir, {"camera.xml": build_camera_xml(cfg),
                               "reacTIVision.xml": build_reactivision_xml(cfg)})
    return p["camera.xml"], p["reacTIVision.xml"]


class ReactivisionEngine(TrackerEngine):
    name = "reactivision"
    label = "reacTIVision"
    fields = tuple(RTV_ONLY_DEFAULTS)

    def find_exe(self, cfg, override=""):
        return find_exe(cfg.get("rtv_exe") or override)

    def missing_exe_message(self):
        return ("reacTIVision.exe 없음 - tools/reactivision/ 에 복사하거나 "
                "rtv_exe 경로를 지정하세요")

    def prepare(self, exe, log):
        try:
            return ensure_local_install(exe)
        except Exception as exc:
            log(f"local copy failed ({exc}); running in place")
            return exe

    def list_modes(self, exe, refresh=False):
        return list_modes(exe, refresh=refresh)

    def write_configs(self, cfg, work_dir):
        cam, rtv = write_configs(cfg, work_dir)
        return {"camera": cam, "rtv": rtv}

    def import_configs(self, exe_dir):
        return import_xml(*original_xml_paths(exe_dir))
