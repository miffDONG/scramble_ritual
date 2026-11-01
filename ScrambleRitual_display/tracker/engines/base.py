"""What both trackers share: the camera.xml format, camera-mode choice, and the engine
interface the process manager (tracker/process.py) drives.

Config keys shared by both engines keep their historical `rtv_` prefix (camera.xml is the
same file format for reacTIVision and Ritualvision), so saved exhibit configs stay valid:

  camera.xml <camera id>                      rtv_camera
             <capture width height fps compress>   rtv_width rtv_height rtv_fps rtv_compress
             <settings brightness ... focus>       rtv_brightness ... rtv_focus
             <frame width height xoff yoff>        rtv_frame_width rtv_frame_height rtv_xoff rtv_yoff
"""

import os
from xml.etree import ElementTree as ET

COMMON_DEFAULTS = {
    "tracker": "reactivision",   # reactivision | ritualvision (exhibition_start.bat --tracker, 마지막 선택 유지)
    "rtv_autostart": True,       # 서버 시작 시 트래커 자동 실행
    "rtv_no_window": False,      # -n : 트래커 창 없이 실행
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
    # reacTIVision.xml tags both trackers read
    "rtv_tuio_port": 3333,
    "rtv_display": "src",        # src | dest | none
    "rtv_equalize": False,
    # pipeline side
    "rtv_angle_offset": 0.0,     # tilt 0° 기준 보정(도)
    "rtv_stale_s": 1.0,          # TUIO가 이 시간 이상 끊기면 오브제 없음 처리
}

SETTING_KEYS = ("brightness", "contrast", "gain", "shutter", "exposure",
                "sharpness", "gamma", "focus")


# ---------------------------------------------------------------- camera modes

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


# ---------------------------------------------------------------- XML helpers

def setting_text(v):
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


def bool_text(v):
    return "true" if v else "false"


def attr(el, name, conv=str):
    if el is None or name not in el.attrib:
        return None
    v = el.attrib[name]
    try:
        return conv(v)
    except (TypeError, ValueError):
        return v


def int_or_str(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return str(v)


def xbool(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def xml_root(path):
    try:
        return ET.parse(path).getroot()
    except Exception:
        return None


def build_camera_xml(cfg):
    c = {**COMMON_DEFAULTS, **cfg}
    settings = " ".join(f'{k}="{setting_text(c["rtv_" + k])}"' for k in SETTING_KEYS)
    return (
        '<?xml version="1.0" encoding="ISO-8859-1" ?>\n'
        '<portvideo>\n'
        f'    <camera id="{int(c["rtv_camera"])}">\n'
        f'        <capture width="{int(c["rtv_width"])}" height="{int(c["rtv_height"])}" '
        f'fps="{setting_text(c["rtv_fps"])}" compress="{bool_text(c["rtv_compress"])}" />\n'
        f'        <settings {settings} />\n'
        f'        <frame width="{setting_text(c["rtv_frame_width"])}" '
        f'height="{setting_text(c["rtv_frame_height"])}" '
        f'xoff="{int(c["rtv_xoff"])}" yoff="{int(c["rtv_yoff"])}" />\n'
        '    </camera>\n'
        '</portvideo>\n'
    )


def import_camera_xml(camera_xml_path):
    """camera.xml -> rtv_* keys (only tags that are present)."""
    out = {}
    root = xml_root(camera_xml_path) if camera_xml_path else None
    cam = root.find("camera") if root is not None else None
    if cam is None:
        return out
    v = attr(cam, "id", int)
    if v is not None:
        out["rtv_camera"] = v
    cap = cam.find("capture")
    for k, a, conv in (("rtv_width", "width", int), ("rtv_height", "height", int),
                       ("rtv_fps", "fps", int_or_str), ("rtv_compress", "compress", xbool)):
        v = attr(cap, a, conv)
        if v is not None:
            out[k] = v
    st = cam.find("settings")
    for k in SETTING_KEYS:
        v = attr(st, k, int_or_str)
        if v is not None:
            out["rtv_" + k] = v
    fr = cam.find("frame")
    for k, a, conv in (("rtv_frame_width", "width", int_or_str),
                       ("rtv_frame_height", "height", int_or_str),
                       ("rtv_xoff", "xoff", int), ("rtv_yoff", "yoff", int)):
        v = attr(fr, a, conv)
        if v is not None:
            out[k] = v
    return out


def write_files(work_dir, files, encoding="latin-1"):
    """Write {name: text} into work_dir (atomic replace). A pre-existing file is backed up
    once as *.orig so the distribution's own settings can still be imported. Returns
    {name: absolute path}."""
    os.makedirs(work_dir, exist_ok=True)
    out = {}
    for name, text in files.items():
        path = os.path.abspath(os.path.join(work_dir, name))
        if os.path.isfile(path) and not os.path.isfile(path + ".orig"):
            try:
                with open(path, "rb") as src, open(path + ".orig", "wb") as dst:
                    dst.write(src.read())
            except Exception:
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding=encoding, errors="replace") as f:
            f.write(text)
        os.replace(tmp, path)
        out[name] = path
    return out


def original_xml_path(exe_dir, name):
    """The file to IMPORT from: the *.orig backup once we have overwritten the live file."""
    live = os.path.join(exe_dir, name)
    return live + ".orig" if os.path.isfile(live + ".orig") else live


# ---------------------------------------------------------------- engine interface

class TrackerEngine:
    """One tracker program. The process manager calls these in order on start:
    find_exe -> prepare -> list_modes -> write_configs -> args."""

    name = ""
    label = ""
    #: settings only this engine uses (the UI shows them when this engine is selected)
    fields = ()
    #: one-file bundles (PyInstaller) run as launcher + child: stop the whole tree
    kill_tree = False

    def find_exe(self, cfg, override=""):
        raise NotImplementedError

    def missing_exe_message(self):
        return f"{self.label} 실행 파일 없음"

    def prepare(self, exe, log):
        """Last step before running `exe` (e.g. copying it to a folder we own)."""
        return exe

    def list_modes(self, exe, refresh=False):
        """{"cameras": [...], "raw", "t", "error", "stale"}; empty when the engine cannot
        enumerate camera modes (the configured mode is then used as is)."""
        import time
        return {"cameras": [], "raw": "", "t": time.time(), "stale": False, "error": None}

    def write_configs(self, cfg, work_dir):
        """Write the engine's config files into work_dir; returns {name: path}."""
        raise NotImplementedError

    def import_configs(self, exe_dir):
        """Read the engine's config files back into settings keys."""
        raise NotImplementedError

    def args(self, cfg, exe):
        return [exe] + (["-n"] if cfg.get("rtv_no_window") else [])
