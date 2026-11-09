"""
Scramble Ritual — exhibition server.

    USB camera -> marker tracker child process, chosen by the `tracker` setting:
                    reacTIVision.exe (amoeba markers, ~/scramble_ritual/reactivision/) or
                    Ritualvision.exe (edge-dash markers, ~/scramble_ritual/Ritualvision/);
                  its camera.xml / reacTIVision.xml (/ ritualvision.xml) are generated
                  into the tracker's own folder (tracker/engines)
               -> TUIO /tuio/2Dobj on UDP 127.0.0.1:3333
               -> this pipeline: ROI normalize -> tension graph -> OSC
               -> SOUND /scramble/obj + TD per-frame OSC bundle

The tracker is the only recognition method: no OpenCV detection in this server, no
simulation, no video/image sources. The web UI exposes what the exhibition
needs — tracker settings, the table region, object size, tension knobs
(MST graph by default), OSC targets, profiles and a monitor.

Run:  python -m webui.exhibit_server          (scripts/exhibition_start.bat)
      python -m webui.exhibit_server --test   (scripts/test_start.bat)

--test: reacTIVision is neither started nor listened to (no camera, no TUIO
port). Hand-placed virtual hexagon objects (IDs 0..24, tracker/virtual.py)
are emitted as frames at the camera fps instead; everything downstream —
ROI, tension, OSC targets and format, config file — is the exhibition path.
"""

import argparse
import json
import math
import os
import re
import statistics
import sys
import threading
import time
from dataclasses import replace

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_from_directory

from tracker.engines import (
    TRACKER_DEFAULTS, TRACKERS, engine_name, engines_info, get_engine, tracker_exe,
)
from tracker.engines.base import default_mode
from tracker.engines.reactivision import (
    INSTALL_DIR, find_exe, import_xml, list_modes, original_xml_paths,
)
from tracker.engines.ritualvision import (
    CONTACT_RC_RATIO, RC_PER_R, SHAPE_TOL, size_range,
)
from tracker.process import TrackerProcess
from tracker.tuio import ExhibitObject, TuioReceiver
from tracker.idfix import IdFixer, IdModel, default_trees_path, parse_trees, tree_steps
from tracker.motion import MotionGate
from tracker.virtual import LIMIT as VIRTUAL_LIMIT, VirtualSource, VirtualTable
from tracker.ritual import RitualState
from tracker.tension import (
    draw_tension_graph, roi_to_px, tension_color,
    tension_thresholds,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "exhibit_config.json")
CONFIGS_DIR = os.path.join(HERE, "..", "configs", "exhibit")
STATIC_DIR = os.path.join(HERE, "static")
TEST_OBJECTS_PATH = os.path.join(HERE, "test_objects.json")   # --test: virtual objects
IDFIX_COUNTS_PATH = os.path.join(CONFIGS_DIR, "id_confusion.json")   # learned misreads

# OSC payload field order (list format = these positions; dict/json = keys).
# `id` is the reacTIVision fiducial symbol id — the object's identity.
OSC_OBJ_FIELDS = ["id", "bits", "x", "y", "tilt", "tension", "pairId", "heartbeat"]
BITS_WIDTH = 8   # amoeba ids are 0..215 -> 8 bits, zero-padded ("00001100")


def id_bits(code_id):
    """Fiducial id as a fixed-width binary string (`bits`; same width as the
    Morse-era field, now derived from the reacTIVision symbol number)."""
    return format(int(code_id), f"0{BITS_WIDTH}b")

# Default payload format is dict (k, v, k, v ...: "id", 12, "bits", "00001100", ...);
# per target it can be switched to list (positional) or json (one string argument).
DEFAULT_OSC_FORMAT = "dict"
DEFAULT_OSC_TARGETS = [
    {"name": "SuperCollider", "host": "127.0.0.1", "port": 57120,
     "format": DEFAULT_OSC_FORMAT, "enabled": True},
    {"name": "TouchDesigner", "host": "127.0.0.1", "port": 7000,
     "format": "td", "enabled": True},
]

EXHIBIT_DEFAULTS = {
    **TRACKER_DEFAULTS,
    # OSC
    "osc_enabled": False,
    "osc_prefix": "/scramble",
    "osc_targets": [dict(t) for t in DEFAULT_OSC_TARGETS],
    # table region (normalized [x, y, w, h] of the camera frame) and object size
    "roi": None,
    "object_side_px": 0.0,       # measured triangle side (canvas px); 0 = use tension_contact
    "tension_contact": 0.06,     # d_near (normalized by the table long side) when side_px is 0
    "tension_margin": 0.012,     # contact plateau extension, normalized by table long side
    # how tension_contact was set: measured (two touching markers) | physical
    # (object_side_mm / table_long_mm) | manual (typed / default) — display only
    "tension_contact_src": "manual",
    "object_side_mm": 0.0,       # physical object side, for the "physical" method
    "table_long_mm": 0.0,        # physical table long side (= ROI long side)
    # tension graph knobs
    "tension_connect": "mst",    # mst (최소 신장 트리, 기본) | all | near | knn
    "tension_fold": "max",       # max | avg | min
    "tension_knn": 3,
    "tension_link_radius": 0.2,
    "show_graph": True,
    # marker-id correction (tracker/idfix.py). Off: the id reacTIVision sends
    # goes out unchanged (ids >= 25 and duplicates included); on: tracked,
    # corrected, unique ids 0..24
    "idfix_enabled": False,
    "idfix_gate": 0.5,           # track gate, x d_near (contact centre distance)
    "idfix_lost_s": 0.5,         # a track unseen this long is dropped (max 0.5)
    "idfix_first_delay_s": 0.1,  # a new object waits up to this for a confident id
    "idfix_switch_frames": 15,   # frames a better id must lead before the output id changes
    "idfix_learn": False,        # count misreads of locked tracks into id_confusion.json
    "idfix_memory_s": 10.0,      # an id stays remembered at its last spot this long
    # lift cut (tracker/motion.py): an object moving faster than lift_cut_speed
    # (table long sides / s) for lift_cut_hold_s is not sent (carried off the
    # table, still decoded from below). Off: nothing is withheld.
    "lift_cut_enabled": False,
    "lift_cut_speed": 0.3,
    "lift_cut_hold_s": 0.1,
    # preview
    "proc_max_dim": 1280,
    "jpeg_quality": 80,
}


# ---------------------------------------------------------------- helpers

def safe_config_name(name):
    name = os.path.basename(str(name).strip())
    if name.lower().endswith(".json"):
        name = name[:-5]
    name = re.sub(r"[^\w \-가-힣]", "_", name).strip()
    return name[:64] or "config"


def list_config_names():
    if not os.path.isdir(CONFIGS_DIR):
        return []
    return sorted((f[:-5] for f in os.listdir(CONFIGS_DIR) if f.endswith(".json")),
                  key=str.lower)


def format_osc_args(fmt, fields, values):
    """list -> [v1, v2, ...]  ·  dict -> [k1, v1, k2, v2, ...]
       json -> ['{"k1": v1, ...}']  (single JSON string argument)"""
    if fmt == "json":
        return [json.dumps(dict(zip(fields, values)))]
    if fmt == "dict":
        return [a for k, v in zip(fields, values) for a in (k, v)]
    return list(values)


def osc_args_text(fmt, args):
    """Monitor text = the OSC arguments exactly as sent. json: the single
    string argument itself; list/dict: the argument array (strings quoted,
    ints and floats as transmitted)."""
    if fmt == "json":
        return str(args[0]) if args else ""
    return json.dumps(list(args), ensure_ascii=False)


def osc_targets(rt):
    """Normalized target rows from rt["osc_targets"]; invalid rows dropped."""
    out = []
    for i, t in enumerate(rt.get("osc_targets") or []):
        if not isinstance(t, dict):
            continue
        try:
            port = int(t.get("port"))
        except (TypeError, ValueError):
            continue
        host = str(t.get("host") or "127.0.0.1").strip() or "127.0.0.1"
        fmt = str(t.get("format") or DEFAULT_OSC_FORMAT)
        if str(t.get("name", "")).lower() in ("td", "touchdesigner"):
            fmt = "td"   # migrate existing named TD targets to the bundle contract
        if fmt not in ("list", "dict", "json", "td"):
            fmt = DEFAULT_OSC_FORMAT
        out.append({"name": str(t.get("name") or f"target{i + 1}"), "host": host,
                    "port": port, "format": fmt, "enabled": bool(t.get("enabled", True))})
    return out


def osc_xy(cx, cy, roi_px, W, H):
    """Canvas px -> the x, y sent over OSC: 0..1 of the ROI (whole canvas
    without one), clamped. x: left 0, right 1. y: bottom 0, top 1."""
    if roi_px:
        ox, oy, x2, y2 = roi_px
        rw, rh = max(x2 - ox, 1), max(y2 - oy, 1)
    else:
        ox, oy, rw, rh = 0, 0, max(W, 1), max(H, 1)
    x = max(0.0, min(1.0, (cx - ox) / rw))
    y = max(0.0, min(1.0, (cy - oy) / rh))
    return round(x, 4), round(1.0 - y, 4)


def object_json(o, tension=0.0, xy=None):
    """`xy`: the OSC x, y (osc_xy) — shown in the web table so it matches
    what receivers get; falls back to the raw TUIO values."""
    x, y = xy if xy else (round(o.nx, 4), round(o.ny, 4))
    return {"track": o.track_id, "id": o.code_id, "bits": id_bits(o.code_id),
            "label": o.label, "raw": o.src_id, "speed": o.speed,
            "x": x, "y": y,
            "cx": round(o.cx, 1), "cy": round(o.cy, 1),
            "angle": round(o.angle, 1), "tension": tension}


def hexagon_pts(cx, cy, r, angle_deg):
    """Regular hexagon (circumradius r) rotated by angle_deg -> int32 points."""
    pts = []
    for k in range(6):
        a = math.radians(angle_deg + 60.0 * k)
        pts.append((int(round(cx + r * math.cos(a))), int(round(cy + r * math.sin(a)))))
    return np.array(pts, np.int32)


def draw_exhibit_overlay(W, H, objs, graph, rtv_status, tuio_fps, roi_px, rt,
                         infer_on=True, test_mode=False, hex_r=0.0, camera=None):
    """Synthetic preview: reacTIVision owns the camera, so we draw what TUIO
    tells us on a blank canvas at the capture aspect. In test mode the
    virtual objects are drawn as hexagons sized so that two of them touch
    edge-to-edge exactly at tension 1 (centre distance = d_near).
    `camera`: a real frame (camera borrowed for ROI setup) drawn instead of
    the blank grid, stretched to the canvas like the TUIO coordinates."""
    if camera is not None:
        vis = cv2.resize(camera, (W, H), interpolation=cv2.INTER_AREA)
    else:
        vis = np.full((H, W, 3), 18, np.uint8)
        step = max(40, W // 16)
        for x in range(0, W, step):
            cv2.line(vis, (x, 0), (x, H), (32, 32, 32), 1)
        for y in range(0, H, step):
            cv2.line(vis, (0, y), (W, y), (32, 32, 32), 1)
    if roi_px:
        rx, ry, rx2, ry2 = roi_px
        cv2.rectangle(vis, (rx, ry), (rx2, ry2), (0, 220, 255), 2)
    side = float(rt.get("object_side_px", 0) or 0)
    radius = int(max(12, side * 0.35)) if side else 16
    if test_mode and hex_r > 0:
        radius = max(6, int(round(hex_r)))
    for o in objs:
        cx, cy = int(o.cx), int(o.cy)
        col = (255, 200, 0)
        if test_mode:
            cv2.polylines(vis, [hexagon_pts(cx, cy, radius, o.angle)], True, col, 2, cv2.LINE_AA)
        else:
            cv2.circle(vis, (cx, cy), radius, col, 2, cv2.LINE_AA)
        a = math.radians(o.angle)
        ex, ey = int(cx + math.cos(a) * radius * 1.5), int(cy + math.sin(a) * radius * 1.5)
        cv2.line(vis, (cx, cy), (ex, ey), col, 2, cv2.LINE_AA)
        fix = f"<{o.src_id}" if o.src_id >= 0 and o.src_id != o.code_id else ""
        cv2.putText(vis, f"#{o.code_id}{fix}  s{o.track_id}  {o.angle:.0f}",
                    (cx - 30, cy + radius + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    col, 1, cv2.LINE_AA)
    if graph and graph[0]:
        draw_tension_graph(vis, graph)
    st = rtv_status or {}
    run = "RUN" if st.get("running") else "STOP"
    # the running process's tracker, else the selected one
    name = (st.get("engine_label") if st.get("running") else None) or \
        get_engine(rt).label
    if camera is not None:
        hud = f"CAMERA (ROI setup) {W}x{H} - {name} paused, OSC idle"
    elif test_mode:
        hud = f"TEST (virtual, no tracker) {W}x{H} frames={tuio_fps:.0f}fps objs={len(objs)}"
    else:
        hud = (f"{name} {run} pid={st.get('pid') or '-'} {st.get('format') or '-'} "
               f"tuio={tuio_fps:.0f}fps objs={len(objs)} "
               + ("IDFIX ON" if rt.get("idfix_enabled") else "IDFIX OFF (raw ids)"))
    cv2.putText(vis, hud, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (255, 255, 255), 2, cv2.LINE_AA)
    if not infer_on:
        cv2.putText(vis, "OSC PAUSED", (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 0, 255), 2, cv2.LINE_AA)
    if st.get("error"):
        cv2.rectangle(vis, (0, H - 34), (W, H), (0, 0, 120), -1)
        cv2.putText(vis, str(st["error"])[:110], (10, H - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return vis


# ---------------------------------------------------------------- pipeline

class ExhibitPipeline(threading.Thread):
    """Owns the reacTIVision child + TUIO receiver. Every TUIO frame: ROI
    normalize -> tension graph -> OSC to all targets. Overlay at <= 30 Hz."""

    OVERLAY_HZ = 20.0          # preview rate; OSC runs at the TUIO frame rate
    test_mode = False          # --test: VirtualSource instead of reacTIVision/TUIO
    virtual = None
    _hex_r = 0.0
    PREVIEW_MAX_W = 800        # JPEG is encoded from a downscaled copy (cheap)

    def __init__(self, autostart=None, exe_override="", test_mode=False,
                 test_objects_path=TEST_OBJECTS_PATH, tracker=None):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.test_mode = bool(test_mode)
        self.rt = self._initial_config(exe_override)
        if autostart is not None and not self.test_mode:
            self.rt["rtv_autostart"] = bool(autostart)
        if tracker in TRACKERS and not self.test_mode:
            # an explicit choice is saved, so the next start without --tracker keeps it
            self.rt["tracker"] = tracker
            try:
                saved = self._load_saved(CFG_PATH)
                saved["tracker"] = tracker
                self._dump(CFG_PATH, saved)
            except Exception:
                pass
        self.rtv = TrackerProcess(exe=exe_override)
        # tracker (re)start shown in the UI: restarting -> playing (first TUIO frame of the
        # new process) or failed (none within RESTART_TIMEOUT_S)
        self._restart = {"phase": "", "t": 0.0, "rx": None, "base": 0, "reason": ""}
        self.virtual = VirtualTable(test_objects_path) if self.test_mode else None
        self._hex_r = 0.0
        self.tuio = None
        self._rtv_dirty = bool(self.rt.get("rtv_autostart", True)) and not self.test_mode
        self._tuio_dirty = True
        self._osc_clients = {}
        self._graph = None
        self._last_node_t = {}
        self.tension_basis = ""
        self.frames = {"overlay": None}
        self.frame_no = 0
        self.results = {"objects": [], "tuio_fps": 0.0, "size": [0, 0],
                        "infer": True, "osc": [], "osc_fps": 0.0, "osc_msgs": 0,
                        "error": None}
        self.infer_on = True
        self.osc_fps = 0.0
        self._osc_last_t = None
        self._overlay_last = 0.0
        self._size = (0, 0)
        self._last_objs = []        # last TUIO objects with canvas cx/cy (for measurements)
        self._last_osc_log = []     # monitor rows from the last frame sent (receiver thread)
        self._last_roi_px = None
        self._frame_lock = threading.Lock()   # TUIO thread vs send clock
        self._last_frame_t = 0.0
        self.active_name = ""
        self.running = True
        # camera borrowed from reacTIVision for ROI setup (opened/released on
        # the pipeline thread only; Flask threads just set _cam_want)
        self._cam_want = False
        self._cam = None
        self._cam_frame = None
        self._cam_error = None
        self._cam_seen_t = 0.0

    # -- config ------------------------------------------------------------

    def _initial_config(self, exe_override=""):
        rt = json.loads(json.dumps(EXHIBIT_DEFAULTS))
        saved = self._load_saved(CFG_PATH)
        if saved:
            rt.update({k: v for k, v in saved.items() if k in EXHIBIT_DEFAULTS})
        else:
            # first run: carry the current reacTIVision XML settings over, but
            # let the camera mode be chosen from the live -l list at 120 fps
            exe = find_exe(exe_override)
            if exe:
                rt.update(import_xml(*original_xml_paths(os.path.dirname(exe))))
            rt.update(rtv_width=0, rtv_height=0, rtv_fps=120)
            try:
                self._dump(CFG_PATH, rt)
            except Exception:
                pass
        rt["osc_targets"] = osc_targets(rt)
        return rt

    @staticmethod
    def _load_saved(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def _dump(path, data):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def _config_path(self, name):
        return os.path.join(CONFIGS_DIR, safe_config_name(name) + ".json")

    def get_rt(self):
        """Deep copy for API consumers (safe to mutate)."""
        with self.lock:
            return json.loads(json.dumps(self.rt))

    def _rt_view(self):
        """Cheap shallow copy for the hot loop (read-only use)."""
        with self.lock:
            return dict(self.rt)

    #: Ritualvision keys that only describe the last size measurement (no restart)
    RV_INFO_KEYS = ("rv_size_src", "rv_size_shape", "rv_size_sigma_pct")

    def _needs_restart(self, keys):
        """Tracker settings mark the child for restart: the shared rtv_* keys and the
        tracker choice always, Ritualvision's own keys only while it is the tracker."""
        rv = engine_name(self.rt) == "ritualvision"
        return any(k == "tracker" or k.startswith("rtv_")
                   or (rv and (k == "ritualvision_exe"
                               or (k.startswith("rv_") and k not in self.RV_INFO_KEYS)))
                   for k in keys)

    def _mark_restart(self):
        """Called with self.lock held whenever the child is marked for restart."""
        self._rtv_dirty = True
        self._tuio_dirty = True
        if not self.test_mode and self.rt.get("rtv_autostart", True):
            self._restart = {"phase": "restarting", "t": time.perf_counter(), "rx": None,
                             "base": 0, "reason": ""}

    def update(self, updates):
        """Apply runtime updates (only known keys). Tracker keys mark the child
        for restart; the restart itself happens on the pipeline thread."""
        applied = {}
        with self.lock:
            for k, v in (updates or {}).items():
                if k not in EXHIBIT_DEFAULTS:
                    continue
                if k == "osc_targets":
                    v = osc_targets({"osc_targets": v})
                self.rt[k] = v
                applied[k] = v
            if self._needs_restart(applied):
                self._mark_restart()
        return applied

    def reset_cfg(self):
        with self.lock:
            self.rt = json.loads(json.dumps(EXHIBIT_DEFAULTS))
            self._mark_restart()

    def _write_cfg(self, path):
        with self.lock:
            data = json.loads(json.dumps(self.rt))
        self._dump(path, data)

    def save_cfg(self, name=None):
        if name:
            name = safe_config_name(name)
            self._write_cfg(self._config_path(name))
            self.active_name = name
        self._write_cfg(CFG_PATH)
        return name or ""

    def load_cfg(self, name=None):
        path = self._config_path(name) if name else CFG_PATH
        data = self._load_saved(path)
        if not data:
            return False
        with self.lock:
            self.rt.update({k: v for k, v in data.items() if k in EXHIBIT_DEFAULTS})
            if "osc_targets" in data:
                self.rt["osc_targets"] = osc_targets(self.rt)
            self._mark_restart()
        if name:
            self.active_name = safe_config_name(name)
            self._write_cfg(CFG_PATH)
        return True

    # -- table region / object size ---------------------------------------

    def set_roi(self, roi):
        with self.lock:
            if not roi:
                self.rt["roi"] = None
            else:
                x, y, rw, rh = (float(v) for v in roi)
                x = max(0.0, min(1.0, x))
                y = max(0.0, min(1.0, y))
                rw = max(0.0, min(1.0 - x, rw))
                rh = max(0.0, min(1.0 - y, rh))
                self.rt["roi"] = [x, y, rw, rh] if rw > 0 and rh > 0 else None
            return self.rt["roi"]

    def calibrate_side(self, x1, y1, x2, y2):
        """One triangle side dragged on the preview (normalized endpoints)
        -> canvas pixels -> the tension d_near basis."""
        w, h = self._size
        if not w or not h:
            return {"ok": False, "error": "아직 캔버스가 없습니다"}
        side = math.hypot((float(x2) - float(x1)) * w, (float(y2) - float(y1)) * h)
        if side < 4:
            return {"ok": False, "error": "너무 짧습니다 — 한 변을 길게 그으세요"}
        with self.lock:
            self.rt["object_side_px"] = round(side, 1)
        return {"ok": True, "object_side_px": round(side, 1)}

    def _table_long_px(self, rt, W, H):
        roi_px = roi_to_px(rt.get("roi"), W, H)
        if roi_px:
            x1, y1, x2, y2 = roi_px
            return max(x2 - x1, y2 - y1, 1)
        return max(W, H, 1)

    def measure_contact(self):
        """Two markers placed edge-to-edge: their centre distance (normalized by
        the table long side) IS d_near, so store it as tension_contact. Uses the
        closest pair of the objects currently visible. Only the variable
        changes; the tension curve / graph logic stays as is."""
        W, H = self._size
        objs = list(self._last_objs)
        if not W or not H:
            return {"ok": False, "error": "아직 캔버스가 없습니다"}
        if len(objs) < 2:
            return {"ok": False, "error": "마커 2개를 변끼리 맞닿게 올려 두세요 (지금 보이는 마커: %d개)" % len(objs)}
        best = None
        for i in range(len(objs)):
            for j in range(i + 1, len(objs)):
                d = math.hypot(objs[i].cx - objs[j].cx, objs[i].cy - objs[j].cy)
                if best is None or d < best[0]:
                    best = (d, objs[i], objs[j])
        d_px, a, b = best
        if d_px < 2:
            return {"ok": False, "error": "두 마커가 같은 위치로 읽힙니다"}
        rt = self._rt_view()
        contact = round(d_px / self._table_long_px(rt, W, H), 4)
        # Ritualvision also reports each marker's size: the same two markers give the size
        # reference, and their centre distance / size checks they really touch edge to edge
        size = None
        if self.test_mode or engine_name(rt) == "ritualvision":
            size = self._contact_size(a, b)
        if size is not None and abs(size["ratio"] / CONTACT_RC_RATIO - 1.0) > SHAPE_TOL:
            return {"ok": False, "shape_ratio": round(size["ratio"], 3),
                    "error": (f"두 마커가 변끼리 맞닿지 않은 것 같습니다 (중심거리/크기 비율 "
                              f"{size['ratio']:.2f}, 기대 {CONTACT_RC_RATIO:.2f}). 다시 측정하세요.")}
        restart = False
        with self.lock:
            self.rt["tension_contact"] = contact
            self.rt["object_side_px"] = 0.0          # the measured contact wins
            self.rt["tension_contact_src"] = "measured"
            if size is not None:
                self.rt["rv_size_ref_px"] = round(size["ref"], 1)
                self.rt["rv_size_src"] = "measured"
                self.rt["rv_size_shape"] = round(size["ratio"], 3)
                self.rt["rv_size_sigma_pct"] = round(size["sigma_pct"], 2)
                if not self.test_mode and engine_name(self.rt) == "ritualvision":
                    self._mark_restart()             # Ritualvision picks the range up on restart
                    restart = True
        out = {"ok": True, "tension_contact": contact, "distance_px": round(d_px, 1),
               "ids": [a.code_id, b.code_id], "restart": restart}
        if size is not None:
            out.update(size_ref_px=round(size["ref"], 1), size_sigma_pct=round(size["sigma_pct"], 2),
                       shape_ratio=round(size["ratio"], 3), size_samples=size["n"])
        return out

    SIZE_SAMPLE_S = 1.0        # contact measurement: collect marker sizes this long

    def _contact_size(self, a, b):
        """Size reference from two markers placed edge to edge:
        {"ref": rc px, "sigma_pct", "ratio": centre distance / rc, "n"} or None when the
        tracker sends no sizes (reacTIVision, or an older Ritualvision.exe)."""
        if self.test_mode:                 # virtual hexagons: drawn with radius _hex_r
            if self._hex_r <= 0:
                return None
            rc = RC_PER_R * self._hex_r
            return {"ref": rc, "sigma_pct": 0.0, "n": 1,
                    "ratio": math.hypot(a.cx - b.cx, a.cy - b.cy) / rc}
        rx = self.tuio
        if not isinstance(rx, TuioReceiver):
            return None
        sizes, dists = [], []
        seen = rx.frame_no
        t_end = time.perf_counter() + self.SIZE_SAMPLE_S
        while time.perf_counter() < t_end:
            if not rx.wait_frame(timeout=0.1, since=seen):
                continue
            seen = rx.frame_no
            got = {o.track_id: o for o in rx.snapshot() if o.track_id in (a.track_id, b.track_id)}
            oa, ob = got.get(a.track_id), got.get(b.track_id)
            if oa is None or ob is None or oa.size_px <= 0 or ob.size_px <= 0 or not oa.frame_w:
                continue
            sizes += [oa.size_px, ob.size_px]
            # distance in camera px, the unit of the sizes (the canvas may be scaled)
            dists.append(math.hypot((oa.nx - ob.nx) * oa.frame_w, (oa.ny - ob.ny) * oa.frame_h))
        if not sizes:
            return None
        ref = statistics.median(sizes)
        return {"ref": ref, "sigma_pct": statistics.pstdev(sizes) / ref * 100.0,
                "ratio": statistics.median(dists) / ref, "n": len(dists)}

    def contact_from_physical(self, side_mm, table_mm):
        """Manual fallback: object side and table long side in mm ->
        tension_contact = (side / sqrt3) / table (pure ratio, camera-free)."""
        try:
            side_mm, table_mm = float(side_mm), float(table_mm)
        except (TypeError, ValueError):
            return {"ok": False, "error": "숫자를 입력하세요"}
        if side_mm <= 0 or table_mm <= 0:
            return {"ok": False, "error": "오브제 한 변과 테이블 긴 변(mm)을 모두 입력하세요"}
        if side_mm >= table_mm:
            return {"ok": False, "error": "오브제 한 변이 테이블 긴 변보다 큽니다"}
        from tracker.tension import TRI_MIN_CENTER_FACTOR
        contact = round(side_mm * TRI_MIN_CENTER_FACTOR / table_mm, 4)
        with self.lock:
            self.rt["object_side_mm"] = side_mm
            self.rt["table_long_mm"] = table_mm
            self.rt["tension_contact"] = contact
            self.rt["object_side_px"] = 0.0
            self.rt["tension_contact_src"] = "physical"
        return {"ok": True, "tension_contact": contact}

    # -- tracker control (Flask threads only set flags) --------------------

    @staticmethod
    def _is_tracker_key(k):
        return k.startswith(("rtv_", "rv_")) or k in ("tracker", "ritualvision_exe")

    def rtv_apply(self, updates):
        self.update({k: v for k, v in (updates or {}).items() if self._is_tracker_key(k)})
        with self.lock:
            self._mark_restart()
        return self.rtv_state()

    def rtv_start(self):
        if self.test_mode:
            return self.rtv_state()
        with self.lock:
            self.rt["rtv_autostart"] = True
            self._mark_restart()
        return self.rtv_state()

    def rtv_stop(self):
        with self.lock:
            self.rt["rtv_autostart"] = False
            self._rtv_dirty = False
            self._restart = {"phase": "", "t": 0.0, "rx": None, "base": 0, "reason": ""}
        self.rtv.stop()
        return self.rtv_state()

    def rtv_import(self):
        rt = self.get_rt()
        eng = get_engine(rt)
        exe = eng.find_exe(rt, self.rtv.exe_override)
        if not exe:
            return {"ok": False, "error": eng.missing_exe_message()}
        d = os.path.dirname(exe)
        data = eng.import_configs(d)
        if not data:
            return {"ok": False, "error": f"{d} 에 {eng.label} 설정 파일 없음"}
        self.update(data)
        return {"ok": True, "imported": data, "dir": d}

    RESTART_TIMEOUT_S = 20.0   # one-file Ritualvision.exe unpacks before the camera opens

    def _restart_status(self):
        """{"phase": "" | "restarting" | "playing" | "failed", "label", "reason"}"""
        with self.lock:
            r = self._restart
            if r["phase"] == "restarting" and r["rx"] is not None:
                rx = r["rx"]
                if rx is self.tuio and rx.frame_no > r["base"]:
                    r["phase"] = "playing"
                elif time.perf_counter() - r["t"] > self.RESTART_TIMEOUT_S:
                    st = self.rtv.status()
                    last = next((ln for ln in reversed(st["log"]) if ln.strip()), "")
                    r["phase"] = "failed"
                    r["reason"] = st["error"] or (f"{self.RESTART_TIMEOUT_S:.0f}초 동안 TUIO 수신 없음"
                                                  + (f" — {last}" if last else ""))
            phase, reason = r["phase"], r["reason"]
        if phase == "playing" and not self.rtv.is_running():
            phase = ""
        label = {"restarting": "다시 시작하는 중", "playing": "재생중",
                 "failed": "재시작 실패"}.get(phase, "")
        return {"phase": phase, "label": label, "reason": reason}

    def rtv_modes(self, refresh=False):
        rt = self.get_rt()
        exe = find_exe(rt.get("rtv_exe", ""))
        if not exe:
            return {"cameras": [], "exe": None, "error": "reacTIVision.exe 없음", "running": False}
        if self.test_mode:              # never touch the camera (-l opens it)
            return {"cameras": [], "exe": exe, "error": None, "running": False,
                    "restarted": False, "test_mode": True, "t": time.time()}
        restarted = False
        if refresh and self.rtv.is_running():
            # `-l` lists no modes while the camera is held (by our own child
            # too): pause the child, enumerate, and let the pipeline restart it
            self.rtv.stop()
            time.sleep(0.5)
            restarted = True
        m = list_modes(exe, refresh=refresh)
        if restarted:
            with self.lock:
                self._rtv_dirty = True
        cams = []
        for c in m["cameras"]:
            cams.append({**c, "default": default_mode(c, int(rt.get("rtv_fps") or 120))})
        return {"cameras": cams, "exe": exe, "error": m["error"], "stale": m.get("stale", False),
                "running": self.rtv.is_running(), "restarted": restarted, "t": m["t"]}

    def rtv_state(self):
        st = self.rtv.status()
        t = self.tuio
        rt = self.get_rt()
        eng = get_engine(rt)
        with self.lock:
            objs = list(self.results.get("objects", []))
        rng = size_range(rt)
        st.update({
            "tuio_port": t.port if t else None,
            "tuio_fps": round(t.fps, 1) if t else 0.0,
            "tuio_error": t.error if t else None,
            "tuio_age": (round(t.age(), 2) if t and t.age() != float("inf") else None),
            "last_fseq": t.last_fseq if t else -1,
            "objects": objs,
            "selected": eng.name,                  # may differ from "engine" until restarted
            "selected_label": eng.label,
            "engines": engines_info(),
            "exe_found": bool(eng.find_exe(rt, self.rtv.exe_override)),
            "restart": self._restart_status(),
            "size_range": [round(rng[0], 1), round(rng[1], 1)] if rng else None,
            "test_mode": self.test_mode,
        })
        return st

    # -- camera borrow (ROI setup with the real image) ------------------------

    CAM_IDLE_S = 15.0          # no UI poll for this long -> camera back to reacTIVision

    def camera_borrow(self, on):
        if self.test_mode:
            return {"ok": False, "error": "테스트 모드에서는 카메라를 쓰지 않습니다"}
        with self.lock:
            self._cam_want = bool(on)
            self._cam_seen_t = time.perf_counter()
            if on:
                self._cam_error = None
        return {"ok": True, **self.camera_state()}

    def camera_touch(self):
        """The UI is still open (called from /api/state polling)."""
        self._cam_seen_t = time.perf_counter()

    def camera_state(self):
        f = self._cam_frame
        return {"borrowed": self._cam_want, "open": self._cam is not None,
                "error": self._cam_error,
                "size": [int(f.shape[1]), int(f.shape[0])] if f is not None else None}

    @staticmethod
    def _crop_like_rtv(frame, rt):
        """Apply camera.xml <frame width height xoff yoff> so the image covers
        the same area reacTIVision normalizes TUIO x/y against."""
        def num(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
        h, w = frame.shape[:2]
        fw, fh = num(rt.get("rtv_frame_width")), num(rt.get("rtv_frame_height"))
        if fw is None and fh is None:
            return frame
        x0 = max(0, min(w - 1, num(rt.get("rtv_xoff")) or 0))
        y0 = max(0, min(h - 1, num(rt.get("rtv_yoff")) or 0))
        x1 = min(w, x0 + fw) if fw else w
        y1 = min(h, y0 + fh) if fh else h
        return frame[y0:y1, x0:x1]

    def _open_camera(self, rt):
        idx = int(rt.get("rtv_camera") or 0)
        api = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(idx, api)
        if not cap.isOpened():
            cap.release()
            return None
        if rt.get("rtv_compress", True):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        w, h = int(rt.get("rtv_width") or 0), int(rt.get("rtv_height") or 0)
        if w and h:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _release_camera(self):
        if self._cam is not None:
            try:
                self._cam.release()
            except Exception:
                pass
        self._cam = None
        self._cam_frame = None

    def _ensure_camera(self, rt):
        """Hand the camera between reacTIVision and the web preview."""
        if self._cam_want and time.perf_counter() - self._cam_seen_t > self.CAM_IDLE_S:
            self._cam_want = False             # UI gone: never leave reacTIVision off
        if self._cam_want and self._cam is None:
            self.rtv.stop()
            time.sleep(0.5)                    # let DirectShow release the device
            cap = None
            for _ in range(3):
                cap = self._open_camera(rt)
                if cap is not None:
                    break
                time.sleep(0.5)
            if cap is None:
                with self.lock:
                    self._cam_want = False
                    self._cam_error = f"카메라 {rt.get('rtv_camera', 0)} 열기 실패"
                    self._rtv_dirty = True
                return
            self._cam = cap
        elif not self._cam_want and self._cam is not None:
            self._release_camera()
            with self.lock:
                self._rtv_dirty = True         # restarts only if rtv_autostart
        if self._cam is not None:
            ok, frame = self._cam.read()
            if ok and frame is not None:
                self._cam_frame = self._crop_like_rtv(frame, rt)

    # -- main loop ---------------------------------------------------------

    TUIO_RETRY_S = 2.0         # re-bind interval after a failed bind (port busy)

    def _ensure_test(self, rt):
        """Test mode: a VirtualSource at the camera fps instead of TUIO; the
        reacTIVision child is never started."""
        self._rtv_dirty = False
        if self._tuio_dirty or self.tuio is None:
            if self.tuio is not None:
                self.tuio.stop()
            self.tuio = VirtualSource(self.virtual, fps=float(rt.get("rtv_fps") or 120))
            self.tuio.on_frame = self._on_tuio_frame
            self.tuio.start()
            with self.lock:
                self._tuio_dirty = False

    def _ensure(self, rt):
        if self.test_mode:
            return self._ensure_test(rt)
        port = int(rt["rtv_tuio_port"])
        now = time.perf_counter()
        # a failed bind (e.g. the previous server instance still held the
        # port for a moment) is retried, otherwise TUIO would stay dead
        retry = (self.tuio is not None and self.tuio.error
                 and now - getattr(self, "_tuio_try_t", 0.0) >= self.TUIO_RETRY_S)
        if self._tuio_dirty or self.tuio is None or self.tuio.port != port or retry:
            if self.tuio is not None:
                self.tuio.stop()
            self._tuio_try_t = now
            self.tuio = TuioReceiver(port=port,
                                     angle_offset=float(rt.get("rtv_angle_offset", 0.0)))
            self.tuio.on_frame = self._on_tuio_frame   # OSC goes out on the receiver thread
            self.tuio.start()
            with self.lock:
                self._tuio_dirty = False
        if self._rtv_dirty and not self._cam_want and self._cam is None:
            with self.lock:
                self._rtv_dirty = False
                want = bool(rt.get("rtv_autostart", True))
            if want:
                st = self.rtv.restart(rt) if self.rtv.is_running() else self.rtv.start(rt)
                with self.lock:            # "playing" once the new process sends a frame
                    rx = self.tuio
                    self._restart = {"phase": "restarting", "t": time.perf_counter(), "rx": rx,
                                     "base": rx.frame_no if rx is not None else 0, "reason": ""}
                    if not st.get("running"):
                        self._restart.update(phase="failed", reason=st.get("error") or "실행 실패")
            else:
                self.rtv.stop()
                with self.lock:
                    self._restart = {"phase": "", "t": 0.0, "rx": None, "base": 0, "reason": ""}

    def _stop_exhibit(self):
        self._release_camera()
        self.rtv.stop()
        if self.tuio is not None:
            self.tuio.stop()
            self.tuio = None

    def _canvas_size(self, rt):
        fmt = self.rtv.format
        if fmt:
            w, h = int(fmt[0]), int(fmt[1])
        elif int(rt.get("rtv_width") or 0) and int(rt.get("rtv_height") or 0):
            w, h = int(rt["rtv_width"]), int(rt["rtv_height"])
        else:
            w, h = 1280, 800
        maxd = int(rt.get("proc_max_dim") or 1280)
        if max(w, h) > maxd:
            s = maxd / max(w, h)
            w, h = int(w * s), int(h * s)
        return max(w, 16), max(h, 16)

    # -- per TUIO frame (runs on the receiver thread) -------------------------

    def _on_tuio_frame(self, objs, fseq):
        """Called by TuioReceiver right after each frame is committed. Does
        only what the OSC output needs (canvas coords -> ROI -> tension ->
        send), so OSC keeps up with reacTIVision's frame rate regardless of
        what the preview/UI thread is doing."""
        with self._frame_lock:
            self._process_frame(objs)

    # -- send clock: keeps OSC at the camera fps while reacTIVision is idle ----

    #: reacTIVision sends TUIO only when something changes (~1 bundle/s with
    #: an empty table). Between real frames the clock repeats the last
    #: committed TUIO state at the camera fps, so heartbeat/count keep the
    #: same rate and the 5-frame expiry (count 1 -> 0) takes 5 camera frames.
    CLOCK_ALIVE_S = 3.0        # no TUIO for this long = reacTIVision gone: clock stops

    def _clock_period(self, rt):
        fmt = self.rtv.format
        fps = float(fmt[2]) if fmt and len(fmt) > 2 and fmt[2] else float(rt.get("rtv_fps") or 120)
        return 1.0 / max(1.0, min(240.0, fps))

    def _clock_tick(self, period):
        """Process one frame from the last TUIO state if no frame (real or
        clock) happened within `period`. Returns True when a frame was sent."""
        t = self.tuio
        if not isinstance(t, TuioReceiver) or t.age() > self.CLOCK_ALIVE_S:
            return False
        with self._frame_lock:
            if time.perf_counter() - self._last_frame_t < period * 0.95:
                return False
            self._process_frame(t.snapshot(), real=False)
        return True

    def _clock_loop(self):
        while self.running:
            period = self._clock_period(self._rt_view())
            try:
                self._clock_tick(period)
            except Exception as exc:
                if self.tuio is not None:
                    self.tuio.on_frame_error = f"clock: {exc}"
            wait = self._last_frame_t + period - time.perf_counter()
            time.sleep(min(max(wait, 0.001), period))

    def _process_frame(self, objs, real=True):
        """`real`: a new TUIO frame (False = the send clock repeating the
        last state; idfix then reuses its last output instead of counting
        the same reading twice)."""
        self._last_frame_t = time.perf_counter()
        rt = self._rt_view()
        W, H = self._canvas_size(rt)
        self._size = (W, H)
        for o in objs:
            o.cx, o.cy = o.nx * W, o.ny * H
        roi_px = roi_to_px(rt.get("roi"), W, H)
        objs = self._idfix_frame(rt, objs, roi_px, W, H, real)
        objs = self._motion_frame(rt, objs, roi_px, W, H, real)
        log = self._send_osc(rt, objs, (W, H), roi_px, send=self.infer_on)
        self._last_objs = list(self._ritual.objects.values())
        self._last_roi_px = roi_px
        if log is not None:            # None = monitor rows skipped this frame
            self._last_osc_log = log

    # -- marker-id correction (optional) ----------------------------------------

    def _idfix_build(self):
        steps = None
        if not str(self._rt_view().get("rtv_tree") or "").strip():   # prior is for default.trees
            exe = find_exe(self._rt_view().get("rtv_exe") or "")
            for d in (INSTALL_DIR, os.path.dirname(exe) if exe else None):
                path = default_trees_path(d) if d else ""
                if path and os.path.isfile(path):
                    try:
                        steps = tree_steps(parse_trees(path))
                    except Exception:
                        steps = None
                    break
        return IdFixer(IdModel(steps, IDFIX_COUNTS_PATH))

    def _idfix_frame(self, rt, objs, roi_px, W, H, real):
        """idfix on: ROI filter -> IdFixer (real frames only) -> corrected
        objects with unique ids 0..24. Off: objects pass through untouched."""
        if not rt.get("idfix_enabled"):
            if getattr(self, "_idfix", None) is not None:
                self._idfix.reset()
                self._idfix_out = []
            return objs
        if getattr(self, "_idfix", None) is None:
            self._idfix = self._idfix_build()
            self._idfix_out = []
        if not real:
            return [replace(o) for o in self._idfix_out]
        if roi_px:
            x1, y1, x2, y2 = roi_px
            objs = [o for o in objs if x1 <= o.cx < x2 and y1 <= o.cy < y2]
            long_px = max(x2 - x1, y2 - y1, 1)
        else:
            long_px = max(W, H)
        side_px = float(rt.get("object_side_px", 0.0) or 0.0)
        d_near = tension_thresholds(rt, side_px, long_px)[0]
        fx = self._idfix
        fx.configure(gate_px=float(rt.get("idfix_gate") or 0.5) * d_near * long_px,
                     lost_s=rt.get("idfix_lost_s", 0.5),
                     first_delay_s=rt.get("idfix_first_delay_s", 0.1),
                     switch_frames=rt.get("idfix_switch_frames", 15),
                     learn=rt.get("idfix_learn", False),
                     memory_s=rt.get("idfix_memory_s", 10.0),
                     table_px=long_px)
        self._idfix_out = fx.update(objs, time.perf_counter())
        return [replace(o) for o in self._idfix_out]

    def _motion_frame(self, rt, objs, roi_px, W, H, real):
        """Speed of every object; with the lift cut on, fast movers are
        withheld. The send clock (real=False) repeats the last result."""
        if getattr(self, "_motion", None) is None:
            self._motion = MotionGate()
            self._motion_out = []
        if not real:
            return [replace(o) for o in self._motion_out]
        long_px = (max(roi_px[2] - roi_px[0], roi_px[3] - roi_px[1], 1)
                   if roi_px else max(W, H))
        try:
            speed = max(0.01, float(rt.get("lift_cut_speed") or 0.3))
            hold = min(0.5, max(0.0, float(rt.get("lift_cut_hold_s") or 0.0)))
        except (TypeError, ValueError):
            speed, hold = 0.3, 0.1
        self._motion_out = self._motion.update(
            objs, time.perf_counter(), long_px, bool(rt.get("lift_cut_enabled")),
            cut_speed=speed, hold_s=hold)
        return [replace(o) for o in self._motion_out]

    def idfix_reset(self, learned=False):
        fx = getattr(self, "_idfix", None)
        if fx is None:
            fx = self._idfix = self._idfix_build()
            self._idfix_out = []
        with self._frame_lock:
            fx.reset()
            if learned:
                fx.model.reset()
                fx.model.save_if_dirty()
        return {"ok": True}

    def idfix_pin(self, track, true_id):
        """Calibration: name the true id of an idfix track (-1 releases)."""
        fx = getattr(self, "_idfix", None)
        if fx is None:
            return {"ok": False, "error": "보정이 꺼져 있거나 아직 프레임이 없습니다"}
        try:
            track, true_id = int(track), int(true_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "track / id 가 숫자가 아닙니다"}
        with self._frame_lock:
            ok = fx.pin(track, true_id)
        return {"ok": ok} if ok else {"ok": False, "error": f"트랙 {track} 없음"}

    def idfix_state(self):
        if not self._rt_view().get("idfix_enabled"):
            return None
        fx = getattr(self, "_idfix", None)
        if fx is None:              # on, but no TUIO frame yet
            return {"tracks": [], "frames": 0, "corrected": 0, "switches": 0,
                    "events": [], "learned": []}
        return fx.stats()

    # -- preview / UI loop (pipeline thread, OVERLAY_HZ) -----------------------

    def run(self):
        if not self.test_mode:      # test mode: VirtualSource already runs at fps
            threading.Thread(target=self._clock_loop, daemon=True, name="osc-clock").start()
        while self.running:
            rt = self._rt_view()
            try:
                self._ensure(rt)
                if not self.test_mode:
                    self._ensure_camera(rt)
                time.sleep(max(0.0, 1.0 / self.OVERLAY_HZ - (time.perf_counter() - self._overlay_last)))
                W, H = self._canvas_size(rt)
                if not self._size[0]:
                    self._size = (W, H)
                objs = list(self._last_objs)
                stale = (self.tuio is None or time.perf_counter() - self._last_frame_t
                         > float(rt.get("rtv_stale_s") or 1.0))
                if stale:                      # TUIO stopped: show (and report) nothing
                    objs = []
                    self._graph = None
                    self._last_node_t = {}
                osc_log = [] if stale else list(self._last_osc_log)
                roi_px = roi_to_px(rt.get("roi"), W, H)
                self._overlay_last = time.perf_counter()
                st = self.rtv.status()
                st["error"] = (st["error"] or (self.tuio.error if self.tuio else None)
                               or (self.tuio.on_frame_error if self.tuio else None))
                vis = draw_exhibit_overlay(W, H, objs, self._graph, st,
                                           self.tuio.fps if self.tuio else 0.0,
                                           roi_px, rt, self.infer_on,
                                           test_mode=self.test_mode, hex_r=self._hex_r,
                                           camera=self._cam_frame if self._cam is not None else None)
                if self.virtual is not None:
                    self.virtual.save_if_dirty()
                fx = getattr(self, "_idfix", None)
                if fx is not None and time.perf_counter() - getattr(self, "_idfix_saved_t", 0.0) > 10.0:
                    self._idfix_saved_t = time.perf_counter()
                    try:
                        fx.model.save_if_dirty()
                    except Exception:
                        pass
                q = [cv2.IMWRITE_JPEG_QUALITY, int(rt.get("jpeg_quality") or 80)]
                if vis.shape[1] > self.PREVIEW_MAX_W:   # cheap JPEG; OSC is not on this thread
                    s = self.PREVIEW_MAX_W / vis.shape[1]
                    vis = cv2.resize(vis, (self.PREVIEW_MAX_W, int(vis.shape[0] * s)),
                                     interpolation=cv2.INTER_AREA)
                jo = cv2.imencode(".jpg", vis, q)[1].tobytes()
                idfix = self.idfix_state()       # takes self.lock: not inside it
                with self.lock:
                    self.frames["overlay"] = jo
                    self.frame_no += 1
                    self.results = {
                        "objects": [object_json(o, self._last_node_t.get(o.track_id, 0.0),
                                                osc_xy(o.cx, o.cy, roi_px, W, H))
                                    for o in objs],
                        "tuio_fps": round(self.tuio.fps, 1) if self.tuio else 0.0,
                        "size": [W, H],
                        "infer": self.infer_on,
                        "osc": osc_log,
                        "osc_fps": 0.0 if stale else round(self.osc_fps, 1),
                        "osc_msgs": len(osc_log),
                        "idfix": idfix,
                        "lift_cut": getattr(getattr(self, "_motion", None), "cut_count", 0),
                        "error": st.get("error"),
                    }
            except Exception as exc:
                with self.lock:
                    self.results["error"] = f"pipeline error: {exc}"
                time.sleep(0.5)

    # -- OSC -----------------------------------------------------------------

    def _send_osc(self, rt, objs, frame_size, roi_px=None, send=True):
        """Stable MST/max + SOUND object messages and one TD frame bundle.
        Enabled targets receive data when OSC is on and `send` is set. Returns monitor
        rows [(address, text, target)]."""
        osc_on = bool(rt.get("osc_enabled"))
        show_graph = bool(rt.get("show_graph", True))
        if not osc_on:
            self._osc_clients = {}
            self.osc_fps = 0.0
            self._osc_last_t = None
        w, h = frame_size
        if roi_px:
            ox, oy, x2, y2 = roi_px
            rw, rh = max(x2 - ox, 1), max(y2 - oy, 1)
        else:
            ox, oy, rw, rh = 0, 0, w, h
        if not hasattr(self, "_ritual"):
            self._ritual = RitualState()
        known = self._ritual.update(objs, roi_px)
        side_px = float(rt.get("object_side_px", 0.0) or 0.0)
        long_px = max(rw, rh)
        d_near, d_far, self.tension_basis = tension_thresholds(rt, side_px, long_px)
        # test-mode hexagon: apothem = d_near/2, so two plates touch at tension 1
        self._hex_r = (d_near * long_px / 2.0) * 2.0 / math.sqrt(3.0)
        edges, node_t = self._ritual.graph(
            long_px, d_near, d_far, float(rt.get("tension_margin", 0.0) or 0.0))
        self._graph = ([(float(o.cx), float(o.cy)) for o in known], edges, node_t) \
            if show_graph else None
        self._last_node_t = {o.track_id: t for o, t in zip(known, node_t)}
        if not osc_on or not send:
            return []

        targets = [t for t in osc_targets(rt) if t["enabled"]]
        if not targets:
            return [["osc", "대상 없음", ""]]
        keys = {(t["host"], t["port"]) for t in targets}
        for k in list(self._osc_clients):
            if k not in keys:
                del self._osc_clients[k]
        try:
            from pythonosc.udp_client import SimpleUDPClient
            for k in keys:
                if k not in self._osc_clients:
                    self._osc_clients[k] = SimpleUDPClient(*k)
        except Exception as exc:
            return [["osc", f"client error: {exc}", ""]]
        prefix = str(rt.get("osc_prefix", "/scramble")).rstrip("/")
        # the monitor text (one row per message) is only needed at the UI
        # rate; building it on every frame would cost more than the sends
        # with 25 objects x 2 targets at 120 fps
        now0 = time.perf_counter()
        want_log = (now0 - getattr(self, "_log_last_t", 0.0)) >= 1.0 / self.OVERLAY_HZ
        if want_log:
            self._log_last_t = now0
        log = [] if want_log else None

        from pythonosc.osc_bundle_builder import OscBundleBuilder, IMMEDIATELY
        from pythonosc.osc_message_builder import OscMessageBuilder
        rows = []
        for o, t in zip(known, node_t):
            rows.append([int(o.code_id), id_bits(o.code_id),
                  *osc_xy(o.cx, o.cy, roi_px, w, h),
                  round(float(o.angle), 2), t, self._ritual.pairs[o.code_id],
                  self._ritual.heartbeat])
        # id -> normalized (x, y), for the TD pairXn / pairYn fields
        pair_xy = {r[0]: (r[2], r[3]) for r in rows}
        for target in targets:
            client = self._osc_clients[(target["host"], target["port"])]
            try:
                if target["format"] == "td":
                    # one heartbeat per bundle (network-flow indicator), sent
                    # even with no objects; per-object fields use slots 0..N-1
                    # count = objects on the table now (= slots 0..count-1 below)
                    bundle = OscBundleBuilder(IMMEDIATELY)
                    for name, value in (("heartbeat", self._ritual.heartbeat),
                                        ("count", len(rows))):
                        addr = f"{prefix}/{name}"
                        message = OscMessageBuilder(address=addr)
                        message.add_arg(value)
                        bundle.add_content(message.build())
                        if want_log:
                            log.append([addr, osc_args_text("list", [value]), target["name"], "td"])
                    for index, values in enumerate(rows):
                        fields = []
                        for field, value in zip(OSC_OBJ_FIELDS, values):
                            if field == "heartbeat":
                                continue
                            if field == "pairId":
                                # TD only: the pair's position as pairXn / pairYn
                                px, py = pair_xy.get(value, (-1.0, -1.0))
                                fields += [(f"pairID{index}", value),
                                           (f"pairX{index}", px), (f"pairY{index}", py)]
                            else:
                                fields.append((f"{field}{index}", value))
                        for name, value in fields:
                            addr = f"{prefix}/{name}"
                            message = OscMessageBuilder(address=addr)
                            message.add_arg(value)
                            bundle.add_content(message.build())
                            if want_log:
                                log.append([addr, osc_args_text("list", [value]), target["name"], "td"])
                    client.send(bundle.build())
                else:
                    for values in rows:
                        args = format_osc_args(target["format"], OSC_OBJ_FIELDS, values)
                        client.send_message(f"{prefix}/obj", args)
                        if want_log:
                            log.append([f"{prefix}/obj", osc_args_text(target["format"], args),
                                        target["name"], target["format"]])
            except Exception as exc:
                if want_log:
                    log.append([prefix, f"send error: {exc}", target["name"], target["format"]])

        now = time.perf_counter()
        if self._osc_last_t is not None:
            dt = now - self._osc_last_t
            if dt > 0:
                self.osc_fps += 0.15 * (1.0 / dt - self.osc_fps)
        self._osc_last_t = now
        return log

    # -- test mode: virtual objects -------------------------------------------

    def object_action(self, body):
        """/api/object: add | remove | clear | move | update (test mode only)."""
        if self.virtual is None:
            return {"ok": False, "error": "테스트 모드가 아닙니다 (test_start.bat)"}
        act = str(body.get("action") or "")
        roi = self._rt_view().get("roi")
        idx = int(body.get("index", -1)) if "index" in body else -1
        if act == "add":
            ok, err = self.virtual.add(roi)
        elif act == "remove":
            ok, err = self.virtual.remove(idx)
        elif act == "clear":
            ok, err = self.virtual.clear()
        elif act == "move":
            ok, err = self.virtual.move(idx, body.get("x", 0.5), body.get("y", 0.5), roi)
        elif act == "update":
            ok, err = self.virtual.update(idx, body.get("code_id"), body.get("tilt"))
        else:
            ok, err = False, f"알 수 없는 action: {act}"
        return {"ok": ok, "error": err, **self.virtual_state()}

    def virtual_state(self):
        if self.virtual is None:
            return {}
        return {"objects": self.virtual.list(), "limit": VIRTUAL_LIMIT,
                "hex_r": round(self._hex_r, 2), "canvas": list(self._size)}

    # -- misc ----------------------------------------------------------------

    def jpeg(self):
        with self.lock:
            return self.frames.get("overlay"), self.frame_no

    def shutdown(self):
        self.running = False
        self._stop_exhibit()
        fx = getattr(self, "_idfix", None)
        if fx is not None:
            try:
                fx.model.save_if_dirty()
            except Exception:
                pass


# ---------------------------------------------------------------- flask

def make_app(pipe):
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "exhibit.html")

    @app.get("/stream/overlay")
    def stream():
        def gen():
            seen = -1
            while True:
                buf, n = pipe.jpeg()
                if buf is None or n == seen:
                    time.sleep(0.02)
                    continue
                seen = n
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(buf)).encode()
                       + b"\r\n\r\n" + buf + b"\r\n")
        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/state")
    def state():
        with pipe.lock:
            results = dict(pipe.results)
        pipe.camera_touch()
        return jsonify({"camera": pipe.camera_state(),
                        "runtime": pipe.get_rt(), "results": results,
                        "rtv": pipe.rtv_state(), "osc_fields": OSC_OBJ_FIELDS,
                        "tension_basis": pipe.tension_basis,
                        "active_name": pipe.active_name,
                        "test_mode": pipe.test_mode,
                        "virtual": pipe.virtual_state()})

    @app.post("/api/idfix/reset")
    def idfix_reset():
        b = request.get_json(force=True) or {}
        return jsonify(pipe.idfix_reset(learned=bool(b.get("learned"))))

    @app.post("/api/idfix/pin")
    def idfix_pin():
        b = request.get_json(force=True) or {}
        return jsonify(pipe.idfix_pin(b.get("track"), b.get("id", -1)))

    @app.post("/api/object")
    def object_api():
        return jsonify(pipe.object_action(request.get_json(force=True) or {}))

    @app.get("/api/configs")
    def configs():
        return jsonify({"configs": list_config_names(), "active": pipe.active_name})

    @app.post("/api/config")
    def config():
        applied = pipe.update(request.get_json(force=True) or {})
        return jsonify({"ok": True, "applied": applied})

    @app.post("/api/config/reset")
    def config_reset():
        pipe.reset_cfg()
        return jsonify({"ok": True})

    @app.post("/api/config/save")
    def config_save():
        name = pipe.save_cfg((request.get_json(force=True) or {}).get("name"))
        return jsonify({"ok": True, "name": name, "configs": list_config_names()})

    @app.post("/api/config/load")
    def config_load():
        name = (request.get_json(force=True) or {}).get("name")
        ok = pipe.load_cfg(name)
        return jsonify({"ok": ok, "name": pipe.active_name})

    @app.post("/api/roi")
    def roi():
        body = request.get_json(force=True) or {}
        return jsonify({"ok": True, "roi": pipe.set_roi(body.get("roi"))})

    @app.post("/api/camera/borrow")
    def camera_borrow():
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(pipe.camera_borrow(bool(body.get("on"))))

    @app.post("/api/tension/measure_contact")
    def tension_measure_contact():
        return jsonify(pipe.measure_contact())

    @app.post("/api/tension/physical")
    def tension_physical():
        b = request.get_json(force=True) or {}
        return jsonify(pipe.contact_from_physical(b.get("object_side_mm"), b.get("table_long_mm")))

    @app.post("/api/calibrate")
    def calibrate():
        b = request.get_json(force=True) or {}
        return jsonify(pipe.calibrate_side(b.get("x1"), b.get("y1"), b.get("x2"), b.get("y2")))

    @app.post("/api/pipeline")
    def pipeline_ctl():
        body = request.get_json(force=True) or {}
        if "infer" in body:
            pipe.infer_on = bool(body["infer"])
        return jsonify({"ok": True, "infer": pipe.infer_on})

    @app.get("/api/rtv/modes")
    def rtv_modes():
        return jsonify(pipe.rtv_modes(refresh=request.args.get("refresh") in ("1", "true")))

    @app.post("/api/rtv/apply")
    def rtv_apply():
        return jsonify(pipe.rtv_apply(request.get_json(force=True) or {}))

    @app.post("/api/rtv/start")
    def rtv_start():
        return jsonify(pipe.rtv_start())

    @app.post("/api/rtv/stop")
    def rtv_stop():
        return jsonify(pipe.rtv_stop())

    @app.post("/api/rtv/import")
    def rtv_import():
        return jsonify(pipe.rtv_import())

    @app.get("/api/rtv/log")
    def rtv_log():
        return jsonify({"log": pipe.rtv.status()["log"]})

    return app


def main():
    ap = argparse.ArgumentParser(description="Scramble Ritual exhibition server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--rtv-exe", default="", help="reacTIVision.exe 경로 (기본: 자동 탐색)")
    ap.add_argument("--no-rtv", action="store_true",
                    help="reacTIVision을 자동 실행하지 않음 (수동 실행 시)")
    ap.add_argument("--test", action="store_true",
                    help="테스트: reacTIVision/TUIO 없이 가상 오브제(6각형, ID 0~24)로 동일 파이프라인 실행")
    ap.add_argument("--tracker", choices=TRACKERS,
                    help="마커 트래커 선택 (저장되어 다음 실행의 기본값이 됨; 생략 시 마지막 선택)")
    args = ap.parse_args()

    pipe = ExhibitPipeline(autostart=(False if args.no_rtv else None),
                           exe_override=args.rtv_exe, test_mode=args.test, tracker=args.tracker)
    pipe.start()
    app = make_app(pipe)
    engine, exe = tracker_exe(pipe.get_rt(), args.rtv_exe)
    if args.test:
        print("[exhibit] TEST MODE - reacTIVision/TUIO off, virtual objects "
              f"({TEST_OBJECTS_PATH})")
    else:
        print(f"[exhibit] tracker: {engine} -> {exe or 'NOT FOUND'}"
              f"{'' if args.tracker else '  (last used; change with --tracker)'}")
    print(f"[exhibit] http://localhost:{args.port}")
    try:
        app.run(host=args.host, port=args.port, threaded=True, debug=False,
                use_reloader=False)
    finally:
        pipe.shutdown()


if __name__ == "__main__":
    main()
