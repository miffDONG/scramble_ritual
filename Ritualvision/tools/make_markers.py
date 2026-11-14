"""Print files for every marker in ids.json: PNG (1200 px, transparent outside the tile) + SVG.

  python tools/make_markers.py [--ids ids.json] [--out markers] [--only 25-59]

Geometry (tile circumradius R = 100, pointy-top, design frame = 0 deg, y down):
  anchor  one line folded over the upper-left + left edges, on the inset hexagon (0.76 R)
  dashes  UR, R, LR, LL edges; n dashes share one span along the edge.
          n <= 3: span 1.24 x half-edge, gap 0.18 x half-edge  (IDs 0-24, unchanged)
          n == 4: span 70 % of the edge, gap 1.5 x line width  (keeps each dash >= 2.7 x width
                  and 15 % corner clearance)
"""

import argparse
import json
import math
import os

from PIL import Image, ImageDraw, ImageFont

R, INSET, W = 100.0, 76.0, 4.0
HALF = INSET / math.sqrt(3)          # half edge length on the inset hexagon
RC = INSET * 2 / math.sqrt(3)        # inset circumradius = edge length
ID_EDGES = (("UR", 300), ("R", 0), ("LR", 60), ("LL", 120))


def seg(a, b, w):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    ox, oy = -dy / n * w / 2, dx / n * w / 2
    return [(a[0] + ox, a[1] + oy), (b[0] + ox, b[1] + oy), (b[0] - ox, b[1] - oy), (a[0] - ox, a[1] - oy)]


def dash_layout(n):
    if n == 4:
        span, gap = 0.70 * RC, 1.5 * W
    else:
        span, gap = 1.24 * HALF, 0.18 * HALF
    return span, gap, (span - gap * (n - 1)) / n


def shapes(code):
    """[(colour, polygon)] painted in order: white tile, then black marks."""
    out = [("w", [(R * math.cos(math.radians(a)), R * math.sin(math.radians(a))) for a in range(30, 390, 60)])]
    v = lambda a: (RC * math.cos(math.radians(a)), RC * math.sin(math.radians(a)))
    a, c, b = v(270), v(210), v(150)
    out += [("b", seg(a, c, W)), ("b", seg(c, b, W))]
    out.append(("b", [c, seg(a, c, W)[1], seg(c, b, W)[0]]))     # fill the fold's outer corner
    for (_, nrm), n in zip(ID_EDGES, code):
        if not n:
            continue
        m = math.radians(nrm)
        nx, ny = math.cos(m), math.sin(m)
        tx, ty = -ny, nx
        span, gap, L = dash_layout(n)
        for i in range(n):
            o = (i - (n - 1) / 2) * (L + gap)
            px, py = nx * INSET + tx * o, ny * INSET + ty * o
            out.append(("b", seg((px - tx * L / 2, py - ty * L / 2), (px + tx * L / 2, py + ty * L / 2), W)))
    return out


def png(code, px=1200, ss=3):
    n = px * ss
    sc = n / (2 * R)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for c, poly in shapes(code):
        d.polygon([((x + R) * sc, (y + R) * sc) for x, y in poly],
                  fill=(255, 255, 255, 255) if c == "w" else (0, 0, 0, 255))
    return img.resize((px, px), Image.LANCZOS)


def svg(code):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="-100 -100 200 200" width="200mm" height="200mm">']
    for c, poly in shapes(code):
        parts.append('<polygon points="%s" fill="%s"/>' % (" ".join("%.3f,%.3f" % p for p in poly),
                                                           "#ffffff" if c == "w" else "#000000"))
    return "\n".join(parts + ["</svg>"])


def sheet(rows, path, cols=10):
    cell_w, cell_h = 220, 250
    k = len(rows)
    img = Image.new("RGB", (cols * cell_w, math.ceil(k / cols) * cell_h), (22, 24, 29))
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("consola.ttf", 16)
    except OSError:
        f = ImageFont.load_default()
    for j, r in enumerate(rows):
        code = (r["UR"], r["R"], r["LR"], r["LL"])
        x, y = (j % cols) * cell_w, (j // cols) * cell_h
        t = png(code, 200, 2)
        img.paste(t, (x + 10, y + 6), t)
        d.text((x + 28, y + 216), "ID %02d  %s" % (r["id"], "·".join(map(str, code))), fill=(220, 224, 230), font=f)
    img.save(path)


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default=os.path.join(here, "ids.json"))
    ap.add_argument("--out", default=os.path.join(here, "markers"))
    ap.add_argument("--only", help="id range to write, e.g. 25-59 (default: all)")
    a = ap.parse_args()
    rows = json.load(open(a.ids, encoding="utf-8"))
    lo, hi = (int(v) for v in a.only.split("-")) if a.only else (0, 10 ** 9)
    os.makedirs(a.out, exist_ok=True)
    for r in rows:
        if not lo <= r["id"] <= hi:
            continue
        code = (r["UR"], r["R"], r["LR"], r["LL"])
        png(code).save(os.path.join(a.out, r["file"] + ".png"))
        with open(os.path.join(a.out, r["file"] + ".svg"), "w") as fh:
            fh.write(svg(code))
    sheet(rows, os.path.join(a.out, "_sheet.png"))
    print(f"{a.out}: {sum(lo <= r['id'] <= hi for r in rows)} markers written, sheet of {len(rows)}")
