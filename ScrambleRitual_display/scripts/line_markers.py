"""
Line-style fiducial generator for reacTIVision's amoeba tree dictionary.

reacTIVision identifies a marker only by the nesting (topology) of its black
and white regions, never by shape or position. Every tree in
symbols/amoeba/default.trees has depth 2 under a white root, so each ID reads
as a list of line figures:

  figure with k cells  = black region holding k white holes (a closed ladder)
  dash                 = black region with no holes (an open line segment)

e.g. ID 0  w0122222212212121111  ->  figures [6, 2, 1, 1] + 4 dashes.

Rotation comes from leaf centroids: C = mean of all leaves (cells + dashes),
B = mean of the dashes, angle = C -> B. So the dashes form the head of a "T"
along the upper edges and the figures form the stem below them.

Run:  python scripts/line_markers.py [--out DIR] [--trees PATH] [--ids 0-24]
Writes one SVG per ID and style plus report.json (decode + rotation check on
a raster of every marker, rotated and downsampled).
"""

import argparse
import json
import math
import os
from collections import deque

import numpy as np
from PIL import Image, ImageDraw

R = 100.0            # tile circumradius (flat-top hexagon), SVG units
S = 4.0              # stroke: every black line and every white gap >= S
FRAME_R = 94.0       # closed outer frame, outer circumradius
MARGIN = 6.0         # clear white between frame, dashes and figures
DASH_LEN = 14.0
SQ3 = math.sqrt(3)

DEFAULT_TREES = os.path.join(os.path.expanduser("~"), "scramble_ritual", "reactivision",
                             "symbols", "amoeba", "default.trees")

# dash slots on the three upper edges, as fractions along each edge
DASH_TEMPLATES = {
    4: {"top": [0.32, 0.68], "ul": [0.5], "ur": [0.5]},
    5: {"top": [0.22, 0.5, 0.78], "ul": [0.5], "ur": [0.5]},
    6: {"top": [0.32, 0.68], "ul": [0.3, 0.7], "ur": [0.3, 0.7]},
    7: {"top": [0.22, 0.5, 0.78], "ul": [0.3, 0.7], "ur": [0.3, 0.7]},
    8: {"top": [0.32, 0.68], "ul": [0.22, 0.5, 0.78], "ur": [0.22, 0.5, 0.78]},
    9: {"top": [0.22, 0.5, 0.78], "ul": [0.22, 0.5, 0.78], "ur": [0.22, 0.5, 0.78]},
    10: {"top": [0.14, 0.38, 0.62, 0.86], "ul": [0.22, 0.5, 0.78], "ur": [0.22, 0.5, 0.78]},
}


# ---------------------------------------------------------------- trees

def parse_tree(s):
    """'w0122...' -> (root colour, sorted cell counts per figure, dash count)."""
    depth = [int(c) for c in s[1:]]
    figs, cur = [], None
    for d in depth[1:]:
        if d == 1:
            if cur is not None:
                figs.append(cur)
            cur = 0
        elif d == 2:
            cur += 1
        else:
            raise ValueError("depth > 2 not supported: " + s)
    figs.append(cur)
    cells = sorted((f for f in figs if f), reverse=True)
    return s[0], cells, figs.count(0)


def load_trees(path):
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip()]


# ---------------------------------------------------------------- geometry

def hex_pts(r, cx=0.0, cy=0.0):
    return [(cx + r * math.cos(math.radians(a)), cy - r * math.sin(math.radians(a)))
            for a in range(0, 360, 60)]


def rect(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def seg_rect(p, q, w):
    """Filled rectangle of width w along segment p-q."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = math.hypot(dx, dy)
    ox, oy = -dy / n * w / 2, dx / n * w / 2
    return [(p[0] + ox, p[1] + oy), (q[0] + ox, q[1] + oy),
            (q[0] - ox, q[1] - oy), (p[0] - ox, p[1] - oy)]


INNER_APOTHEM = FRAME_R * SQ3 / 2 - S          # white root boundary
DASH_APOTHEM = INNER_APOTHEM - MARGIN - S / 2   # dash centre line
FIG_APOTHEM = DASH_APOTHEM - S / 2 - MARGIN     # figures stay inside this hex


def dash_shapes(n):
    t = DASH_TEMPLATES[n]
    r = DASH_APOTHEM * 2 / SQ3
    v = hex_pts(r)                     # v0 at 0deg, v1 60, v2 120, v3 180
    edges = {"ur": (v[0], v[1]), "top": (v[1], v[2]), "ul": (v[2], v[3])}
    out = []
    for key in ("ul", "top", "ur"):
        a, b = edges[key]
        for f in t[key]:
            cx, cy = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
            ux, uy = (b[0] - a[0]) / r, (b[1] - a[1]) / r
            p = (cx - ux * DASH_LEN / 2, cy - uy * DASH_LEN / 2)
            q = (cx + ux * DASH_LEN / 2, cy + uy * DASH_LEN / 2)
            out.append({"poly": seg_rect(p, q, S), "c": (cx, cy)})
    return out


# A figure is drawn at the origin and returns (w, h, black shapes, white holes).
# Shapes: ("poly", pts) or ("circle", cx, cy, r). Holes carry their centroid.

def ladder(k):
    cw, ch = 10.0, 12.0
    rows = [k] if k <= 4 else [math.ceil(k / 2), k // 2]
    w = rows[0] * (cw + S) + S
    h = len(rows) * (ch + S) + S
    black = [("poly", rect(0, 0, w, h))]
    holes = []
    for ri, n in enumerate(rows):
        y = S + ri * (ch + S)
        hw = (w - S - n * S) / n
        for i in range(n):
            x = S + i * (hw + S)
            holes.append((("poly", rect(x, y, hw, ch)), (x + hw / 2, y + ch / 2)))
    return w, h, black, holes


def chain(k):
    ro, ri, pitch = 8.0, 4.0, 19.0
    nrows = math.ceil(k / 5) if k > 5 else 1
    per = math.ceil(k / nrows)
    rows = [min(per, k - r * per) for r in range(nrows)]
    w = (rows[0] - 1) * pitch + 2 * ro
    h = (len(rows) - 1) * pitch + 2 * ro
    centres = []
    for r_i, n in enumerate(rows):
        xs = [ro + i * pitch for i in range(n)]
        if r_i % 2:                        # snake back: odd rows start under the previous row's end
            xs = [ro + (rows[0] - 1 - i) * pitch for i in range(n)]
        centres += [(x, ro + r_i * pitch) for x in xs]
    black = []
    for a, b in zip(centres, centres[1:]):
        black.append(("poly", seg_rect(a, b, S)))
    for (x, y) in centres:
        black.append(("circle", x, y, ro))
    holes = [(("circle", x, y, ri), (x, y)) for (x, y) in centres]
    return w, h, black, holes


def _hex_spiral(k):
    """Axial coords of k cells filled ring by ring (compact, never encloses a void)."""
    cells = [(0, 0)]
    dirs = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]
    ring = 1
    while len(cells) < k:
        q, r = -ring, ring                  # start corner of the ring, walk its 6 sides
        for d in range(6):
            for _ in range(ring):
                cells.append((q, r))
                q, r = q + dirs[d][0], r + dirs[d][1]
        ring += 1
    return cells[:k]


def honey(k, apothem=5.9):
    """Honeycomb cluster: k pointy-top hex cells sharing walls of width S."""
    pitch = 2 * apothem + S                 # centre spacing between neighbours
    cs = [(pitch * (q + r / 2), pitch * SQ3 / 2 * r) for q, r in _hex_spiral(k)]

    def hexagon(cx, cy, ap):
        rr = ap * 2 / SQ3
        return [(cx + rr * math.cos(math.radians(a)), cy + rr * math.sin(math.radians(a)))
                for a in range(30, 390, 60)]

    outer = [hexagon(x, y, pitch / 2 + S / 2) for x, y in cs]
    xs = [p[0] for h in outer for p in h]
    ys = [p[1] for h in outer for p in h]
    ox, oy = -min(xs), -min(ys)
    black = [("poly", [(x + ox, y + oy) for x, y in h]) for h in outer]
    holes = [(("poly", hexagon(x + ox, y + oy, apothem)), (x + ox, y + oy)) for x, y in cs]
    return max(xs) - min(xs), max(ys) - min(ys), black, holes


STYLES = {"ladder": ladder, "chain": chain, "honey": honey}


def inside_fig_hex(x, y):
    """Point inside the hexagon whose apothem is FIG_APOTHEM (flat top)."""
    ax, ay = abs(x), abs(y)
    return ay <= FIG_APOTHEM and ax * SQ3 / 2 + ay / 2 <= FIG_APOTHEM


def layout(cells, n_dash, style, gap=8.0):
    dashes = dash_shapes(n_dash)
    dash_boxes = [(min(p[0] for p in d["poly"]) - MARGIN, min(p[1] for p in d["poly"]) - MARGIN,
                   max(p[0] for p in d["poly"]) + MARGIN, max(p[1] for p in d["poly"]) + MARGIN)
                  for d in dashes]
    figs = [STYLES[style](k) for k in cells]
    best = None
    for wmax in (90, 110, 130, 150):
        lines, cur, cw = [], [], 0.0
        for f in figs:
            add = f[0] + (gap if cur else 0)
            if cur and cw + add > wmax:
                lines.append(cur)
                cur, cw = [], 0.0
                add = f[0]
            cur.append(f)
            cw += add
        lines.append(cur)
        block_h = sum(max(f[1] for f in ln) for ln in lines) + gap * (len(lines) - 1)
        for y0 in np.arange(-70, 70, 1.0):
            placed, y, ok = [], y0, True
            for ln in lines:
                lw = sum(f[0] for f in ln) + gap * (len(ln) - 1)
                lh = max(f[1] for f in ln)
                x = -lw / 2
                for f in ln:
                    fy = y + (lh - f[1]) / 2
                    box = (x, fy, x + f[0], fy + f[1])
                    if not all(inside_fig_hex(px, py) for px in (box[0], box[2]) for py in (box[1], box[3])):
                        ok = False
                    if any(box[0] < d[2] and box[2] > d[0] and box[1] < d[3] and box[3] > d[1]
                           for d in dash_boxes):
                        ok = False
                    placed.append((f, x, fy))
                    x += f[0] + gap
                y += lh + gap
            if not ok:
                continue
            pts = [d["c"] for d in dashes] + [(hx + px, hy + py)
                                             for (f, px, py) in placed for (_, (hx, hy)) in f[3]]
            c = np.mean(pts, axis=0)
            score = math.hypot(*c)
            if best is None or score < best[0]:
                best = (score, placed, block_h)
    if best is None:
        raise RuntimeError(f"no layout for {cells}+{n_dash} ({style})")
    return dashes, best[1]


def build(cells, n_dash, style):
    """Paint list [(shape, colour)] in order, plus leaf centroids for rotation."""
    dashes, placed = layout(cells, n_dash, style)
    paint = [(("poly", hex_pts(R)), "w"),
             (("poly", hex_pts(FRAME_R)), "b"),
             (("poly", hex_pts(INNER_APOTHEM * 2 / SQ3)), "w")]
    white_c, black_c = [], []
    for d in dashes:
        paint.append((("poly", d["poly"]), "b"))
        black_c.append(d["c"])
    for (w, h, black, holes), px, py in placed:
        for sh in black:
            paint.append((shift(sh, px, py), "b"))
        for sh, (hx, hy) in holes:
            paint.append((shift(sh, px, py), "w"))
            white_c.append((hx + px, hy + py))
    return paint, white_c, black_c


def shift(sh, dx, dy):
    if sh[0] == "poly":
        return ("poly", [(x + dx, y + dy) for x, y in sh[1]])
    return ("circle", sh[1] + dx, sh[2] + dy, sh[3])


# ---------------------------------------------------------------- output

def to_svg(paint, size=240, label=None):
    col = {"w": "#ffffff", "b": "#000000"}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-100 -100 200 200" '
           f'width="{size}" height="{size}">']
    for sh, c in paint:
        if sh[0] == "poly":
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in sh[1])
            out.append(f'<polygon points="{pts}" fill="{col[c]}"/>')
        else:
            out.append(f'<circle cx="{sh[1]:.2f}" cy="{sh[2]:.2f}" r="{sh[3]:.2f}" fill="{col[c]}"/>')
    out.append("</svg>")
    return "\n".join(out)


def raster(paint, px, rot_deg=0.0, ss=4):
    """Tile on a black table, rotated, rendered at px pixels across, area-averaged."""
    n = int(px * 1.6) * ss
    sc = px * ss / (2 * R)
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)

    def tr(x, y):
        return (n / 2 + (x * ca - y * sa) * sc, n / 2 + (x * sa + y * ca) * sc)

    img = Image.new("L", (n, n), 0)
    dr = ImageDraw.Draw(img)
    for sh, c in paint:
        fill = 255 if c == "w" else 0
        if sh[0] == "poly":
            dr.polygon([tr(x, y) for x, y in sh[1]], fill=fill)
        else:
            cx, cy = tr(sh[1], sh[2])
            r = sh[3] * sc
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    img = img.resize((n // ss, n // ss), Image.BOX)
    return np.asarray(img) >= 128


def region_tree(binimg):
    """Label 4-connected black / 8-connected white regions, return the depth-2
    subtrees hanging off any white region: list of (cells, dashes)."""
    h, w = binimg.shape
    lab = -np.ones((h, w), int)
    colour = []
    for y in range(h):
        for x in range(w):
            if lab[y, x] >= 0:
                continue
            c = binimg[y, x]
            i = len(colour)
            colour.append(c)
            q = deque([(y, x)])
            lab[y, x] = i
            nb = ((1, 0), (-1, 0), (0, 1), (0, -1)) + (((1, 1), (1, -1), (-1, 1), (-1, -1)) if c else ())
            while q:
                cy, cx = q.popleft()
                for dy, dx in nb:
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and lab[ny, nx] < 0 and binimg[ny, nx] == c:
                        lab[ny, nx] = i
                        q.append((ny, nx))
    adj = [set() for _ in colour]
    a, b = lab[:, :-1], lab[:, 1:]
    m = a != b
    for u, v in zip(a[m], b[m]):
        adj[u].add(v); adj[v].add(u)
    a, b = lab[:-1, :], lab[1:, :]
    m = a != b
    for u, v in zip(a[m], b[m]):
        adj[u].add(v); adj[v].add(u)
    depth = [None] * len(colour)
    root = lab[0, 0]
    depth[root] = 0
    q = deque([root])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if depth[v] is None:
                depth[v] = depth[u] + 1
                q.append(v)
    kids = [[v for v in adj[u] if depth[v] == depth[u] + 1] for u in range(len(colour))]
    found = []
    for u in range(len(colour)):
        if not colour[u] or not kids[u]:
            continue
        figs = []
        ok = True
        for v in kids[u]:
            gk = kids[v]
            if any(kids[g] for g in gk):
                ok = False
            figs.append(len(gk))
        if ok:
            found.append((sorted((f for f in figs if f), reverse=True), figs.count(0)))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "docs", "line-markers"))
    ap.add_argument("--trees", default=DEFAULT_TREES)
    ap.add_argument("--ids", default="0-24")
    ap.add_argument("--styles", default="ladder,chain")
    ap.add_argument("--sizes", default="80,60,48,40,32")
    a = ap.parse_args()
    lo, hi = (int(v) for v in a.ids.split("-"))
    trees = load_trees(a.trees)
    os.makedirs(a.out, exist_ok=True)
    report = []
    for style in a.styles.split(","):
        for i in range(lo, hi + 1):
            root, cells, nd = parse_tree(trees[i])
            paint, wc, bc = build(cells, nd, style)
            with open(os.path.join(a.out, f"{style}_{i:02d}.svg"), "w") as f:
                f.write(to_svg(paint))
            allc = np.mean(wc + bc, axis=0)
            bcen = np.mean(bc, axis=0)
            v = bcen - allc
            ang = math.degrees(math.atan2(v[0], -v[1]))   # 0 = straight up
            min_px = None
            for px in sorted((int(s) for s in a.sizes.split(",")), reverse=True):
                good = all((cells, nd) in region_tree(raster(paint, px, rot)) for rot in (0, 23, 45))
                if not good:
                    break
                min_px = px
            report.append({"style": style, "id": i, "tree": trees[i], "cells": cells, "dashes": nd,
                           "center_offset": round(float(math.hypot(*allc)) / R, 3),
                           "dir_angle": round(ang, 1), "dir_len": round(float(math.hypot(*v)) / R, 3),
                           "min_px_ok": min_px})
            print(report[-1])
    with open(os.path.join(a.out, "report.json"), "w") as f:
        json.dump(report, f, indent=1)


if __name__ == "__main__":
    main()
