"""마커 패턴 인식 & 분류부 (marker recognition and classification).

Marker (pointy-top hexagon, design frame = 0 deg, image coords with y down):
  anchor  one continuous line folded over the upper-left + left edges; its corner is the
          upper-left vertex, at 210 deg from the tile centre
  dashes  the other four edges carry 0-3 / 1-3 / 1-3 / 0-3 dashes  (UR, R, LR, LL)
  id      the four dash counts, looked up in ids.json (anything else is rejected)

Per frame:
  1  binarize: a pixel is "line" when darker than its local mean - offset (adaptive)
  2  connected components of the line mask
  3  anchors: components shaped like two equal straight arms meeting at 120 deg.
     arm lines -> corner C, arm length rc (= inset circumradius), centre O = C + rc*(u1+u2)
  4  dashes: elongated components around O at radius ~0.87 rc, axis along the edge
  5  angle: edge normals mod 60 from the circular mean of 6*psi over arms + dashes,
     then the top corner is the candidate (normal + 30 + 60k) nearest to O -> C
  6  id: count dashes in the UR / R / LR / LL sectors relative to the corner
"""

import json
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

CORNER_DEG = 210.0                       # anchor corner direction at 0 deg rotation
FACE_MARGIN = 4                          # binarize(): a line is this much darker than the tile face
# ID edges: outward normal relative to the corner direction
SECTORS = (("UR", 90.0), ("R", 150.0), ("LR", -150.0), ("LL", -90.0))


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


@dataclass
class Marker:
    fid: int                      # -1 when the dash code is not in the id table
    code: tuple
    x: float                      # centre, pixels
    y: float
    angle: float                  # degrees, clockwise, 0 = design orientation
    size: float                   # rc in pixels
    arm_angle: float = 120.0      # measured fold angle
    corner_dir: float = 210.0     # direction centre -> fold corner (deg, image coords)
    corner: tuple = (0.0, 0.0)
    anchor_pts: tuple = ()
    dash_pts: list = field(default_factory=list)


def load_ids(path):
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    return {(r["UR"], r["R"], r["LR"], r["LL"]): int(r["id"]) for r in rows}


class Recognizer:
    def __init__(self, ids, block=0, offset=10.0, invert=False, equalize=False,
                 min_arm=12.0, max_arm=400.0, angle_tol=6.0, min_rc=0.0, max_rc=0.0,
                 face_check=True, valley=False, spots=False, edge_band=0):
        self.ids = ids
        # spots=True (valley mode): the IR LEDs leave small fixed bright dots in the image (their
        # reflections). A line running through one is cut there and a dash under one is lost.
        # The dots are found about once a second (_find_spots); the line mask is bridged across
        # them, and an edge with a dot on it may count one dash more (_decode).
        self.spots = spots
        self.spot_every = 60            # frames between two dot searches
        self.spot_hat = 60              # a dot is this much brighter than around it (half scale)
        self.spot_zone = None           # 255 where a dot (grown) is, or None
        self._spot_boxes = []
        self._spot_age = 0
        # edge_band (valley mode): within this many px of the image's left / right border the
        # line test needs only 3/4 of `offset`. The lens edge blurs and dims lines there; a
        # fold arm along the border came out half missing (FIXLOG 13).
        self.edge_band = edge_band
        self.dash_outer = 0.95          # dashes searched out to this x rc (see _decode)
        self.dash_outer_edge = 0.95     # ... for a tile whose centre is within 1.5 rc of the
        #                                 left / right border (1.0 for the exhibit camera)
        self._width = 0
        self.edge_scale = 0.75
        # binarize(): valley=True takes a line to be a thin dark stripe with brighter pixels on
        # both sides (camera.xml <settings valley="true">), instead of "darker than the local
        # mean". For IR light, where the dark background next to a bright tile is darker than
        # the local mean too and its edge came out as a second hexagon of lines.
        self.valley = valley
        # binarize(): also require a line to be darker than the tile face. Made for a bright
        # table; under IR light the background is as dark as the lines and passes for the
        # "face", so no line survives: camera.xml <settings face_check="false"> turns it off
        self.face_check = face_check
        self.angle_tol = angle_tol      # fold must be 120 deg +- this
        self.min_rc = min_rc            # smallest accepted size (px); 0 = off
        self.max_rc = max_rc            # largest accepted size (px); 0 = off. A fold merged
        #                                 with the tile outline grows ~14%: dropped, not misread
        self.last_noise = 0             # folds dropped by the size filter in the last frame
        self.block = block
        self.offset = offset
        self.invert = invert
        self.equalize = equalize
        self.min_arm = min_arm
        self.max_arm = max_arm
        self.mask = None

    # 1 ------------------------------------------------------------- binarize
    def binarize(self, gray):
        if self.equalize:
            gray = cv2.equalizeHist(gray)
        if self.invert:
            gray = 255 - gray
        if self.valley:
            return self._valley(gray)
        # a small window keeps the dark halo that the table forms next to a bright tile thin,
        # so lines near the tile edge do not merge with it (tested: 15 px suits 90-160 px tiles)
        b = (self.block or 15) | 1
        # THRESH_BINARY_INV: 255 where pixel < local mean - offset  -> dark lines
        m = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                  cv2.THRESH_BINARY_INV, max(b, 3), self.offset)
        # drop isolated sensor specks (far fewer components to label): a line pixel needs at
        # least 2 dark neighbours. Unlike a blur or median it neither fills dash gaps nor
        # thins 2 px lines.
        # (on the 0/255 mask the 3x3 mean is 85 x the dark-pixel count / 3: >= 3 pixels <=> >= 80)
        nb = cv2.boxFilter(m, -1, (3, 3))
        _, keep = cv2.threshold(nb, 79, 255, cv2.THRESH_BINARY)
        m = cv2.bitwise_and(m, keep)
        # Next to the bright table the tile's own rim is darker than the local mean too, and at
        # a corner it bridged the fold's end to a dash (one component: no fold found). The rim
        # is as light as the tile face, a line is darker: keep only pixels darker than the
        # darkest local mean around them (the tile face) by FACE_MARGIN. Tested on the camera
        # frames, the stl_hexcell renders and the paper-marker snapshots: 0-4 keep every line
        # (8 and up start to drop some), 4 cuts every rim bridge seen so far.
        if not self.face_check:
            return m
        face = cv2.erode(cv2.blur(gray, (b, b)), np.ones((b, b), np.uint8))
        darker = cv2.compare(gray, cv2.subtract(face, FACE_MARGIN), cv2.CMP_LT)
        return cv2.bitwise_and(m, darker)

    def _valley(self, gray):
        """Line mask for IR light: a pixel is "line" when it is darker than the closing of the
        image (the brightness it would have were the thin dark stripe filled in) by `offset`
        percent. A 9 px window fills strokes up to 8 px wide; the dark background beside a tile
        is wider than that, stays unfilled and gives no line. A percentage rather than grey
        levels because the tiles are lit unevenly (faces measured 131 / 165 / 199 in one frame)."""
        closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, self._valley_kernel)
        depth = cv2.subtract(closed, gray)
        need = cv2.max(cv2.multiply(closed, self.offset / 100.0, dtype=cv2.CV_8U), 5)
        b = min(int(self.edge_band), gray.shape[1] // 2)
        if b > 0:
            pct = self.offset * self.edge_scale / 100.0
            for sl in (np.s_[:, :b], np.s_[:, -b:]):
                need[sl] = cv2.max(cv2.multiply(closed[sl], pct, dtype=cv2.CV_8U), 5)
        m = cv2.compare(depth, need, cv2.CMP_GT)
        nb = cv2.boxFilter(m, -1, (3, 3))                       # specks, as in binarize()
        _, keep = cv2.threshold(nb, 79, 255, cv2.THRESH_BINARY)
        m = cv2.bitwise_and(m, keep)
        if self.spots:
            if self.spot_zone is None or self.spot_zone.shape != gray.shape or self._spot_age <= 0:
                self._find_spots(gray)
                self._spot_age = self.spot_every
            self._spot_age -= 1
            m = cv2.bitwise_and(m, cv2.bitwise_not(self.spot_zone))   # bridged in process()
        return m

    _valley_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))

    # ------------------------------------------------------------ LED dots
    _spot_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))       # half scale
    _bridge_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))

    def _find_spots(self, gray):
        """Small, round, much brighter than their surroundings: the LED reflections (8-12 px
        across on the exhibit camera). Searched at half size for speed. A tile's bright rim is a
        thin line, not round, and is left out by the shape test."""
        small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        hat = cv2.subtract(small, cv2.morphologyEx(small, cv2.MORPH_OPEN, self._spot_kernel))
        n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.compare(hat, self.spot_hat, cv2.CMP_GT))
        w, h, a = st[:, 2], st[:, 3], st[:, 4]
        ok = ((a >= 2) & (a <= 60) & (np.maximum(w, h) <= 2.2 * np.minimum(w, h) + 1)
              & (a >= 0.4 * w * h))
        ok[0] = False
        dots = cv2.resize(ok[lab].astype(np.uint8) * 255, (gray.shape[1], gray.shape[0]),
                          interpolation=cv2.INTER_NEAREST)
        # grown by 3 px: the valley test leaves a dark-looking halo just around a bright dot
        zone = cv2.dilate(dots, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
        # one work box per group of dots (an LED shows as three in a row)
        n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(zone, np.ones((15, 15), np.uint8)))
        self.spot_zone = zone
        # the 2 px ring just outside the zones: a line piece that would continue under a dot
        # touches it
        self._spot_ring = cv2.subtract(cv2.dilate(zone, np.ones((5, 5), np.uint8)), zone)
        self._spot_boxes = [tuple(int(v) for v in st[i, :4]) for i in range(1, n)]
        ry, rx = np.nonzero(self._spot_ring)
        self._ring_idx = ry * gray.shape[1] + rx               # flat index of every ring pixel
        self._ring_box = lab[ry, rx] - 1                       # its work box

    def _bridge_spots(self, m, labels, stats):
        """Inside each dot zone the mask is unreliable (a line under the dot is too bright, the
        gap between neighbouring dots looks like a dark stripe), so binarize() cleared it. Here
        a line that enters one side of a dot and leaves the other in the same direction is drawn
        across: a fold arm or a dash running under the dot. Pieces that do not line up stay
        apart. Uses the frame's labelling; returns True when something was drawn."""
        H, W = m.shape
        drew = False
        # (box, label) of every line piece reaching a dot, in one pass over the ring pixels
        lab_r = labels.ravel()[self._ring_idx]
        sel = lab_r > 0
        if not sel.any():
            return False
        ext = np.maximum(stats[:, 2], stats[:, 3])
        stroke = (stats[:, 4] >= 6) & (ext >= 5) & (stats[:, 4] <= 0.5 * ext * ext)
        sel &= stroke[lab_r]
        pairs = np.unique(self._ring_box[sel].astype(np.int64) * (len(stats) + 1) + lab_r[sel])
        boxes, labs = pairs // (len(stats) + 1), pairs % (len(stats) + 1)
        for b in np.unique(boxes):
            ids = labs[boxes == b]
            if len(ids) < 2 or len(ids) > 12:                   # nothing to join, or clutter
                continue
            ends = []
            for i in ids:
                bx, by, bw, bh = stats[i, :4]
                mo = cv2.moments((labels[by:by + bh, bx:bx + bw] == i).view(np.uint8), binaryImage=True)
                a20, a02, a11 = mo["mu20"] / mo["m00"], mo["mu02"] / mo["m00"], mo["mu11"] / mo["m00"]
                com = math.sqrt(4 * a11 * a11 + (a20 - a02) ** 2)
                l1, l2 = (a20 + a02 + com) / 2, (a20 + a02 - com) / 2
                if l1 < 6.0 * max(l2, 0.05):                     # not a stroke
                    continue
                th = 0.5 * math.atan2(2 * a11, a20 - a02)
                u = np.array([math.cos(th), math.sin(th)])
                c = np.array([bx + mo["m10"] / mo["m00"], by + mo["m01"] / mo["m00"]])
                half = math.sqrt(3.0 * l1)                        # half length of a bar
                ends.append((c, u, c - half * u, c + half * u, max(1, int(round(math.sqrt(12.0 * max(l2, 0)))))))
            for i in range(len(ends)):
                for j in range(i + 1, len(ends)):
                    ci, ui, ai, bi, wi = ends[i]
                    cj, uj, aj, bj, wj = ends[j]
                    if abs(ui @ uj) < 0.97:                      # not the same direction
                        continue
                    if abs((cj - ci) @ np.array([-ui[1], ui[0]])) > 2.5:   # not on one line
                        continue
                    p, q = min(((a, b) for a in (ai, bi) for b in (aj, bj)),
                               key=lambda ab: np.linalg.norm(ab[0] - ab[1]))
                    if np.linalg.norm(p - q) > 26:
                        continue
                    mx, my = np.clip(np.round((p + q) / 2).astype(int), 0, [W - 1, H - 1])
                    if not self.spot_zone[my, mx]:               # the gap is not under a dot
                        continue
                    cv2.line(m, tuple(int(v) for v in np.round(p)), tuple(int(v) for v in np.round(q)),
                             255, max(wi, wj))
                    drew = True
        return drew

    # --------------------------------------------------------------- per frame
    def process(self, frame):
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self._width = gray.shape[1]
        mask = self.binarize(gray)
        self.mask = mask
        n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if self.spots and self.valley and self.spot_zone is not None                 and self._bridge_spots(mask, labels, stats):
            n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        st = stats[1:]
        keep = np.flatnonzero(st[:, 4] >= 4) + 1                # drop specks without a Python loop
        comps = {"i": keep, "x": stats[keep, 0], "y": stats[keep, 1], "w": stats[keep, 2],
                 "h": stats[keep, 3], "area": stats[keep, 4], "c": cents[keep]}
        diag = np.hypot(comps["w"], comps["h"])
        # an anchor is a thin line: its pixel count is small against its extent
        cand = np.flatnonzero((comps["area"] >= 20) & (diag >= self.min_arm * 1.4) & (diag <= self.max_arm * 2.2)
                              & (comps["area"] <= 0.2 * diag * diag))
        anchors = []
        boxes = np.column_stack([comps[k][cand] for k in ("i", "x", "y", "w", "h")]).tolist()
        for i, x, y, w, h in boxes:
            sub = labels[y:y + h, x:x + w] == i
            # two equal arms at 120 deg have a variance ratio (major / minor axis) of about 12;
            # a dash long enough to get here is above 100, a blob near 1: skip both early
            mo = cv2.moments(sub.view(np.uint8), binaryImage=True)
            a20, a02, a11 = mo["mu20"], mo["mu02"], mo["mu11"]
            common = math.sqrt(4 * a11 * a11 + (a20 - a02) ** 2)
            l1, l2 = a20 + a02 + common, a20 + a02 - common
            if not 4.0 * max(l2, 1e-6) < l1 < 40.0 * max(l2, 1e-6):
                continue
            pts = np.column_stack(np.nonzero(sub)[::-1]).astype(np.float64)
            pts += (x, y)
            a = self._anchor(pts)
            if a is not None:
                anchors.append(a)
        H, W = mask.shape[:2]
        # a tile whose centre is outside the frame cannot be read whole; it is never a marker
        anchors = [a for a in self._dedupe(anchors)
                   if 0 <= a["centre"][0] < W and 0 <= a["centre"][1] < H]
        if self.min_rc > 0 or self.max_rc > 0:      # outside the size range: noise
            kept = [a for a in anchors if a["rc"] >= self.min_rc
                    and (self.max_rc <= 0 or a["rc"] <= self.max_rc)]
            self.last_noise = len(anchors) - len(kept)
            anchors = kept
        else:
            self.last_noise = 0
        dash_cache = {}
        markers = []
        for a in anchors:
            m = self._decode(a, comps, labels, dash_cache)
            # A fold arm whose end faded (dim tile) is cut short: the mean of the two arms is then
            # too small, the centre lands off the tile's and the dashes fall outside their ring
            # (code 0000 on the camera). When the arms differ and the mean gives no id, the
            # longer arm alone is tried. A dropped dash never makes a valid id, so this cannot
            # turn one marker into another.
            if m.fid < 0 and a["rc_long"] > 1.04 * a["rc"]:
                b = dict(a, rc=a["rc_long"], centre=a["corner"] + a["rc_long"] * (a["ua"] + a["ub"]))
                if self.max_rc <= 0 or b["rc"] <= self.max_rc:
                    m2 = self._decode(b, comps, labels, dash_cache)
                    if m2.fid >= 0:
                        m = m2
            markers.append(m)
        return markers

    # 3 ------------------------------------------------------------- anchors
    def _anchor(self, pts):
        hull = cv2.convexHull(pts.astype(np.float32)).reshape(-1, 2).astype(np.float64)
        if len(hull) < 3:
            return None
        d = np.linalg.norm(hull[:, None, :] - hull[None, :, :], axis=2)
        ia, ib = np.unravel_index(np.argmax(d), d.shape)
        A, B = hull[ia], hull[ib]
        ab = B - A
        L = np.linalg.norm(ab)
        if L < 1e-6:
            return None
        nrm = np.array([-ab[1], ab[0]]) / L
        dist = np.abs((pts - A) @ nrm)
        C = pts[np.argmax(dist)]
        ca, cb = np.linalg.norm(A - C), np.linalg.norm(B - C)
        if min(ca, cb) < self.min_arm or max(ca, cb) > self.max_arm:
            return None
        if not 0.75 < ca / cb < 1.33:
            return None
        cosang = np.dot(A - C, B - C) / (ca * cb)
        if not -0.72 < cosang < -0.25:                 # ~105..136 deg, nominal 120
            return None
        arm = 0.5 * (ca + cb)
        thick = len(pts) / (ca + cb)
        if thick > 0.22 * arm:
            return None
        # split pixels by arm, fit each arm as a line (drop the corner blob)
        da = _seg_dist(pts, C, A)
        db = _seg_dist(pts, C, B)
        near_c = np.linalg.norm(pts - C, axis=1) < 0.2 * arm
        ga = pts[(da <= db) & ~near_c]
        gb = pts[(db < da) & ~near_c]
        if len(ga) < 5 or len(gb) < 5:
            return None
        if max(np.mean(np.minimum(da, db)), 0) > 0.12 * arm + 1.5:
            return None
        pa, ua = _line_fit(ga)
        pb, ub = _line_fit(gb)
        if np.dot(ua, A - C) < 0:
            ua = -ua
        if np.dot(ub, B - C) < 0:
            ub = -ub
        # strict 120 deg check on the fitted arms (real folds measure 119-120.2 deg on camera;
        # look-alike blobs measured 99, 155, 172 deg)
        arm_angle = math.degrees(math.acos(float(np.clip(ua @ ub, -1.0, 1.0))))
        if abs(arm_angle - 120.0) > self.angle_tol:
            return None
        corner = _intersect(pa, ua, pb, ub)
        if corner is None or np.linalg.norm(corner - C) > 0.25 * arm:
            corner = C
        ra, rb = np.max((ga - corner) @ ua), np.max((gb - corner) @ ub)
        rc = 0.5 * (ra + rb)
        centre = corner + rc * (ua + ub)
        return {"corner": corner, "centre": centre, "rc": rc, "rc_long": max(ra, rb), "ua": ua, "ub": ub,
                "len_a": len(ga), "len_b": len(gb), "pts": (A, C, B), "arm_angle": arm_angle}

    def _spot_code(self, code, O, rc, corner_dir):
        """An edge with a dot on it may hide one dash (or a dot merged two): try one more dash
        on such edges. Accepted only when exactly one of the tries is in the id table."""
        H, W = self.spot_zone.shape
        x0, y0 = max(0, int(O[0] - rc)), max(0, int(O[1] - rc))
        if not cv2.countNonZero(self.spot_zone[y0:min(H, int(O[1] + rc) + 1), x0:min(W, int(O[0] + rc) + 1)]):
            return code, -1
        edges = _spot_edges(self.spot_zone, O, rc, corner_dir)
        if not edges:
            return code, -1
        idx = [k for k, _ in SECTORS]
        found = set()
        for mask in range(1, 1 << len(edges)):
            c = list(code)
            for j, k in enumerate(edges):
                if mask >> j & 1:
                    c[idx.index(k)] += 1
            if tuple(c) in self.ids:
                found.add(tuple(c))
        if len(found) == 1:
            c = found.pop()
            return c, self.ids[c]
        return code, -1

    def _dedupe(self, anchors):
        out = []
        for a in sorted(anchors, key=lambda a: -a["rc"]):
            if all(np.linalg.norm(a["centre"] - b["centre"]) > 0.5 * b["rc"] for b in out):
                out.append(a)
        return out

    # 4-6 ---------------------------------------------------------- decode one tile
    def _decode(self, a, comps, labels, cache):
        O, rc = a["centre"], a["rc"]
        dashes = []
        r_all = np.linalg.norm(comps["c"] - O, axis=1)
        # dashes sit on the same inset ring as the anchor: 0.866 rc at an edge's middle, up to
        # ~0.90 rc for the outer dash of three (printed tiles of stl_hexcell: 0.839-0.888 rc).
        # Near the image edge the wide-angle lens pushes the dashes far from the fold outward:
        # up to 0.943 rc measured at the right edge, hence 0.95. The 3D tile's rim (0.938 rc at
        # an edge's middle) would fit in too, but binarize() keeps only pixels darker than the
        # tile face, and the rim is not.
        # At the image's left / right edge the lens squeezes the tile: the fold comes out ~5%
        # short and the dashes far from it reach 0.97 rc (FIXLOG 13). The ring is widened there
        # only; elsewhere 1.0 picked up stray pixels and lost frames.
        r_hi = self.dash_outer
        if self._width and min(O[0], self._width - 1 - O[0]) < 1.5 * rc:
            r_hi = max(r_hi, self.dash_outer_edge)
        near = np.flatnonzero((r_all > 0.78 * rc) & (r_all < r_hi * rc)
                              & (np.maximum(comps["w"], comps["h"]) <= 0.95 * rc)
                              & (comps["area"] <= 0.25 * rc * rc))
        for j in near:
            i, x, y, w, h = (int(comps[k][j]) for k in ("i", "x", "y", "w", "h"))
            cen = comps["c"][j]
            v = cen - O
            if i not in cache:
                mo = cv2.moments((labels[y:y + h, x:x + w] == i).astype(np.uint8), binaryImage=True)
                n0 = mo["m00"]
                if n0 < 3:
                    cache[i] = None
                    continue
                a20, a02, a11 = mo["mu20"] / n0, mo["mu02"] / n0, mo["mu11"] / n0
                common = math.sqrt(4 * a11 * a11 + (a20 - a02) ** 2)
                l1, l2 = (a20 + a02 + common) / 2, (a20 + a02 - common) / 2
                length = math.sqrt(12.0 * l1)                     # major-axis length of a bar
                width = math.sqrt(12.0 * max(l2, 0.0))
                # a thick, short dash (three per edge, wide stroke) is too stubby for a reliable
                # axis: it is still counted by where it sits, but it does not steer the angle
                elongated = l1 >= 3.0 * max(l2, 0.08)
                cache[i] = (math.degrees(0.5 * math.atan2(2 * a11, a20 - a02)), n0, length, width, elongated)
            if cache[i] is None:
                continue
            psi, n, length, width, elongated = cache[i]
            if length < 0.1 * rc or width > 0.16 * rc:   # too short, or a blob wider than any stroke
                continue
            phi = math.degrees(math.atan2(v[1], v[0]))
            # a dash lies along its edge: axis perpendicular to the radius
            if elongated and abs(((psi - phi) % 180.0) - 90.0) > 25.0:
                continue
            dashes.append((cen, psi if elongated else None, n, phi))
        # 5: edge orientation from arms + dashes (tangents repeat every 60 deg)
        z = 0j
        for u, w in ((a["ua"], a["len_a"]), (a["ub"], a["len_b"])):
            t = math.atan2(u[1], u[0])
            z += w * complex(math.cos(6 * t), math.sin(6 * t))
        for _, psi, n, _ in dashes:
            if psi is None:
                continue
            t = math.radians(psi)
            z += n * complex(math.cos(6 * t), math.sin(6 * t))
        tangent = math.degrees(math.atan2(z.imag, z.real)) / 6.0
        normal = tangent + 90.0
        coarse = math.degrees(math.atan2(a["corner"][1] - O[1], a["corner"][0] - O[0]))
        corner_dir = min((normal + 30.0 + 60.0 * k for k in range(6)),
                         key=lambda c: abs(wrap180(c - coarse)))
        # 6: count dashes per id edge
        counts = {k: 0 for k, _ in SECTORS}
        kept = []
        for cen, psi, n, phi in dashes:
            rel = wrap180(phi - corner_dir)
            best = min(SECTORS, key=lambda s: abs(wrap180(rel - s[1])))
            if abs(wrap180(rel - best[1])) <= 24.0:
                counts[best[0]] += 1
                kept.append(cen)
        code = tuple(counts[k] for k, _ in SECTORS)
        fid = self.ids.get(code, -1)
        if fid < 0 and self.spots and self.spot_zone is not None:
            code, fid = self._spot_code(code, O, rc, corner_dir)
        angle = (corner_dir - CORNER_DEG) % 360.0
        return Marker(fid, code, float(O[0]), float(O[1]), angle, float(rc),
                      a["arm_angle"], corner_dir, tuple(a["corner"]), a["pts"], kept)


_EDGE_R, _EDGE_S = (a.ravel() for a in np.meshgrid([0.84, 0.87, 0.90], np.linspace(-0.45, 0.45, 19)))


def _spot_edges(zone, O, rc, corner_dir):
    """Which id edges have a dot zone on their dash line (0.84-0.90 rc, middle 90% of the edge)."""
    H, W = zone.shape
    hit = []
    for k, rel in SECTORS:
        n = math.radians(corner_dir + rel)
        u, t = np.array([math.cos(n), math.sin(n)]), np.array([-math.sin(n), math.cos(n)])
        p = O + rc * (_EDGE_R[:, None] * u + _EDGE_S[:, None] * t)
        xi, yi = np.round(p[:, 0]).astype(int), np.round(p[:, 1]).astype(int)
        ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
        if zone[yi[ok], xi[ok]].any():
            hit.append(k)
    return hit


def _seg_dist(p, a, b):
    ab = b - a
    t = np.clip(((p - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=1)


def _line_fit(p):
    m = p.mean(0)
    _, _, vt = np.linalg.svd(p - m, full_matrices=False)
    return m, vt[0]


def _intersect(p1, u1, p2, u2):
    M = np.array([u1, -u2]).T
    if abs(np.linalg.det(M)) < 1e-6:
        return None
    t = np.linalg.solve(M, p2 - p1)
    return p1 + t[0] * u1
