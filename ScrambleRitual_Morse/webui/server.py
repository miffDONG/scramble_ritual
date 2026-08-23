"""
Live-tuning web UI for the Morse card tracker.

    python -m webui.server                # http://localhost:8765
    python -m webui.server --camera 0     # start on a camera
    python -m webui.server --video ../sampleVideo/morseCode.MOV

One worker thread owns the capture source and runs detection with the
current config; Flask serves MJPEG streams of the raw / mask / overlay
views and a JSON API the browser uses to change options in real time.
"""

import argparse
import json
import math
import os
import re
import sys
import threading
import time

# tracker.camera must be imported before cv2: it sets the OpenCV videoio env
# switch that only takes effect while the library loads. See tracker/camera.py.
from tracker.camera import open_camera, probe_cameras

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request, send_from_directory

from tracker.morse import (
    DEFAULTS, Glyph, MorseCard, SlotRead, cfg_with_defaults, detect_cards,
    extraction_maps, find_glyphs, generate_codebook, prepare_gray, preprocess, render_card,
    render_scene, shape_features,
)
from webui.diagnostics import (
    DIAG_CAND_BGR, DIAG_DOT_BGR, DIAG_LINE_BGR, VIEWS, caption,
    compare_wipe, draw_extraction, extraction_summary, glyph_is_line,
    mask_over_frame, preprocess_chain,
)
from tracker.stability import CardStabilizer

# object-sim canvas + object layout (shared by render and ground-truth cards).
# Matches the real plates, whose morse chain sits INSIDE the body, horizontally,
# near the base: the 26.07.20 upright triangle and the hexagon concept plate.
# The table is sized to hold a full set of 20 objects without their chains
# running into each other, and kept wide (~2.5:1) so it fills the monitor
# column instead of sitting letterboxed inside it.
SIM_CANVAS = (1920, 780)
SIM_GRID = (7, 3)        # auto-placement cells (21 >= the 20-ID codebook)
SIM_STEP = 22            # chain symbol spacing; must exceed dash_len enough that
                         # consecutive dashes don't merge under blur
SIM_TRI_W = 260          # triangle base width
SIM_TRI_H = 225          # triangle height (~equilateral)
# Pointy-top regular hexagon plate (sampleVideo/morse-concepts). Circumradius
# 126 -> 218 x 252 px: the flat left/right sides are 218 wide, which is what
# the chain (176 px of symbols + the anchor marker) has to fit between.
SIM_HEX_R = 126
SIM_BODIES = ("triangle", "hexagon")
SIM_CHAIN_DY = 50        # chain offset below the centroid (inside, near base)
# The plate outline must stay BRIGHT: a dark outline survives thresholding and,
# because findContours is RETR_EXTERNAL, it would enclose (and hide) the marks.
SIM_BODY_COLOR = (205, 205, 255)   # light pink — visible, never detected
from webui.schema import AXES, RUNTIME_DEFAULTS, SCHEMA

CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "tracker", "config.json")
CONFIGS_DIR = os.path.join(os.path.dirname(__file__), "..", "configs")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
MEDIA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "sampleVideo")

VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

#: consecutive failed camera reads (~20 ms apart) before the source is
#: declared dead and the UI shows an error instead of a frozen preview
CAM_FAIL_LIMIT = 90

# OSC payload field order. In "list" format these are the positional slots;
# in "dict" format they are the keys. Shown in the web UI so the receiver
# never has to guess the order.
# Per-object tension is folded from this object's distances to the other n-1
# objects (see object_tension); pairs are no longer sent.
OSC_OBJ_FIELDS = ["bits", "x", "y", "tilt", "tension", "flip", "freq"]


def safe_config_name(name):
    """A filesystem-safe profile name (drops any path parts, keeps word
    chars / spaces / dash / Korean)."""
    name = os.path.basename(str(name).strip())
    if name.lower().endswith(".json"):
        name = name[:-5]
    name = re.sub(r"[^\w \-가-힣]", "_", name).strip()
    return name[:64] or "config"


def list_config_names():
    """Saved profile names (configs/*.json), sorted."""
    if not os.path.isdir(CONFIGS_DIR):
        return []
    names = [f[:-5] for f in os.listdir(CONFIGS_DIR) if f.endswith(".json")]
    return sorted(names, key=str.lower)


def roi_to_px(roi, w, h):
    """Normalized [x, y, w, h] (0..1) -> clamped integer (x1, y1, x2, y2),
    or None if unset / degenerate."""
    if not roi or len(roi) != 4:
        return None
    x, y, rw, rh = roi
    if rw <= 0 or rh <= 0:
        return None
    x1 = max(0, min(w - 1, int(round(x * w))))
    y1 = max(0, min(h - 1, int(round(y * h))))
    x2 = max(x1 + 1, min(w, int(round((x + rw) * w))))
    y2 = max(y1 + 1, min(h, int(round((y + rh) * h))))
    return (x1, y1, x2, y2)


def list_cameras():
    """Connected video devices as [{index, name}]. Windows: pygrabber reads
    DirectShow names without opening devices (safe while one is in use);
    elsewhere fall back to an OpenCV probe."""
    if sys.platform == "win32":
        try:
            from pygrabber.dshow_graph import FilterGraph
            names = FilterGraph().get_input_devices()
            return [{"index": i, "name": n or f"Camera {i}"}
                    for i, n in enumerate(names)]
        except Exception:
            pass
    return probe_cameras()


def list_media(kind, base_dir=MEDIA_DIR):
    """Files under the media dir for the UI dropdown (browsers cannot hand
    over absolute paths; the server runs on the same PC)."""
    exts = VIDEO_EXTS if kind == "video" else IMAGE_EXTS
    root = os.path.abspath(base_dir)
    items = []
    if os.path.isdir(root):
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(exts):
                    p = os.path.join(dirpath, f)
                    items.append({"path": p,
                                  "name": os.path.relpath(p, root).replace("\\", "/")})
    items.sort(key=lambda it: it["path"].lower())
    return items


def card_pairs(cards, frame_size):
    """Pairwise normalized distance + relative tilt (same OSC contract as
    tracker.morse_run)."""
    w, h = frame_size
    diag = math.hypot(w, h)
    ordered = sorted(cards, key=lambda c: (c.cx, c.cy))
    pairs = []
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            a, b = ordered[i], ordered[j]
            dist = math.hypot(b.cx - a.cx, b.cy - a.cy) / max(diag, 1e-6)
            rel = (b.angle - a.angle + 180.0) % 360.0 - 180.0
            pairs.append((i, j, dist, rel))
    return pairs


#: two equilateral plates touch edge-to-edge (one rotated 180°) at exactly
#: side / sqrt(3) between centres — the closest they can be without overlapping
TRI_MIN_CENTER_FACTOR = 1.0 / math.sqrt(3.0)


def tension_thresholds(rt, side_px, long_px):
    """Distance thresholds for the tension curve, both normalized by the TABLE
    (ROI) LONG side so tension 0..1 means the same physical closeness on any
    source or zoom, and so `d_far` is exactly half the long side.

        d_far  = tension reaches 0    = half the table's long side  -> 0.5
        d_near = tension reaches 1    = edge-touching centre distance
                                        (side/sqrt3), object-size derived

    Returns (d_near, d_far, source) where source describes what set d_near.
    Distance passed to pair_tension must be normalized the same way:
        dist_norm = centre_distance_px / long_px
    """
    d_far = 0.5                             # half the long side, by definition
    if side_px and side_px > 0 and long_px > 0:
        return (side_px * TRI_MIN_CENTER_FACTOR) / long_px, d_far, \
            f"side={side_px:.0f}px"
    # fallback when the object size is unknown (no calibration / not objects)
    return float(rt.get("tension_contact", 0.06)), d_far, "fixed"


def pair_tension(dist, d_near, d_far):
    """Monotonic tension between two plates as they approach — no drop on
    overlap (that '→0' replacement was removed).

        멀어짐        dist >= d_far           -> 0
        가까워지는 중  d_far .. d_near         -> rise 0 -> 1 (linear)
        변이 닿음·겹침 dist <= d_near          -> 1 (stays at max)

    `dist`, `d_near`, `d_far` are all normalized by the table long side.
    """
    if dist >= d_far:
        return 0.0
    if dist <= d_near:
        return 1.0
    return round((d_far - dist) / max(d_far - d_near, 1e-6), 4)


def _fold(values, rule):
    """Combine a list of per-edge tensions into one scalar. Empty -> 0."""
    if not values:
        return 0.0
    if rule == "min":
        return min(values)
    if rule == "avg":
        return sum(values) / len(values)
    return max(values)                     # "max" (default)


def object_tension(i, dists, d_near, d_far, connect="all", fold="max", knn=3,
                   link_radius=None):
    """Tension on object i, folded from its distances to the other n-1 objects.

    `dists` is the list of normalized centre distances from i to every other
    object (already excluding i itself).

        connect: which neighbours are LINKED (graph topology)
            all  — every other object (n-1)
            near — only those within `link_radius` (a tunable connection
                   radius, independent of the tension curve's d_far)
            knn  — the k nearest objects
        fold: how the per-edge tensions combine — min / avg / max

    `link_radius` defaults to d_far when unset (legacy behaviour). Set it
    SMALLER than d_far so 'near' actually prunes distant links — otherwise on
    a normal table every pair sits inside d_far and near == all.
    """
    if not dists:
        return 0.0
    edges = sorted(dists)
    if connect == "near":
        r = d_far if link_radius is None else link_radius
        edges = [d for d in edges if d < r]
    elif connect == "knn":
        edges = edges[:max(1, int(knn))]
    tensions = [pair_tension(d, d_near, d_far) for d in edges]
    return round(_fold(tensions, fold), 4)


def tension_graph(nodes, long_px, d_near, d_far, connect="all", fold="max",
                  knn=3, link_radius=None):
    """The tension graph the algorithm actually builds — the single source of
    truth for both the OSC per-object value and the overlay visualization.

    `nodes` carry .cx/.cy (frame pixels). Distances are normalized by the
    table long side. Returns (edges, node_tensions):
        edges          — [(i, j, edge_tension)] undirected UNION of every
                         node's considered neighbours (so knn's asymmetric
                         links still show as a connection)
        node_tensions  — [float] per node, identical to object_tension()
    The connect / knn / link_radius filtering mirrors object_tension() exactly.
    `link_radius` (near only) defaults to d_far when unset.
    """
    n = len(nodes)
    if n == 0:
        return [], []
    long_px = max(long_px, 1e-6)
    r_near = d_far if link_radius is None else link_radius
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = math.hypot(nodes[i].cx - nodes[j].cx,
                           nodes[i].cy - nodes[j].cy) / long_px
            dist[i][j] = dist[j][i] = d
    considered = []
    for i in range(n):
        order = sorted((j for j in range(n) if j != i), key=lambda j: dist[i][j])
        if connect == "near":
            order = [j for j in order if dist[i][j] < r_near]
        elif connect == "knn":
            order = order[:max(1, int(knn))]
        considered.append(order)
    node_t = []
    for i in range(n):
        ts = [pair_tension(dist[i][j], d_near, d_far) for j in considered[i]]
        node_t.append(round(_fold(ts, fold), 4) if ts else 0.0)
    edges, seen = [], set()
    for i in range(n):
        for j in considered[i]:
            key = (i, j) if i < j else (j, i)
            if key in seen:
                continue
            seen.add(key)
            edges.append((key[0], key[1],
                          pair_tension(dist[key[0]][key[1]], d_near, d_far)))
    return edges, node_t


def tension_color(t):
    """BGR ramp for a tension value: light gray (0) -> hot orange-red (1)."""
    t = max(0.0, min(1.0, t))
    return (int(200 * (1 - t)), int(200 - 110 * t), int(200 + 55 * t))


def _rot(lx, ly, cos_r, sin_r, cx, cy):
    """Rotate a local (lx, ly) by the object's tilt and place it at (cx, cy)."""
    return (cx + lx * cos_r - ly * sin_r, cy + lx * sin_r + ly * cos_r)


def sim_chain_center(cx, cy, angle_deg, flip=False):
    """Return the Morse-chain centre after the plate's physical transform.

    The front face has the chain below the centroid.  A physical top/bottom
    flip mirrors local Y before the in-plane tilt is applied, moving the chain
    above the centroid without reversing its left-to-right symbol order.
    """
    r = math.radians(angle_deg)
    chain_y = -SIM_CHAIN_DY if flip else SIM_CHAIN_DY
    return _rot(0.0, chain_y, math.cos(r), math.sin(r), cx, cy)


def _sim_chain_geometry(cx, cy, angle_deg, n_slots, step):
    """Anchor + per-symbol image coords matching render_card's layout so the
    overlay line lands exactly on the drawn chain. (cx, cy) = chain centre."""
    origin_x = (n_slots + 1) * step / 2.0
    r = math.radians(angle_deg)
    cos_r, sin_r = math.cos(r), math.sin(r)
    to_img = lambda x: (cx + (x - origin_x) * cos_r, cy + (x - origin_x) * sin_r)
    return to_img(0.0), [to_img((i + 1) * step) for i in range(n_slots)]


def _fake_glyph(cx, cy, shape=""):
    g = Glyph(kind="start" if shape else "dot", cx=cx, cy=cy, area=100.0,
              bbox=(int(cx) - 5, int(cy) - 5, 10, 10),
              contour=np.zeros((1, 1, 2), np.int32))
    g.shape = shape
    return g


def _obj_bits(code_id, codebook, n_slots):
    return ((codebook.get(code_id) if codebook else None)
            or format((code_id - 1) % (1 << n_slots), f"0{n_slots}b"))


def objects_to_cards(objs, w, h, codebook, n_slots):
    """Ground-truth MorseCards from sim objects — bypasses detection."""
    cards = []
    for o in objs:
        cid = int(o.get("code_id", 1))
        cx, cy = o["x"] * w, o["y"] * h
        # wrap to -180..180 so ground truth matches what detection (atan2)
        # would report — auto-rotation otherwise grows the angle without bound
        angle = (float(o.get("tilt", 0.0)) + 180.0) % 360.0 - 180.0
        bits = _obj_bits(cid, codebook, n_slots)
        # card centre stays the OBJECT (triangle) centre for distance; the
        # chain geometry hangs off the offset chain centre inside the plate
        flipped = bool(o.get("flip", False))
        ccx, ccy = sim_chain_center(cx, cy, angle, flipped)
        anchor, syms = _sim_chain_geometry(ccx, ccy, angle, len(bits), SIM_STEP)
        slots = [SlotRead(i, b, sx, sy, 1.0, _fake_glyph(sx, sy))
                 for i, (b, (sx, sy)) in enumerate(zip(bits, syms))]
        start = _fake_glyph(anchor[0], anchor[1], o.get("shape", "triangle"))
        end = _fake_glyph(*syms[-1]) if syms else start
        pitch = o.get("pitch")
        cards.append(MorseCard(
            cx=cx, cy=cy, angle=angle, bits=bits, code_id=cid, raw_id=cid,
            status="sim", score=1.0, start=start, end=end, slots=slots,
            flip=flipped,
            pitch=None if pitch is None else float(pitch)))
    cards.sort(key=lambda c: (c.cx, c.cy))
    return cards


def _attach_truth(cards, truth):
    """Copy sim-only attributes (flip / pitch) from the nearest ground-truth
    object onto each detected card — detection cannot know these."""
    for c in cards:
        best, bd = None, float("inf")
        for t in truth:
            d = (c.cx - t.cx) ** 2 + (c.cy - t.cy) ** 2
            if d < bd:
                bd, best = d, t
        if best is not None:
            c.flip, c.pitch = best.flip, best.pitch


def _body_outline(body):
    """Plate outline + mounting-hole positions in plate-local coords (origin =
    centroid, +y down), for each physical object form."""
    if body == "hexagon":
        # pointy-top regular hexagon: vertex at top and bottom, flat sides
        # left and right — the form the morse cells run along in the concept
        # sheet (sampleVideo/morse-concepts/hex-code-edge-layouts.png)
        r = float(SIM_HEX_R)
        outline = [(r * math.cos(math.radians(-90 + i * 60)),
                    r * math.sin(math.radians(-90 + i * 60))) for i in range(6)]
        # holes near the top and the two lower vertices, pulled inside; they
        # are the only asymmetric feature, so a flip stays visible on a shape
        # that is otherwise its own mirror image
        holes = [(0.0, -r + 22), (-r * 0.75, r * 0.5 - 14), (r * 0.75, r * 0.5 - 14)]
        return outline, holes
    hw, apex_y, base_y = SIM_TRI_W / 2.0, -SIM_TRI_H * 2 / 3.0, SIM_TRI_H / 3.0
    return ([(0.0, apex_y), (-hw, base_y), (hw, base_y)],
            [(0.0, apex_y + 20), (-hw + 22, base_y - 12), (hw - 22, base_y - 12)])


def plate_reach():
    """Worst-case (half-width, half-height) a plate reaches from its centroid,
    over every body form. Vertically symmetric because a face flip mirrors the
    outline, so a flipped plate must not fall off the table either."""
    hw = hh = 0.0
    for form in SIM_BODIES:
        for x, y in _body_outline(form)[0]:
            hw, hh = max(hw, abs(x)), max(hh, abs(y))
    return hw, hh


def table_slots():
    """Normalized centres of the table's auto-placement grid, row by row.
    Inset by the plate reach so an auto-placed object sits fully on the table
    instead of hanging off the edge."""
    cols, rows = SIM_GRID
    w, h = SIM_CANVAS
    hw, hh = plate_reach()
    span_x, span_y = max(w - 2 * hw, 1.0), max(h - 2 * hh, 1.0)
    return [((hw + span_x * (q / (cols - 1) if cols > 1 else 0.5)) / w,
             (hh + span_y * (r / (rows - 1) if rows > 1 else 0.5)) / h)
            for r in range(rows) for q in range(cols)]


def free_table_slot(objs, tol=0.02):
    """First grid cell no object is sitting on, else the table centre."""
    taken = [(float(o.get("x", 0.5)), float(o.get("y", 0.5))) for o in objs]
    for sx, sy in table_slots():
        if all(abs(sx - x) > tol or abs(sy - y) > tol for x, y in taken):
            return sx, sy
    return 0.5, 0.5


def draw_object_body(frame, cx, cy, angle_deg, color, flip=False,
                     body="triangle"):
    """Plate outline centred on its centroid, with its mounting holes — like
    the real acrylic object. `body` picks the physical form: the upright
    triangle (apex up, base down) or the pointy-top regular hexagon."""
    r = math.radians(angle_deg)
    cos_r, sin_r = math.cos(r), math.sin(r)
    outline, holes = _body_outline(body)
    # A face flip is a reflection in the plate's local horizontal axis.  Do it
    # before tilt so flip=ON/0deg differs geometrically from flip=OFF/180deg.
    fy = -1.0 if flip else 1.0
    pts = [[int(v) for v in _rot(lx, ly * fy, cos_r, sin_r, cx, cy)]
           for lx, ly in outline]
    cv2.polylines(frame, [np.array(pts, np.int32)], True, color, 2, cv2.LINE_AA)
    for lx, ly in holes:
        hx, hy = _rot(lx, ly * fy, cos_r, sin_r, cx, cy)
        cv2.circle(frame, (int(hx), int(hy)), 5, color, 1, cv2.LINE_AA)


def render_objects(objs, codebook, n_slots, bg=245, color=0):
    """Draw the object-sim scene: triangle body + morse chain per object."""
    w, h = SIM_CANVAS
    frame = np.full((h, w, 3), bg, np.uint8)
    col = (color, color, color) if isinstance(color, (int, float)) else color
    for o in objs:
        cx, cy, angle = o["x"] * w, o["y"] * h, float(o.get("tilt", 0.0))
        bits = _obj_bits(int(o.get("code_id", 1)), codebook, n_slots)
        flipped = bool(o.get("flip", False))
        draw_object_body(frame, cx, cy, angle, SIM_BODY_COLOR, flipped,
                         o.get("body", "triangle"))
        # chain drawn INSIDE the plate, near the base — sized to the smaller step
        ccx, ccy = sim_chain_center(cx, cy, angle, flipped)
        render_card(frame, bits, (ccx, ccy), angle, step=SIM_STEP, color=col,
                    dot_r=5, dash_len=11, dash_thick=5, marker=15,
                    marker_shape=o.get("shape", "square"), draw_start=True)
    return frame


def flatten_cfg(morse_cfg):
    """morse cfg -> UI dict (threshold_clamp list becomes _lo/_hi)."""
    out = {k: v for k, v in morse_cfg.items() if k != "threshold_clamp"}
    lo, hi = morse_cfg.get("threshold_clamp", DEFAULTS["threshold_clamp"])
    out["threshold_clamp_lo"] = lo
    out["threshold_clamp_hi"] = hi
    return out


def unflatten_cfg(ui_cfg):
    """UI dict -> morse cfg (rebuild threshold_clamp list)."""
    out = {k: v for k, v in ui_cfg.items()
           if k not in ("threshold_clamp_lo", "threshold_clamp_hi")}
    if "threshold_clamp_lo" in ui_cfg or "threshold_clamp_hi" in ui_cfg:
        base = ui_cfg.get("_clamp_base", DEFAULTS["threshold_clamp"])
        out["threshold_clamp"] = [
            ui_cfg.get("threshold_clamp_lo", base[0]),
            ui_cfg.get("threshold_clamp_hi", base[1]),
        ]
    out.pop("_clamp_base", None)
    return out


class Pipeline(threading.Thread):
    """Owns the capture source; every frame runs detect -> stabilize ->
    overlay with whatever config the web UI last posted."""

    def __init__(self, source=("sim", None)):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.cfg = cfg_with_defaults(self._load_saved().get("morse"))
        self.runtime = dict(RUNTIME_DEFAULTS)
        self.runtime.update(self._load_saved().get("webui", {}))
        self.source_req = source
        self.source_desc = "-"
        self.stab = CardStabilizer()
        self.frames = {view: None for view in VIEWS}
        self.frame_no = 0
        self.results = {"cards": [], "stable": [], "glyphs": 0, "fps": 0.0,
                        "size": [0, 0], "error": None}
        self._cap = None
        self._cur_source = None
        self._codebook = None
        self._codebook_key = None
        self.running = True
        # transport / pipeline control (sensing-style)
        self.infer_on = True
        self.paused = False
        self._step = 0
        self._seek = None
        self._last_frame = None
        self.video_pos = 0
        self.video_total = 0
        self._osc_client = None
        self._osc_target = None
        self.osc_fps = 0.0          # measured OSC send-cycle rate
        self._osc_last_t = None
        self.tension_basis = ""     # what the tension thresholds came from
        self._graph = None          # (node_xy, edges, node_tensions) overlay
        self._last_full = None
        self._cam_dirty = True
        self.cam_status = ""
        self._cam_backend = "-"
        self._cam_fails = 0
        self.source_error = None   # sticky open/read failure, shown in the UI
        self.active_name = ""

    # ---- config ------------------------------------------------------

    def _load_saved(self, path=CFG_PATH):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _config_path(self, name):
        return os.path.join(CONFIGS_DIR, safe_config_name(name) + ".json")

    def get_cfg(self):
        with self.lock:
            return flatten_cfg(self.cfg), dict(self.runtime)

    def update_cfg(self, updates):
        morse_updates, runtime_updates = {}, {}
        runtime_keys = set(RUNTIME_DEFAULTS)
        for k, v in updates.items():
            (runtime_updates if k in runtime_keys else morse_updates)[k] = v
        with self.lock:
            if morse_updates:
                morse_updates["_clamp_base"] = list(self.cfg["threshold_clamp"])
                self.cfg.update(unflatten_cfg(morse_updates))
            self.runtime.update(runtime_updates)
            self._sync_stabilizer()

    def reset_cfg(self):
        with self.lock:
            self.cfg = cfg_with_defaults(None)
            self.runtime = dict(RUNTIME_DEFAULTS)
            self._sync_stabilizer()

    def _write_cfg(self, path, keep_extra_from=None):
        data = self._load_saved(keep_extra_from) if keep_extra_from else {}
        with self.lock:
            data["morse"] = {k: self.cfg[k] for k in sorted(self.cfg)}
            data["webui"] = dict(self.runtime)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    def save_cfg(self, name=None):
        """Save current options. With a name, write a profile under
        configs/<name>.json; also mirror to tracker/config.json so the CLI
        and the next server start pick up the latest saved options."""
        if name:
            name = safe_config_name(name)
            self._write_cfg(self._config_path(name))
            self.active_name = name
        # keep the active config.json in sync (preserves vision/scene/sound)
        self._write_cfg(CFG_PATH, keep_extra_from=CFG_PATH)
        return name or ""

    def load_cfg(self, name=None):
        """Load options into the live session (no restart needed). With a
        name, load the configs/<name>.json profile and make it active;
        without a name, reload tracker/config.json."""
        if name:
            name = safe_config_name(name)
            data = self._load_saved(self._config_path(name))
            self.active_name = name
        else:
            data = self._load_saved(CFG_PATH)
        with self.lock:
            self.cfg = cfg_with_defaults(data.get("morse"))
            self.runtime = dict(RUNTIME_DEFAULTS)
            self.runtime.update(data.get("webui", {}))
            self._sync_stabilizer()
        if name:  # mirror the loaded profile to the active config.json
            self._write_cfg(CFG_PATH, keep_extra_from=CFG_PATH)
        return name or ""

    def seed_configs(self):
        """First run: expose the current active config as one named profile
        so the dropdown is not empty."""
        os.makedirs(os.path.abspath(CONFIGS_DIR), exist_ok=True)
        if not list_config_names():
            self._write_cfg(self._config_path("default"))
            self.active_name = "default"

    def capture_template(self, label, roi):
        """Extract the dominant blob inside `roi` on the last frame and save
        its descriptor vector as a template for `label`. Used to teach the
        classifier the actual marker shapes (square/triangle/star/...)."""
        frame = self._last_full
        if frame is None:
            return {"ok": False, "error": "아직 프레임이 없습니다"}
        if not label:
            return {"ok": False, "error": "라벨을 고르세요"}
        h, w = frame.shape[:2]
        px = roi_to_px(roi, w, h)
        if not px:
            return {"ok": False, "error": "영역을 드래그하세요"}
        x1, y1, x2, y2 = px
        crop = frame[y1:y2, x1:x2]
        with self.lock:
            c = dict(self.cfg)
        # find the blob with gates fully open so the marker is captured
        # regardless of the current filter/shape settings
        c.update({"shape_classify": False, "min_solidity": 0.0,
                  "min_rectangularity": 0.0, "min_blob_contrast": 0,
                  "max_glyph_area_frac": 1.0, "max_glyph_area_px": 0,
                  "min_glyph_area_px": 5, "min_glyph_area_frac": 0.0})
        gray = prepare_gray(crop, c)
        glyphs = find_glyphs(preprocess(crop, c, gray=gray), c, gray=gray)
        if not glyphs:
            return {"ok": False, "error": "영역에서 마커를 못 찾음"}
        g = max(glyphs, key=lambda z: z.area)
        with self.lock:
            tmpls = list(self.cfg.get("shape_templates") or [])
            tmpls.append({"label": label, "v": shape_features(g)})
            self.cfg["shape_templates"] = tmpls
            self.stab.reset()
        return {"ok": True, "label": label, "count": len(tmpls)}

    def clear_templates(self):
        with self.lock:
            self.cfg["shape_templates"] = []
            self.stab.reset()

    def template_labels(self):
        return [t.get("label", "?") for t in (self.cfg.get("shape_templates") or [])]

    def calibrate_side(self, x1, y1, x2, y2):
        """Measure the observed triangle side from a drag across one edge
        (normalized endpoints) and store it in processed-frame pixels."""
        frame = self._last_full
        if frame is None:
            return {"ok": False, "error": "아직 프레임이 없습니다"}
        h, w = frame.shape[:2]
        side = math.hypot((float(x2) - float(x1)) * w,
                          (float(y2) - float(y1)) * h)
        if side < 4:
            return {"ok": False, "error": "너무 짧습니다 — 한 변을 길게 그으세요"}
        with self.lock:
            self.runtime["object_side_px"] = round(side, 1)
        return {"ok": True, "object_side_px": round(side, 1)}

    def set_roi(self, roi):
        """Store the table ROI as normalized [x, y, w, h], or None to clear."""
        with self.lock:
            if not roi:
                self.runtime["roi"] = None
            else:
                x, y, rw, rh = (float(v) for v in roi)
                x = max(0.0, min(1.0, x))
                y = max(0.0, min(1.0, y))
                rw = max(0.0, min(1.0 - x, rw))
                rh = max(0.0, min(1.0 - y, rh))
                self.runtime["roi"] = ([x, y, rw, rh] if rw > 0 and rh > 0
                                       else None)
            self.stab.reset()

    # ---- simulated objects (ground-truth for sound/OSC dev) ----------

    def _objects(self):
        return self.runtime.setdefault("sim_objects", [])

    def _spun_objects(self, t):
        """Object list with the server-side auto-rotation applied, so rotation
        is smooth regardless of browser/polling latency."""
        with self.lock:
            objs = [dict(o) for o in self.runtime.get("sim_objects", [])]
            spin = float(self.runtime.get("sim_spin", 0.0))
        if spin:
            for i, o in enumerate(objs):
                # stagger per object so they don't rotate in lockstep
                o["tilt"] = float(o.get("tilt", 0.0)) + spin * t + i * 23.0
        return objs

    def object_op(self, action, req):
        """add / update / move / remove on the sim_objects list. Returns the
        updated list. Coords are normalized 0..1 (plate center)."""
        with self.lock:
            objs = self._objects()
            if action == "add":
                # no explicit spot -> drop it on the first free table cell.
                # Stacking every new object on the middle of the table buried
                # them in one blob that reads as a single (or no) card.
                px, py = free_table_slot(objs)
                x = max(0.0, min(1.0, float(req.get("x", px))))
                y = max(0.0, min(1.0, float(req.get("y", py))))
                objs.append({"code_id": int(req.get("code_id", len(objs) + 1)),
                             "x": x, "y": y, "tilt": 0.0, "flip": False,
                             "shape": "triangle",
                             "body": str(req.get("body", "triangle")),
                             "pitch": None})
            elif action == "clear":
                objs.clear()
            elif action in ("update", "move", "remove"):
                i = int(req.get("index", -1))
                if 0 <= i < len(objs):
                    if action == "remove":
                        objs.pop(i)
                    elif action == "move":
                        objs[i]["x"] = max(0.0, min(1.0, float(req["x"])))
                        objs[i]["y"] = max(0.0, min(1.0, float(req["y"])))
                    else:  # update
                        o = objs[i]
                        if "code_id" in req:
                            o["code_id"] = int(req["code_id"])
                        if "tilt" in req:
                            o["tilt"] = float(req["tilt"])
                        if "flip" in req:
                            o["flip"] = bool(req["flip"])
                        if "shape" in req:
                            o["shape"] = str(req["shape"])
                        if "body" in req:
                            o["body"] = str(req["body"])
                        if "pitch" in req:
                            p = req["pitch"]
                            o["pitch"] = None if p is None else float(p)
            return list(objs)

    def _sync_stabilizer(self):
        self.stab.configure({
            "window": int(self.runtime["stab_window"]),
            "min_votes": int(self.runtime["stab_min_votes"]),
            "max_missed": int(self.runtime["stab_max_missed"]),
        })

    def _get_codebook(self, c):
        if c["decode_mode"] != "codebook":
            return None
        key = (c["slots"], c["code_count"], c["min_hamming"])
        if key != self._codebook_key:
            try:
                self._codebook = generate_codebook(
                    c["code_count"], c["slots"], c["min_hamming"])
                self._codebook_key = key
            except ValueError:
                self._codebook = None
        return self._codebook

    # ---- camera exposure / anti-flicker ------------------------------

    def set_camera(self, updates):
        with self.lock:
            for k in ("cam_auto_exposure", "cam_exposure", "cam_powerline"):
                if k in updates:
                    self.runtime[k] = updates[k]
            self._cam_dirty = True

    def _apply_cam_props(self):
        """Push exposure / anti-flicker settings to the open camera. Property
        support is driver-dependent; we set what we can and report back."""
        cap = self._cap
        if cap is None or (self._cur_source or ("", ""))[0] != "camera":
            self._cam_dirty = False
            return
        rt = self.runtime
        msgs = []
        try:
            # 0.25 = manual, 0.75 = auto  (common UVC/DShow convention)
            auto = bool(rt.get("cam_auto_exposure", True))
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75 if auto else 0.25)
            msgs.append("auto" if auto else "manual")
            if not auto:
                cap.set(cv2.CAP_PROP_EXPOSURE, float(rt.get("cam_exposure", -6)))
                msgs.append(f"exp={rt.get('cam_exposure')}")
            pl = int(rt.get("cam_powerline", 0))
            if pl:
                # not all backends expose this; harmless if ignored
                prop = getattr(cv2, "CAP_PROP_POWERLINE_FREQUENCY", None)
                if prop is not None:
                    cap.set(prop, float(pl))
                    msgs.append(f"{'50' if pl == 1 else '60'}Hz")
        except Exception as exc:
            msgs.append(f"err:{exc}")
        self.cam_status = " ".join(msgs)
        self._cam_dirty = False

    # ---- source ------------------------------------------------------

    def set_source(self, kind, arg=None):
        with self.lock:
            self.source_error = None   # only the user picking a source clears it
            self.source_req = (kind, arg)

    def _open_source(self, kind, arg):
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._image = None
        if kind == "camera":
            idx = int(arg or 0)
            # open_camera proves the backend can actually deliver a frame
            # before we keep it — on Windows an "open" camera often cannot.
            cap, backend = open_camera(idx)
            self._cap = cap
            self._cam_backend = backend
            self._cam_fails = 0
            self.source_desc = f"camera:{idx}({backend})"
            self._cam_dirty = True   # apply exposure/anti-flicker on next tick
        elif kind == "video":
            cap = cv2.VideoCapture(arg)
            if not cap.isOpened():
                raise RuntimeError(f"비디오를 열 수 없습니다: {arg}")
            self._cap = cap
            self.video_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            self.video_pos = 0
            self.paused = False
            self.source_desc = f"video:{os.path.basename(str(arg))}"
        elif kind == "image":
            img = cv2.imread(arg)
            if img is None:
                raise RuntimeError(f"이미지를 열 수 없습니다: {arg}")
            self._image = img
            self.source_desc = f"image:{os.path.basename(str(arg))}"
        elif kind == "sim-field":
            self.source_desc = "sim-field"
        elif kind == "objects":
            self.source_desc = "objects"
        else:
            kind = "sim"
            self.source_desc = "sim"
        self._cur_source = (kind, arg)

    def transport(self, action=None, seek=None):
        """Video controls: pause / resume / toggle / step / restart / seek."""
        if action == "pause":
            self.paused = True
        elif action == "resume":
            self.paused = False
        elif action == "toggle":
            self.paused = not self.paused
        elif action == "step":
            self.paused = True
            self._step += 1
        elif action == "restart":
            self._seek = 0.0
        if seek is not None:
            self._seek = max(0.0, min(1.0, float(seek)))

    def _read(self, t, c):
        kind, arg = self._cur_source
        if kind == "video":
            if self._seek is not None:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES,
                              int(self._seek * max(self.video_total - 1, 0)))
                self._seek = None
            elif self.paused and self._step == 0:
                time.sleep(1.0 / 30.0)  # hold the frame, settings still apply
                return self._last_frame.copy() if self._last_frame is not None else None
            if self._step > 0:
                self._step -= 1
            ok, frame = self._cap.read()
            if not ok or frame is None:  # loop the clip
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self._cap.read()
                if not ok or frame is None:
                    return None
            self.video_pos = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES))
            self._last_frame = frame
            return frame
        if kind == "camera":
            if self._cam_dirty:
                self._apply_cam_props()
            ok, frame = self._cap.read()
            if ok and frame is not None:
                self._cam_fails = 0
                return frame
            # A dropped frame or two is normal (USB hiccup, mode change); a
            # camera that has stopped delivering entirely must surface as an
            # error instead of a silently frozen preview.
            self._cam_fails += 1
            if self._cam_fails >= CAM_FAIL_LIMIT:
                raise RuntimeError(
                    f"카메라({self._cam_backend})가 프레임을 주지 않습니다 — "
                    "다른 앱이 장치를 사용 중이거나 케이블/전원을 확인하세요")
            time.sleep(0.02)
            return None
        if kind == "image":
            time.sleep(1.0 / 15.0)  # settings still apply live
            return self._image.copy()
        if kind == "objects":
            objs = self._spun_objects(t)
            book = self._get_codebook(c)
            time.sleep(1.0 / 30.0)
            return render_objects(objs, book, int(c["slots"]))
        # synthetic scene, animated like tracker.morse_run --sim
        book = self._get_codebook(c) or {
            i + 1: format(i + 11, f"0{c['slots']}b") for i in range(4)}
        codes = list(book.values())
        k = 0.5 + 0.5 * math.sin(t * 0.6)
        cards = [
            (codes[0 % len(codes)], 220, 160, 8 + 18 * k),
            (codes[1 % len(codes)], 610, 170, -24 + 14 * k),
            (codes[2 % len(codes)], 260, 360, -12 - 20 * k),
            (codes[3 % len(codes)], 650, 360, 35 - 18 * k),
        ]
        time.sleep(1.0 / 30.0)
        if kind == "sim-field":
            return render_scene(cards, bg=25, color=235)
        return render_scene(cards)

    # ---- main loop ---------------------------------------------------

    def run(self):
        t0 = time.monotonic()
        fps = 0.0
        last = t0
        while self.running:
            with self.lock:
                c = dict(self.cfg)
                rt = dict(self.runtime)
                req = self.source_req
            try:
                if req != self._cur_source:
                    self._open_source(*req)
                    self.stab.reset()
                frame = self._read(time.monotonic() - t0, c)
            except Exception as exc:
                # Sticky: the sim fallback keeps producing frames, so a
                # per-frame error would flash past before anyone read it.
                with self.lock:
                    self.source_error = str(exc)
                    self.source_req = ("sim", None)
                continue
            if frame is None:
                time.sleep(0.05)
                continue

            h, w = frame.shape[:2]
            maxd = int(rt["proc_max_dim"])
            if max(w, h) > maxd:
                s = maxd / max(w, h)
                frame = cv2.resize(frame, (int(w * s), int(h * s)),
                                   interpolation=cv2.INTER_AREA)
                h, w = frame.shape[:2]

            self._last_full = frame
            roi_px = roi_to_px(rt.get("roi"), w, h)

            codebook = self._get_codebook(c)
            err = None
            kind0 = self._cur_source[0] if self._cur_source else "sim"
            # Contrast experiments target captured media only. Synthetic and
            # object-sound sources remain pixel-identical to their renderer.
            dc = dict(c)
            if kind0 in ("sim", "objects"):
                dc["preprocess_enabled"] = False
            if kind0 == "objects":
                objs = self._spun_objects(time.monotonic() - t0)
                truth = objects_to_cards(objs, w, h, codebook, int(c["slots"]))
                if rt.get("sim_detect", True) and self.infer_on:
                    # run the REAL detector on the rendered plate, then carry
                    # over the sim-only attributes (flip/pitch) by nearest truth
                    try:
                        mask, cards, glyphs = detect_cards(frame, dc, codebook)
                    except Exception as exc:
                        mask = np.zeros((h, w), np.uint8)
                        cards, glyphs = [], []
                        err = f"detect error: {exc}"
                    _attach_truth(cards, truth)
                else:  # bypass detection: ground truth straight through
                    cards, glyphs = truth, []
                    mask = np.zeros((h, w), np.uint8)
                stable = []
            elif self.infer_on:
                # restrict detection to the table ROI: fill outside with the
                # background polarity so it yields no glyphs and does not
                # pollute the threshold histogram
                det_frame = frame
                if roi_px:
                    rx, ry, rx2, ry2 = roi_px
                    fill = 255 if dc["dark_on_light"] else 0
                    det_frame = np.full_like(frame, fill)
                    det_frame[ry:ry2, rx:rx2] = frame[ry:ry2, rx:rx2]
                try:
                    mask, cards, glyphs = detect_cards(det_frame, dc, codebook)
                except Exception as exc:
                    mask = np.zeros((h, w), np.uint8)
                    cards, glyphs = [], []
                    err = f"detect error: {exc}"
                if roi_px:  # belt-and-suspenders: drop anything outside the ROI
                    rx, ry, rx2, ry2 = roi_px
                    glyphs = [g for g in glyphs
                              if rx <= g.cx <= rx2 and ry <= g.cy <= ry2]
                    cards = [cd for cd in cards
                             if rx <= cd.cx <= rx2 and ry <= cd.cy <= ry2]
                self.stab.codebook = codebook
                self.stab.correction_distance = int(dc["correction_distance"])
                stable = (self.stab.update(cards, (w, h))
                          if rt["stabilize"] else [])
            else:
                mask = np.zeros((h, w), np.uint8)
                cards, glyphs, stable = [], [], []

            # objects mode emits the ground-truth cards; detection emits tracks
            osc_log = self._send_osc(rt, cards if kind0 == "objects" else stable,
                                     (w, h), roi_px)

            now = time.monotonic()
            dt = now - last
            last = now
            if dt > 0:
                fps += 0.15 * (1.0 / dt - fps)

            # Diagnostic stages are always generated so a paused still image
            # can be inspected while toggling preprocessing/extraction options.
            enhanced_gray = prepare_gray(frame, dc)
            diag_frame = frame
            if roi_px:
                rx, ry, rx2, ry2 = roi_px
                fill = 255 if dc["dark_on_light"] else 0
                diag_frame = np.full_like(frame, fill)
                diag_frame[ry:ry2, rx:rx2] = frame[ry:ry2, rx:rx2]
            diag_gray = prepare_gray(diag_frame, dc)
            diag_mask = preprocess(diag_frame, dc, gray=diag_gray)
            _, line_mask, dot_mask = extraction_maps(diag_mask, dc)
            # The extraction view must stay live while inference is stopped or
            # a ground-truth sim is running, so fall back to its own blob pass.
            diag_glyphs = glyphs or find_glyphs(diag_mask, dc, gray=diag_gray,
                                                line_mask=line_mask)
            n_line = sum(1 for g in diag_glyphs if glyph_is_line(g, dc))
            mode, params = extraction_summary(dc)

            vis = draw_overlay(frame, cards, glyphs, stable, fps,
                               infer_on=self.infer_on, graph=self._graph)
            raw_view = frame.copy() if roi_px else frame
            enhanced_bgr = cv2.cvtColor(enhanced_gray, cv2.COLOR_GRAY2BGR)
            # A/B at NATIVE scale: original on one side of a movable split,
            # preprocessed on the other, so a faint contrast change is judged
            # at the seam instead of across two half-size thumbnails.
            split = float(rt.get("compare_split", 0.5))
            compare_bgr = compare_wipe(frame, enhanced_bgr, split)
            mask_bgr = cv2.cvtColor(diag_mask, cv2.COLOR_GRAY2BGR)
            # Derived maps are tinted ONTO the frame: as bare masks they are a
            # black screen in contour mode (no line map) and in morphology
            # (almost no dot residual), which is no feedback at all.
            line_bgr = mask_over_frame(diag_gray, line_mask, DIAG_LINE_BGR)
            dot_bgr = mask_over_frame(diag_gray, dot_mask, DIAG_DOT_BGR)
            extract_bgr = draw_extraction(diag_gray, diag_mask, line_mask,
                                          dot_mask, diag_glyphs, dc)
            if roi_px:  # draw the ROI boundary on every view
                rx, ry, rx2, ry2 = roi_px
                for im in (raw_view, enhanced_bgr, mask_bgr, line_bgr,
                           dot_bgr, extract_bgr, compare_bgr, vis):
                    cv2.rectangle(im, (rx, ry), (rx2, ry2), (0, 220, 255), 2)

            # captions live in a strip under each image (never over the pixels)
            chain = preprocess_chain(dc)
            if kind0 in ("sim", "objects"):
                pp_note = "PREPROCESS BYPASSED - synthetic source keeps its render"
            elif chain:
                pp_note = "PREPROCESS: " + " > ".join(chain)
            elif dc.get("preprocess_enabled"):
                pp_note = "PREPROCESS ON - no filter selected yet"
            else:
                pp_note = "PREPROCESS OFF"
            pp_color = (110, 235, 130) if chain else (170, 170, 170)
            compare_bgr = caption(compare_bgr, [
                (f"LEFT = original   |   RIGHT = preprocessed"
                 f"   (split {round(split * 100)}%)", (0, 200, 255)),
                (pp_note, pp_color)])
            enhanced_bgr = caption(enhanced_bgr, [(pp_note, pp_color)])
            line_bgr = caption(line_bgr, [
                (f"LINE MAP [{mode}]  {params}", DIAG_LINE_BGR),
                (f"line pixels = {cv2.countNonZero(line_mask)}"
                 + ("   (contour builds no line map)"
                    if mode == "contour" else ""), (200, 200, 200))])
            dot_bgr = caption(dot_bgr, [
                (f"DOT RESIDUAL [{mode}] = candidate mask minus line map",
                 DIAG_DOT_BGR),
                (f"dot pixels = {cv2.countNonZero(dot_mask)}", (200, 200, 200))])
            mask_bgr = caption(mask_bgr, [
                ("CANDIDATE MASK after threshold + open/close", (200, 200, 200))])

            q = [cv2.IMWRITE_JPEG_QUALITY, int(rt["jpeg_quality"])]
            jr = cv2.imencode(".jpg", raw_view, q)[1].tobytes()
            jc = cv2.imencode(".jpg", compare_bgr, q)[1].tobytes()
            je = cv2.imencode(".jpg", enhanced_bgr, q)[1].tobytes()
            jm = cv2.imencode(".jpg", mask_bgr, q)[1].tobytes()
            jl = cv2.imencode(".jpg", line_bgr, q)[1].tobytes()
            jd = cv2.imencode(".jpg", dot_bgr, q)[1].tobytes()
            jx = cv2.imencode(".jpg", extract_bgr, q)[1].tobytes()
            jo = cv2.imencode(".jpg", vis, q)[1].tobytes()

            with self.lock:
                self.frames = {"raw": jr, "compare": jc, "enhanced": je,
                               "mask": jm, "lines": jl, "dots": jd,
                               "extract": jx, "overlay": jo}
                self.frame_no += 1
                kind = self._cur_source[0] if self._cur_source else "sim"
                self.results = {
                    "cards": [card_json(x) for x in cards],
                    "stable": [stable_json(s) for s in stable],
                    "glyphs": len(glyphs),
                    # what the chosen extraction method did this frame — the
                    # numbers behind the picture in the 추출 진단 view
                    "extract": {
                        "mode": mode,
                        "params": params,
                        "line_px": int(cv2.countNonZero(line_mask)),
                        "dot_px": int(cv2.countNonZero(dot_mask)),
                        "dash": n_line,
                        "dot": len(diag_glyphs) - n_line,
                        "preprocess": chain,
                        "preprocess_active": bool(chain),
                    },
                    "fps": round(fps, 1),
                    "size": [w, h],
                    "infer": self.infer_on,
                    "osc": osc_log,
                    "osc_fps": round(self.osc_fps, 1),
                    "osc_msgs": len(osc_log),
                    "video": ({"pos": self.video_pos,
                               "total": self.video_total,
                               "paused": self.paused}
                              if kind == "video" else None),
                    "error": err or self.source_error,
                }

    def _send_osc(self, rt, stable, frame_size, roi_px=None):
        """Compute the tension graph, send per-object OSC (when enabled), and
        stash the graph for the overlay. Returns [(address, args)] for the UI
        monitor. The graph is built whenever OSC OR the graph overlay is on, so
        both always agree (single source of truth)."""
        osc_on = bool(rt.get("osc_enabled"))
        show_graph = bool(rt.get("show_graph", True))
        if not osc_on:
            self._osc_client = None
            self.osc_fps = 0.0
            self._osc_last_t = None
            if not show_graph:
                self._graph = None
                return []
        w, h = frame_size
        # Normalize against the TABLE (ROI) when one is set, else the whole
        # frame. ROI-relative values stay stable when the camera is moved,
        # re-zoomed or re-resolutioned — the table becomes the unit square.
        if roi_px:
            ox, oy, x2, y2 = roi_px
            rw, rh = max(x2 - ox, 1), max(y2 - oy, 1)
        else:
            ox, oy, rw, rh = 0, 0, w, h
        known = [s for s in stable if s.code_id is not None]
        known.sort(key=lambda s: (s.cx, s.cy))
        # object-size-derived tension thresholds; the objects sim knows its own
        # plate size, other sources use the drag-calibrated value
        side_px = float(rt.get("object_side_px", 0.0) or 0.0)
        if not side_px and (self._cur_source or ("",))[0] == "objects":
            side_px = float(SIM_TRI_W)
        # distances are normalized by the TABLE long side, so d_far = 0.5 is
        # exactly half the long side (half a side on a square crop)
        long_px = max(rw, rh)
        d_near, d_far, self.tension_basis = tension_thresholds(
            rt, side_px, long_px)
        # tension algorithm knobs (switchable live to compare on stage)
        t_connect = str(rt.get("tension_connect", "all"))
        t_fold = str(rt.get("tension_fold", "max"))
        t_knn = int(rt.get("tension_knn", 3) or 3)
        t_link = float(rt.get("tension_link_radius", 0.2) or 0.2)

        # the graph = the per-object tension AND the node connections
        edges, node_t = tension_graph(known, long_px, d_near, d_far,
                                      t_connect, t_fold, t_knn, t_link)
        # stash for draw_overlay (frame-pixel node coords)
        self._graph = ([(float(s.cx), float(s.cy)) for s in known],
                       edges, node_t) if show_graph else None

        if not osc_on:
            return []

        target = (str(rt["osc_host"]), int(rt["osc_port"]))
        if self._osc_client is None or target != self._osc_target:
            try:
                from pythonosc.udp_client import SimpleUDPClient
                self._osc_client = SimpleUDPClient(*target)
                self._osc_target = target
            except Exception:
                return [("osc", "client error")]
        prefix = str(rt.get("osc_prefix", "/scramble")).rstrip("/")
        log = []

        def norm(v, origin, span):   # clamp so receivers always get 0..1
            return round(max(0.0, min(1.0, (v - origin) / span)), 4)

        # Only ONE message family is sent: the individual object. Each object
        # carries its own tension, folded from its distances to the other n-1
        # objects. Objects are identified by their BINARY morse value, which is
        # the object's real identity — not a positional index (that would
        # re-order whenever objects pass each other).

        fmt = str(rt.get("osc_format", "list"))

        def send(addr, args):
            # monitor shows the payload as it is actually structured
            try:
                self._osc_client.send_message(addr, args)
                if fmt == "dict":
                    txt = ", ".join(f"{args[i]}: {args[i + 1]}"
                                    for i in range(0, len(args) - 1, 2))
                elif fmt == "json":
                    txt = args[0]                       # already a JSON string
                else:
                    txt = ", ".join(str(a) for a in args)
                log.append([addr, txt])
            except Exception as exc:
                log.append([addr, f"send error: {exc}"])

        def emit(addr, fields, values):
            """list -> [v1, v2, ...]  ·  dict -> [k1, v1, k2, v2, ...]
               json -> ['{"k1": v1, "k2": v2, ...}']  (single JSON string arg)"""
            if fmt == "json":
                args = [json.dumps(dict(zip(fields, values)))]
            elif fmt == "dict":
                args = [a for k, v in zip(fields, values) for a in (k, v)]
            else:
                args = list(values)
            send(addr, args)

        # --- individual object (with per-object tension from the graph) ---
        for i, s in enumerate(known):
            emit(f"{prefix}/obj", OSC_OBJ_FIELDS,
                 [s.bits, norm(s.cx, ox, rw), norm(s.cy, oy, rh),
                  round(float(s.angle), 2), node_t[i],
                  1 if getattr(s, "flip", False) else 0,
                  round(self._object_freq(s), 2)])

        # measured send-cycle rate (EMA), so the UI can show real OSC fps
        now = time.monotonic()
        if self._osc_last_t is not None:
            dt = now - self._osc_last_t
            if dt > 0:
                self.osc_fps += 0.15 * (1.0 / dt - self.osc_fps)
        self._osc_last_t = now
        return log

    @staticmethod
    def _object_freq(card):
        """Per-object semitone band: explicit pitch override, else an
        ID-derived band so distinct IDs are audibly separable."""
        pitch = getattr(card, "pitch", None)
        if pitch is not None:
            return float(pitch)
        return ((int(card.code_id) * 5) % 12) - 6

    def jpeg(self, view):
        with self.lock:
            return self.frames.get(view), self.frame_no


def card_json(c):
    return {"bits": c.bits, "id": c.code_id, "raw_id": c.raw_id,
            "label": c.label, "status": c.status, "score": round(c.score, 2),
            "cx": round(c.cx, 1), "cy": round(c.cy, 1),
            "angle": round(c.angle, 1)}


def stable_json(s):
    return {"track": s.track_id, "bits": s.bits, "id": s.code_id,
            "label": s.label, "status": s.status, "age": s.age,
            "missed": s.missed, "votes": s.votes,
            "cx": round(s.cx, 1), "cy": round(s.cy, 1),
            "angle": round(s.angle, 1)}


def draw_tension_graph(vis, graph):
    """Draw the tension graph: edges colored/weighted by pair tension, and each
    node's folded tension. `graph` = (node_xy, edges, node_tensions)."""
    points, edges, node_t = graph
    pts = [(int(x), int(y)) for x, y in points]
    for i, j, t in edges:
        if t <= 0.001:                 # far / inactive: faint thin link
            cv2.line(vis, pts[i], pts[j], (70, 70, 70), 1, cv2.LINE_AA)
            continue
        col = tension_color(t)
        cv2.line(vis, pts[i], pts[j], col, 1 + int(round(2 * t)), cv2.LINE_AA)
        if t >= 0.1:                    # label the taut edges at their midpoint
            mx, my = (pts[i][0] + pts[j][0]) // 2, (pts[i][1] + pts[j][1]) // 2
            cv2.putText(vis, f"{t:.2f}", (mx - 12, my - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
    for (x, y), t in zip(pts, node_t):  # node = folded tension
        col = tension_color(t)
        cv2.circle(vis, (x, y), 5, col, -1, cv2.LINE_AA)
        cv2.putText(vis, f"{t:.2f}", (x + 9, y - 9),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
    return vis


def draw_overlay(frame, cards, glyphs, stable, fps, infer_on=True, graph=None):
    vis = frame.copy()
    if vis.ndim == 2:
        vis = cv2.cvtColor(vis, cv2.COLOR_GRAY2BGR)
    if not infer_on:
        cv2.putText(vis, "INFERENCE OFF", (10, 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 0, 255), 2, cv2.LINE_AA)
        return vis
    colors = {"start": (0, 220, 255), "dash": (180, 80, 255),
              "dot": (0, 200, 0), "unknown": (110, 110, 110)}
    # distinct colour + on-mark label per SHAPE when the classifier is on, so
    # you can read which physical marker each blob was recognized as
    shape_colors = {"circle": (0, 200, 0), "line": (180, 80, 255),
                    "square": (0, 220, 255), "triangle": (255, 210, 0),
                    "pentagon": (255, 120, 0), "hexagon": (200, 0, 200),
                    "star": (0, 140, 255)}
    anchor_shapes = ("square", "triangle", "pentagon", "hexagon", "star")
    for g in glyphs:
        col = shape_colors.get(g.shape) or colors.get(g.kind, (110,) * 3)
        cv2.drawContours(vis, [g.contour], -1, col, 1)
        if g.shape in anchor_shapes:  # label the anchors
            cv2.putText(vis, g.shape, (int(g.cx) - 12, int(g.cy) - 11),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
    for card in cards:
        ok = card.code_id is not None
        color = (0, 230, 0) if ok else (0, 0, 255)
        cv2.line(vis, (int(card.start.cx), int(card.start.cy)),
                 (int(card.end.cx), int(card.end.cy)), color, 2, cv2.LINE_AA)
        for slot in card.slots:
            cv2.putText(vis, slot.bit, (int(slot.cx) - 4, int(slot.cy) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)
        shape = f"[{card.start.shape}]" if card.start.shape else ""
        cv2.putText(vis, f"{card.label}{shape} {card.bits} {card.status}",
                    (int(card.cx) - 80, int(card.cy) - 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    for s in stable:
        color = (255, 200, 0) if s.code_id is not None else (0, 120, 255)
        cv2.circle(vis, (int(s.cx), int(s.cy)), 14, color, 2, cv2.LINE_AA)
        cv2.putText(vis, f"T{s.track_id}:{s.label}",
                    (int(s.cx) - 30, int(s.cy) + 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    if graph and graph[0]:
        draw_tension_graph(vis, graph)
    hud = f"fps={fps:4.1f} cards={len(cards)} stable={len(stable)}"
    cv2.putText(vis, hud, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (255, 255, 255), 2, cv2.LINE_AA)
    return vis


def make_app(pipe):
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/stream/<view>")
    def stream(view):
        if view not in VIEWS:
            return "unknown view", 404

        def gen():
            seen = -1
            while True:
                buf, n = pipe.jpeg(view)
                if buf is None or n == seen:
                    time.sleep(0.02)
                    continue
                seen = n
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(buf)).encode()
                       + b"\r\n\r\n" + buf + b"\r\n")

        return Response(gen(),
                        mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/schema")
    def schema():
        return jsonify({"options": SCHEMA, "axes": AXES,
                        "osc_fields": {"obj": OSC_OBJ_FIELDS}})

    @app.get("/api/state")
    def state():
        morse, runtime = pipe.get_cfg()
        with pipe.lock:
            results = dict(pipe.results)
            source = pipe.source_desc
        return jsonify({"cfg": morse, "runtime": runtime,
                        "source": source, "results": results,
                        "templates": pipe.template_labels(),
                        "cam_status": pipe.cam_status,
                        "objects": runtime.get("sim_objects", []),
                        "tension_basis": getattr(pipe, "tension_basis", "")})

    @app.post("/api/config")
    def config():
        pipe.update_cfg(request.get_json(force=True) or {})
        return jsonify({"ok": True})


    @app.post("/api/config/reset")
    def reset():
        pipe.reset_cfg()
        return jsonify({"ok": True})

    @app.get("/api/configs")
    def configs():
        return jsonify({"configs": list_config_names(),
                        "active": pipe.active_name})

    @app.post("/api/config/save")
    def save():
        body = request.get_json(force=True) or {}
        name = pipe.save_cfg(body.get("name"))
        return jsonify({"ok": True, "name": name,
                        "configs": list_config_names()})

    @app.post("/api/config/load")
    def load():
        body = request.get_json(force=True) or {}
        name = pipe.load_cfg(body.get("name"))
        return jsonify({"ok": True, "name": name})

    @app.post("/api/source")
    def source():
        body = request.get_json(force=True) or {}
        kind = body.get("type", "sim")
        arg = body.get("arg")
        pipe.set_source(kind, arg)
        return jsonify({"ok": True})

    @app.post("/api/roi")
    def roi():
        body = request.get_json(force=True) or {}
        pipe.set_roi(body.get("roi"))
        return jsonify({"ok": True, "roi": pipe.runtime["roi"]})

    @app.post("/api/template/capture")
    def template_capture():
        body = request.get_json(force=True) or {}
        res = pipe.capture_template(body.get("label"), body.get("roi"))
        res["templates"] = pipe.template_labels()
        return jsonify(res)

    @app.post("/api/template/clear")
    def template_clear():
        pipe.clear_templates()
        return jsonify({"ok": True, "templates": []})

    @app.post("/api/camera")
    def camera_props():
        pipe.set_camera(request.get_json(force=True) or {})
        return jsonify({"ok": True})

    @app.post("/api/calibrate")
    def calibrate():
        b = request.get_json(force=True) or {}
        return jsonify(pipe.calibrate_side(b.get("x1"), b.get("y1"),
                                           b.get("x2"), b.get("y2")))

    @app.post("/api/object")
    def object_op():
        body = request.get_json(force=True) or {}
        objs = pipe.object_op(body.get("action", ""), body)
        return jsonify({"ok": True, "objects": objs})

    @app.get("/api/cameras")
    def cameras():
        return jsonify({"cameras": list_cameras()})

    @app.get("/api/media")
    def media():
        kind = request.args.get("kind", "video")
        return jsonify({"media": list_media(kind)})

    @app.post("/api/transport")
    def transport():
        body = request.get_json(force=True) or {}
        pipe.transport(body.get("action"), body.get("seek"))
        return jsonify({"ok": True})

    @app.post("/api/pipeline")
    def pipeline_ctl():
        body = request.get_json(force=True) or {}
        pipe.infer_on = bool(body.get("infer", True))
        if not pipe.infer_on:
            pipe.stab.reset()
        return jsonify({"ok": True, "infer": pipe.infer_on})

    return app


def main():
    ap = argparse.ArgumentParser(description="Morse tracker web tuner")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--camera", type=int)
    src.add_argument("--video")
    src.add_argument("--image")
    args = ap.parse_args()

    source = ("sim", None)
    if args.camera is not None:
        source = ("camera", args.camera)
    elif args.video:
        source = ("video", args.video)
    elif args.image:
        source = ("image", args.image)

    pipe = Pipeline(source)
    pipe.seed_configs()
    pipe.start()
    app = make_app(pipe)
    print(f"* Morse web tuner: http://localhost:{args.port}  (source: {source[0]})")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
