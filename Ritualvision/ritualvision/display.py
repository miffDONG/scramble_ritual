"""Preview window: camera image (or the line mask) with what the recognizer found.

For each fold line the recognizer finds, the hexagon it implies is drawn on the image:
  outer hexagon  the tile edge predicted from the fold (vertex = fold corner direction)
  inner hexagon  the ring the ID lines sit on (radius rc = fold arm length)
green = id found, red = fold found but dash code not in the id table.
"""

import math

import cv2
import numpy as np

GREEN = (80, 220, 80)
AMBER = (40, 180, 250)
RED = (60, 60, 230)
WHITE = (240, 240, 240)
GREY = (150, 150, 150)

TILE_PER_RC = 1.0 / (0.76 * 2 / math.sqrt(3))     # tile vertex radius / fold arm length

# reacTIVision 1.5.1 keys (same letters, same meaning)
HELP = ["s  source image", "t  target image (line mask)", "n  no image",
        "g  threshold: offset (up/down) / window (left/right)", "o  camera options",
        "e  toggle frame equalizer", "p  pause processing", "v  verbose output (console)",
        "F1  toggle fullscreen", "h  toggle help text", "ESC  quit",
        "m  marker overlay on/off (Ritualvision only)"]


def _hexagon(cx, cy, radius, first_vertex_deg):
    pts = []
    for k in range(6):
        t = math.radians(first_vertex_deg + 60 * k)
        pts.append([int(round(cx + radius * math.cos(t))), int(round(cy + radius * math.sin(t)))])
    return np.array(pts, dtype=np.int32)


def _label(img, text, org, scale, color, thick):
    """Text in one colour on a dark translucent box (no black copy underneath)."""
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    x, y = org
    pad = 3
    x0, y0 = max(0, x - pad), max(0, y - th - pad)
    x1, y1 = min(img.shape[1], x + tw + pad), min(img.shape[0], y + base + pad)
    if x1 > x0 and y1 > y0:
        roi = img[y0:y1, x0:x1]
        img[y0:y1, x0:x1] = (roi * 0.35).astype(roi.dtype)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def draw(frame, mask, markers, show_mask, fps, offset, show_help, msg="", block=15,
         size_state="", noise=0, overlay_lines=(), overlay=True):
    """overlay=False: no marker drawings (hexagons, IDs, fold lines, dashes, arrows) on the
    image; the status text stays."""
    if show_mask and mask is not None:
        img = cv2.cvtColor(255 - mask, cv2.COLOR_GRAY2BGR)
    else:
        img = frame.copy() if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    for m in (markers if overlay else ()):
        ok = m.fid >= 0
        col = GREEN if ok else RED
        # hexagon implied by the fold line
        cv2.polylines(img, [_hexagon(m.x, m.y, m.size * TILE_PER_RC, m.corner_dir)], True, col, 2, cv2.LINE_AA)
        cv2.polylines(img, [_hexagon(m.x, m.y, m.size, m.corner_dir)], True, GREY, 1, cv2.LINE_AA)
        A, C, B = m.anchor_pts
        cv2.polylines(img, [np.array([[int(p[0]), int(p[1])] for p in (A, C, B)], np.int32)], False, col, 3, cv2.LINE_AA)
        for d in m.dash_pts:
            cv2.circle(img, (int(d[0]), int(d[1])), 3, AMBER, -1, cv2.LINE_AA)
        cx, cy = int(m.x), int(m.y)
        t = math.radians(m.angle + 210.0)
        cv2.arrowedLine(img, (cx, cy), (int(m.x + 0.6 * m.size * math.cos(t)), int(m.y + 0.6 * m.size * math.sin(t))),
                        AMBER, 2, cv2.LINE_AA, tipLength=0.25)
        label = f"ID {m.fid}" if ok else "?"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        _label(img, label, (cx - tw // 2, cy + th // 2), 0.9, col, 2)
        _label(img, f"{''.join(map(str, m.code))}  {m.angle:5.1f}deg  fold {m.arm_angle:5.1f}",
               (cx - 85, int(cy + m.size * TILE_PER_RC + 18)), 0.48, col, 1)
    found = sum(1 for m in markers if m.fid >= 0)
    view = "target" if (show_mask and mask is not None) else "source"
    status = f"{fps:5.1f} fps  markers {found}  offset {offset:.0f}  window {block}  [{view}]  {size_state}"
    if noise:
        status += f"  dropped {noise}"
    _texts(img, status, show_help, msg, overlay_lines)
    return img


def no_image(fps, markers, show_help, msg="", overlay_lines=()):
    """n: no camera image (as reacTIVision's display off), only the status text."""
    img = np.zeros((300 if show_help or overlay_lines else 90, 640, 3), np.uint8)
    found = sum(1 for m in markers if m.fid >= 0)
    _texts(img, f"{fps:5.1f} fps  markers {found}  [no image]  s source / t target",
           show_help, msg, overlay_lines)
    return img


def _texts(img, status, show_help, msg, overlay_lines):
    _label(img, status + ("" if show_help else "   h: help"), (10, 24), 0.6, WHITE, 1)
    y = 50
    for line in overlay_lines:                          # g threshold settings
        _label(img, line, (10, y), 0.7, AMBER, 2)
        y += 28
    if msg:
        _label(img, msg, (10, y), 0.6, WHITE, 1)
        y += 24
    if show_help:
        for line in HELP:
            _label(img, line, (10, y), 0.5, WHITE, 1)
            y += 20
