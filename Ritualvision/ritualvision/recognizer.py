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
                 face_check=True, valley=False):
        self.ids = ids
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
        m = cv2.compare(depth, need, cv2.CMP_GT)
        nb = cv2.boxFilter(m, -1, (3, 3))                       # specks, as in binarize()
        _, keep = cv2.threshold(nb, 79, 255, cv2.THRESH_BINARY)
        return cv2.bitwise_and(m, keep)

    _valley_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))

    # --------------------------------------------------------------- per frame
    def process(self, frame):
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mask = self.binarize(gray)
        self.mask = mask
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
        near = np.flatnonzero((r_all > 0.78 * rc) & (r_all < 0.95 * rc)
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
        angle = (corner_dir - CORNER_DEG) % 360.0
        return Marker(fid, code, float(O[0]), float(O[1]), angle, float(rc),
                      a["arm_angle"], corner_dir, tuple(a["corner"]), a["pts"], kept)


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
