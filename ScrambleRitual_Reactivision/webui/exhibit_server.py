"""
Scramble Ritual — exhibition server (reacTIVision edition).

    USB camera -> reacTIVision.exe (child process; camera.xml/reacTIVision.xml
                  are generated into its own folder, ~/scramble_ritual/reactivision/)
               -> TUIO /tuio/2Dobj on UDP 127.0.0.1:3333
               -> this pipeline: ROI normalize -> tension graph -> OSC
               -> /scramble/obj to every OSC target (SuperCollider, TouchDesigner)

reacTIVision is the only recognition method: no OpenCV detection, no
simulation, no video/image sources. The web UI exposes what the exhibition
needs — reacTIVision settings, the table region, object size, tension knobs
(MST graph by default), OSC targets, profiles and a monitor.

Run:  python -m webui.exhibit_server   (scripts/exhibition_start.bat)
"""

import argparse
import json
import math
import os
import re
import sys
import threading
import time

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_from_directory

from tracker.reactivision import (
    RTV_DEFAULTS, ExhibitObject, ReactivisionProcess, TuioReceiver,
    default_mode, find_exe, import_xml, list_modes, original_xml_paths,
)
from tracker.tension import (
    draw_tension_graph, roi_to_px, tension_color, tension_graph,
    tension_thresholds,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "exhibit_config.json")
CONFIGS_DIR = os.path.join(HERE, "..", "configs", "exhibit")
STATIC_DIR = os.path.join(HERE, "static")

# OSC payload field order (list format = these positions; dict/json = keys).
# `id` is the reacTIVision fiducial symbol id — the object's identity.
OSC_OBJ_FIELDS = ["id", "x", "y", "tilt", "tension", "flip", "freq"]

# Default payload format is JSON (one string argument: {"id": .., "x": .., ...});
# per target it can be switched to list (positional) or dict (k, v, k, v ...).
DEFAULT_OSC_FORMAT = "json"
DEFAULT_OSC_TARGETS = [
    {"name": "SuperCollider", "host": "127.0.0.1", "port": 57120,
     "format": DEFAULT_OSC_FORMAT, "enabled": True},
    {"name": "TouchDesigner", "host": "127.0.0.1", "port": 7000,
     "format": DEFAULT_OSC_FORMAT, "enabled": True},
]

EXHIBIT_DEFAULTS = {
    **RTV_DEFAULTS,
    # OSC
    "osc_enabled": False,
    "osc_prefix": "/scramble",
    "osc_targets": [dict(t) for t in DEFAULT_OSC_TARGETS],
    # table region (normalized [x, y, w, h] of the camera frame) and object size
    "roi": None,
    "object_side_px": 0.0,       # measured triangle side (canvas px); 0 = fallback
    "tension_contact": 0.06,     # fallback d_near when the size is unknown
    # tension graph knobs
    "tension_connect": "mst",    # mst (최소 신장 트리, 기본) | all | near | knn
    "tension_fold": "max",       # max | avg | min
    "tension_knn": 3,
    "tension_link_radius": 0.2,
    "show_graph": True,
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
    """Monitor text showing the payload as it is actually structured."""
    if fmt == "dict":
        return ", ".join(f"{args[i]}: {args[i + 1]}" for i in range(0, len(args) - 1, 2))
    if fmt == "json":
        return str(args[0]) if args else ""
    return ", ".join(str(a) for a in args)


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
        if fmt not in ("list", "dict", "json"):
            fmt = DEFAULT_OSC_FORMAT
        out.append({"name": str(t.get("name") or f"target{i + 1}"), "host": host,
                    "port": port, "format": fmt, "enabled": bool(t.get("enabled", True))})
    return out


def object_freq(code_id):
    """Per-object semitone band derived from the id, so distinct ids are
    audibly separable (-6..+5)."""
    return float(((int(code_id) * 5) % 12) - 6)


def object_json(o, tension=0.0):
    return {"track": o.track_id, "id": o.code_id, "label": o.label,
            "x": round(o.nx, 4), "y": round(o.ny, 4),
            "cx": round(o.cx, 1), "cy": round(o.cy, 1),
            "angle": round(o.angle, 1), "tension": tension}


def draw_exhibit_overlay(W, H, objs, graph, rtv_status, tuio_fps, roi_px, rt,
                         infer_on=True):
    """Synthetic preview: reacTIVision owns the camera, so we draw what TUIO
    tells us on a blank canvas at the capture aspect."""
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
    for o in objs:
        cx, cy = int(o.cx), int(o.cy)
        col = (255, 200, 0)
        cv2.circle(vis, (cx, cy), radius, col, 2, cv2.LINE_AA)
        a = math.radians(o.angle)
        ex, ey = int(cx + math.cos(a) * radius * 1.5), int(cy + math.sin(a) * radius * 1.5)
        cv2.line(vis, (cx, cy), (ex, ey), col, 2, cv2.LINE_AA)
        cv2.putText(vis, f"#{o.code_id}  s{o.track_id}  {o.angle:.0f}",
                    (cx - 30, cy + radius + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    col, 1, cv2.LINE_AA)
    if graph and graph[0]:
        draw_tension_graph(vis, graph)
    st = rtv_status or {}
    run = "RUN" if st.get("running") else "STOP"
    hud = (f"reacTIVision {run} pid={st.get('pid') or '-'} {st.get('format') or '-'} "
           f"tuio={tuio_fps:.0f}fps objs={len(objs)}")
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
    PREVIEW_MAX_W = 800        # JPEG is encoded from a downscaled copy (cheap)

    def __init__(self, autostart=None, exe_override=""):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.rt = self._initial_config(exe_override)
        if autostart is not None:
            self.rt["rtv_autostart"] = bool(autostart)
        self.rtv = ReactivisionProcess(exe=exe_override)
        self.tuio = None
        self._rtv_dirty = bool(self.rt.get("rtv_autostart", True))
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
        self.active_name = ""
        self.running = True

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

    def update(self, updates):
        """Apply runtime updates (only known keys). reacTIVision keys mark the
        child for restart; the restart itself happens on the pipeline thread."""
        applied = {}
        with self.lock:
            for k, v in (updates or {}).items():
                if k not in EXHIBIT_DEFAULTS:
                    continue
                if k == "osc_targets":
                    v = osc_targets({"osc_targets": v})
                self.rt[k] = v
                applied[k] = v
            if any(k.startswith("rtv_") for k in applied):
                self._rtv_dirty = True
                self._tuio_dirty = True
        return applied

    def reset_cfg(self):
        with self.lock:
            self.rt = json.loads(json.dumps(EXHIBIT_DEFAULTS))
            self._rtv_dirty = self._tuio_dirty = True

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
            self._rtv_dirty = self._tuio_dirty = True
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

    # -- reacTIVision control (Flask threads only set flags) ---------------

    def rtv_apply(self, updates):
        self.update({k: v for k, v in (updates or {}).items() if k.startswith("rtv_")})
        with self.lock:
            self._rtv_dirty = True
            self._tuio_dirty = True
        return self.rtv_state()

    def rtv_start(self):
        with self.lock:
            self.rt["rtv_autostart"] = True
            self._rtv_dirty = True
        return self.rtv_state()

    def rtv_stop(self):
        with self.lock:
            self.rt["rtv_autostart"] = False
            self._rtv_dirty = False
        self.rtv.stop()
        return self.rtv_state()

    def rtv_import(self):
        exe = find_exe(self.get_rt().get("rtv_exe", ""))
        if not exe:
            return {"ok": False, "error": "reacTIVision.exe 없음"}
        d = os.path.dirname(exe)
        data = import_xml(*original_xml_paths(d))
        if not data:
            return {"ok": False, "error": f"{d} 에 camera.xml/reacTIVision.xml 없음"}
        self.update(data)
        return {"ok": True, "imported": data, "dir": d}

    def rtv_modes(self, refresh=False):
        rt = self.get_rt()
        exe = find_exe(rt.get("rtv_exe", ""))
        if not exe:
            return {"cameras": [], "exe": None, "error": "reacTIVision.exe 없음", "running": False}
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
        with self.lock:
            objs = list(self.results.get("objects", []))
        st.update({
            "tuio_port": t.port if t else None,
            "tuio_fps": round(t.fps, 1) if t else 0.0,
            "tuio_error": t.error if t else None,
            "tuio_age": (round(t.age(), 2) if t and t.age() != float("inf") else None),
            "last_fseq": t.last_fseq if t else -1,
            "objects": objs,
            "exe_found": bool(find_exe(self.get_rt().get("rtv_exe", ""))),
        })
        return st

    # -- main loop ---------------------------------------------------------

    TUIO_RETRY_S = 2.0         # re-bind interval after a failed bind (port busy)

    def _ensure(self, rt):
        port = int(rt["rtv_tuio_port"])
        now = time.monotonic()
        # a failed bind (e.g. the previous server instance still held the
        # port for a moment) is retried, otherwise TUIO would stay dead
        retry = (self.tuio is not None and self.tuio.error
                 and now - getattr(self, "_tuio_try_t", 0.0) >= self.TUIO_RETRY_S)
        if self._tuio_dirty or self.tuio is None or self.tuio.port != port or retry:
            if self.tuio is not None:
                self.tuio.stop()
            self._tuio_try_t = now
            self.tuio = TuioReceiver(port=port,
                                     angle_offset=float(rt.get("rtv_angle_offset", 0.0))).start()
            with self.lock:
                self._tuio_dirty = False
        if self._rtv_dirty:
            with self.lock:
                self._rtv_dirty = False
                want = bool(rt.get("rtv_autostart", True))
            if want:
                self.rtv.restart(rt) if self.rtv.is_running() else self.rtv.start(rt)
            else:
                self.rtv.stop()

    def _stop_exhibit(self):
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

    def run(self):
        while self.running:
            rt = self._rt_view()
            try:
                self._ensure(rt)
                got = self.tuio.wait_frame(0.1) if (self.tuio and not self.tuio.error) else False
                if not got and (self.tuio is None or self.tuio.error):
                    time.sleep(0.1)
                W, H = self._canvas_size(rt)
                self._size = (W, H)
                objs = self.tuio.snapshot() if self.tuio else []
                if self.tuio and self.tuio.age() > float(rt.get("rtv_stale_s") or 1.0):
                    objs = []
                for o in objs:
                    o.cx, o.cy = o.nx * W, o.ny * H
                roi_px = roi_to_px(rt.get("roi"), W, H)
                osc_log = self._send_osc(rt, objs, (W, H), roi_px, send=got and self.infer_on)
                now = time.monotonic()
                if now - self._overlay_last >= 1.0 / self.OVERLAY_HZ:
                    self._overlay_last = now
                    st = self.rtv.status()
                    st["error"] = st["error"] or (self.tuio.error if self.tuio else None)
                    vis = draw_exhibit_overlay(W, H, objs, self._graph, st,
                                               self.tuio.fps if self.tuio else 0.0,
                                               roi_px, rt, self.infer_on)
                    q = [cv2.IMWRITE_JPEG_QUALITY, int(rt.get("jpeg_quality") or 80)]
                    if vis.shape[1] > self.PREVIEW_MAX_W:   # keep the loop fast
                        s = self.PREVIEW_MAX_W / vis.shape[1]
                        vis = cv2.resize(vis, (self.PREVIEW_MAX_W, int(vis.shape[0] * s)),
                                         interpolation=cv2.INTER_AREA)
                    jo = cv2.imencode(".jpg", vis, q)[1].tobytes()
                    with self.lock:
                        self.frames["overlay"] = jo
                        self.frame_no += 1
                        self.results = {
                            "objects": [object_json(o, self._last_node_t.get(o.track_id, 0.0))
                                        for o in objs],
                            "tuio_fps": round(self.tuio.fps, 1) if self.tuio else 0.0,
                            "size": [W, H],
                            "infer": self.infer_on,
                            "osc": osc_log,
                            "osc_fps": round(self.osc_fps, 1),
                            "osc_msgs": len(osc_log),
                            "error": st.get("error"),
                        }
            except Exception as exc:
                with self.lock:
                    self.results["error"] = f"pipeline error: {exc}"
                time.sleep(0.5)

    # -- OSC -----------------------------------------------------------------

    def _send_osc(self, rt, objs, frame_size, roi_px=None, send=True):
        """Tension graph (always, for the overlay) + per-object OSC to every
        enabled target when OSC is on and `send` is set. Returns the monitor
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
        known = sorted(objs, key=lambda o: (o.cx, o.cy))
        side_px = float(rt.get("object_side_px", 0.0) or 0.0)
        long_px = max(rw, rh)
        d_near, d_far, self.tension_basis = tension_thresholds(rt, side_px, long_px)
        edges, node_t = tension_graph(
            known, long_px, d_near, d_far,
            str(rt.get("tension_connect", "mst")), str(rt.get("tension_fold", "max")),
            int(rt.get("tension_knn", 3) or 3), float(rt.get("tension_link_radius", 0.2) or 0.2))
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
        log = []

        def norm(v, origin, span):   # clamp so receivers always get 0..1
            return round(max(0.0, min(1.0, (v - origin) / span)), 4)

        def emit(addr, values):
            for t in targets:
                args = format_osc_args(t["format"], OSC_OBJ_FIELDS, values)
                try:
                    self._osc_clients[(t["host"], t["port"])].send_message(addr, args)
                    log.append([addr, osc_args_text(t["format"], args), t["name"]])
                except Exception as exc:
                    log.append([addr, f"send error: {exc}", t["name"]])

        for o, t in zip(known, node_t):
            emit(f"{prefix}/obj",
                 [int(o.code_id), norm(o.cx, ox, rw), norm(o.cy, oy, rh),
                  round(float(o.angle), 2), t, 1 if o.flip else 0,
                  round(object_freq(o.code_id), 2)])

        now = time.monotonic()
        if self._osc_last_t is not None:
            dt = now - self._osc_last_t
            if dt > 0:
                self.osc_fps += 0.15 * (1.0 / dt - self.osc_fps)
        self._osc_last_t = now
        return log

    # -- misc ----------------------------------------------------------------

    def jpeg(self):
        with self.lock:
            return self.frames.get("overlay"), self.frame_no

    def shutdown(self):
        self.running = False
        self._stop_exhibit()


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
        return jsonify({"runtime": pipe.get_rt(), "results": results,
                        "rtv": pipe.rtv_state(), "osc_fields": OSC_OBJ_FIELDS,
                        "tension_basis": pipe.tension_basis,
                        "active_name": pipe.active_name})

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
    args = ap.parse_args()

    pipe = ExhibitPipeline(autostart=(False if args.no_rtv else None),
                           exe_override=args.rtv_exe)
    pipe.start()
    app = make_app(pipe)
    exe = find_exe(args.rtv_exe or pipe.get_rt().get("rtv_exe", ""))
    print(f"[exhibit] reacTIVision: {exe or 'NOT FOUND'}")
    print(f"[exhibit] http://localhost:{args.port}")
    try:
        app.run(host=args.host, port=args.port, threaded=True, debug=False,
                use_reloader=False)
    finally:
        pipe.shutdown()


if __name__ == "__main__":
    main()
