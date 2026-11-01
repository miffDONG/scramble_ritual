"""Config: reads the same camera.xml / reacTIVision.xml files reacTIVision uses.

Ritualvision looks in its own folder (next to Ritualvision.exe, or the project root when
run from source). A ritualvision.xml, if present, is read instead of reacTIVision.xml and may
add a <recognizer> element; everything else keeps reacTIVision's format so the exhibit server
can write these files unchanged.
"""

import os
import sys
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def bundled(name):
    """A data file shipped inside the exe (PyInstaller) or in the project root."""
    root = getattr(sys, "_MEIPASS", None) or base_dir()
    return os.path.join(root, name)


@dataclass
class CameraConfig:
    camera_id: int = 0
    width: int = 1280
    height: int = 800
    fps: int = 60
    compress: bool = True
    settings: dict = field(default_factory=dict)       # brightness, gain, exposure, focus ...
    frame_width: str = "max"
    frame_height: str = "max"
    xoff: int = 0
    yoff: int = 0


@dataclass
class AppConfig:
    tuio_host: str = "127.0.0.1"
    tuio_port: int = 3333
    display: str = "src"            # src | dest | none
    equalize: bool = False
    # recognizer
    block: int = 15                 # adaptive threshold window (px, odd)
    offset: float = 12.0            # a pixel is "line" when darker than local mean - offset
    invert: bool = False            # true when lines are brighter than the tile
    min_anchor_px: float = 12.0     # anchor arm length limits (px)
    max_anchor_px: float = 400.0
    hold_frames: int = 3            # keep a lost marker alive this many frames
    ids_file: str = "ids.json"


def _int(v, d):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def _bool(v, d=False):
    if v is None:
        return d
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def load_camera(path):
    cfg = CameraConfig()
    if not os.path.exists(path):
        return cfg
    cam = ET.parse(path).getroot().find("camera")
    if cam is None:
        return cfg
    cfg.camera_id = _int(cam.get("id"), 0)
    cap = cam.find("capture")
    if cap is not None:
        cfg.width = _int(cap.get("width"), cfg.width)
        cfg.height = _int(cap.get("height"), cfg.height)
        cfg.fps = _int(cap.get("fps"), cfg.fps)
        cfg.compress = _bool(cap.get("compress"), cfg.compress)
    st = cam.find("settings")
    if st is not None:
        cfg.settings = dict(st.attrib)
    fr = cam.find("frame")
    if fr is not None:
        cfg.frame_width = fr.get("width", "max")
        cfg.frame_height = fr.get("height", "max")
        cfg.xoff = _int(fr.get("xoff"), 0)
        cfg.yoff = _int(fr.get("yoff"), 0)
    return cfg


def load_app(path, extra=None):
    """reacTIVision.xml (written by the exhibit server), then ritualvision.xml on top for
    the Ritualvision-only <recognizer> settings, so the server never overwrites them."""
    cfg = AppConfig()
    for p in (path, extra):
        if p and os.path.exists(p):
            _apply_app(cfg, ET.parse(p).getroot())
    return cfg


def _apply_app(cfg, root):
    t = root.find("tuio")
    if t is not None:
        cfg.tuio_host = t.get("host", cfg.tuio_host)
        cfg.tuio_port = _int(t.get("port"), cfg.tuio_port)
    im = root.find("image")
    if im is not None:
        cfg.display = im.get("display", cfg.display)
        cfg.equalize = _bool(im.get("equalize"), cfg.equalize)
    rec = root.find("recognizer")          # Ritualvision-only element
    if rec is not None:
        cfg.block = _int(rec.get("block"), cfg.block)
        try:
            cfg.offset = float(rec.get("offset", cfg.offset))
        except ValueError:
            pass
        cfg.invert = _bool(rec.get("invert"), cfg.invert)
        cfg.hold_frames = _int(rec.get("hold"), cfg.hold_frames)
        cfg.ids_file = rec.get("ids", cfg.ids_file)


def resolve_files(conf_dir=None):
    """camera.xml, reacTIVision.xml, ritualvision.xml (the last may not exist)."""
    d = conf_dir or base_dir()
    return (os.path.join(d, "camera.xml"), os.path.join(d, "reacTIVision.xml"),
            os.path.join(d, "ritualvision.xml"))
