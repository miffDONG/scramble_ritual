"""
Morse-style marker reader for Scramble Ritual.

The physical rule (matched to the real hand-drawn cards):

    start square -> exactly N dot/dash symbols along one line

There is NO end marker. The reader anchors on each start square, grows a
collinear chain of data glyphs away from it, and reads the first N glyphs
in order. The chain direction (square -> symbols) disambiguates reading
orientation, and the fitted chain line doubles as the object's reference
axis (tilt / spacing measurements downstream).

Everything that is not a square-anchored chain of N glyphs is treated as
noise: bright content (the white engraved waveform) never survives the
dark threshold, mid-gray shadows are dropped by the contrast gate, and
stray dark specks fail the chain geometry gates.
"""

from dataclasses import dataclass, field
import math

import cv2
import numpy as np


DEFAULTS = {
    "slots": 8,
    "code_count": 20,
    "min_hamming": 3,
    "correction_distance": 1,
    "decode_mode": "codebook",    # codebook or binary
    "threshold": 0,               # >0 forces a fixed threshold (back-compat)
    "threshold_mode": "percentile",  # percentile | otsu | fixed | adaptive
    "threshold_percentile": 2.0,
    "threshold_clamp": [40, 120],
    "adaptive_block_px": 0,       # 0 = auto (min dim / 12)
    "adaptive_c": 12,             # adaptive offset: lower catches fainter marks
    "dark_on_light": True,        # black pen marks on bright material
    "normalize_illumination": False,  # flat-field divide for uneven backlight
    "normalize_kernel_frac": 0.25,    # background blur size as frac of min dim
    # Optional contrast lab.  The web server enables these only for real
    # camera/video/image sources; synthetic sources stay as rendered.
    "preprocess_enabled": False,
    "autocontrast_enabled": False,
    "autocontrast_low_pct": 1.0,
    "autocontrast_high_pct": 99.0,
    "gamma_enabled": False,
    "gamma_value": 1.0,
    "bilateral_enabled": False,
    "bilateral_d": 5,
    "bilateral_sigma": 22.0,
    "clahe_enabled": False,
    "clahe_clip_limit": 2.0,
    "clahe_tile_grid": 12,
    "unsharp_enabled": False,
    "unsharp_amount": 0.6,
    "blur_px": 3,
    "morph_open_px": 1,
    "morph_close_px": 2,
    # Glyph extraction/classification strategy. Contour is the general
    # geometry path; Hough and morphology create explicit line/dot maps.
    "extraction_mode": "contour",  # contour | hough | morphology
    "method_line_score_min": 0.22,
    "hough_threshold": 10,
    "hough_min_line_length": 6,
    "hough_max_line_gap": 3,
    "hough_line_thickness": 3,
    "morph_line_length": 9,
    "morph_line_thickness": 1,
    "min_glyph_area_px": 45,
    "min_glyph_area_frac": 2.0e-5,   # scales the area floor with resolution
    "max_glyph_area_frac": 0.03,
    "max_glyph_area_px": 0,       # absolute area cap in px (0 = off)
    "min_blob_contrast": 50,      # median(gray) - blob mean must exceed this
    # shape-quality gates (scale-invariant): the primary noise filter now
    # that camera distance / mark px size are not fixed. A real filled mark
    # (dot/dash/square/circle) is convex and fills its box; finger edges and
    # wiggly streaks are not. Star anchors are concave (low solidity) -> if a
    # run uses a star start marker, lower min_solidity accordingly.
    "min_solidity": 0.7,          # area / convexHull area
    "min_rectangularity": 0.4,    # area / minAreaRect area (rotation-invariant)
    # shape classifier: label each glyph as one of the 7 physical marks by
    # scale-invariant descriptors, instead of "compact vs elongated" only.
    # circle -> dot(0), line -> dash(1); square/triangle/pentagon/hexagon/star
    # -> start anchor. Lets the start marker be found by SHAPE (not by being
    # bigger), so the 26.07.20 cards (anchor ~ same size as dots) decode.
    "shape_classify": False,
    "star_solidity_max": 0.72,      # below this (concave) -> star
    "triangle_max_vertices": 3,     # <= this many corners -> triangle (4 -> square)
    "pentagon_max_vertices": 5,     # 5 -> pentagon; 6..(circle_min-1) -> hexagon
    "circle_min_vertices": 8,       # >= this many corners -> circle
    "circle_min_circularity": 0.88,  # 6-7 corners but rounder than this -> circle
                                     # (guards a small circle that rounds to 7)
    # template matching: instead of the brittle threshold tree above, capture
    # one example of each real marker and label every blob by its nearest
    # template. Robust to shapes the thresholds mis-order (star/triangle with
    # aspect just over dash_aspect_min). Empty -> fall back to the tree.
    "shape_templates": [],          # [{"label": str, "v": [5 features]}, ...]
    "template_max_dist": 0.35,      # no template within this -> noise (dropped)
    "require_start": True,        # False: read the blob chain with no anchor
    "square_min_extent": 0.5,     # loose: chain gates do the real validation
    "square_aspect_max": 1.9,     # hand squares merge with edge shadows a bit
    "square_max_vertices": 6,     # raise to ~12 for star (*) anchor variants
    "compact_aspect_max": 1.55,
    "dash_aspect_min": 1.85,      # global minAreaRect fallback for dashes
    "dash_ratio_min": 1.7,        # along/across projection ratio on the axis
    "square_area_ratio": 1.8,     # square must be this much bigger than chain glyphs
    "chain_perp_tol": 1.4,        # x sqrt(median glyph area) perpendicular band
    "chain_gap_ratio_max": 2.4,   # max gap / median gap (splits collinear cards)
    "chain_first_gap_ratio_max": 3.0,
    "chain_neighbors": 12,        # direction hypotheses per square
    "max_cards": 20,              # max cards read per frame (table capacity)
    "allow_end_circle": False,    # legacy end-circle consumption hook
    "end_min_circularity": 0.68,
}


# Verified 8-bit codebook: 20 codewords, pairwise Hamming distance >= 3.
# IDs 1 and 2 are the two hand-drawn reference cards
# (sampleVideo/IMG_0343.JPG and IMG_0344.JPG).
_CODEBOOK_8_20 = [
    "00110110", "01001101", "00000000", "00000111", "10001110",
    "10010101", "00011011", "00101100", "10101001", "11100100",
    "01100011", "10100010", "00110001", "01111000", "11000001",
    "10011000", "11010010", "01001010", "01010100", "10111111",
]


@dataclass
class Glyph:
    kind: str
    cx: float
    cy: float
    area: float
    bbox: tuple
    contour: np.ndarray = field(repr=False)
    circularity: float = 0.0
    extent: float = 0.0
    vertices: int = 0
    aspect: float = 1.0
    rect_angle: float = 0.0   # long-axis direction of minAreaRect, degrees
    solidity: float = 1.0     # area / convex-hull area
    rectangularity: float = 1.0   # area / minAreaRect area
    shape: str = ""           # circle/line/square/triangle/star (shape_classify)
    line_score: float = 0.0    # overlap with Hough/morphology line-only map


@dataclass
class SlotRead:
    index: int
    bit: str
    cx: float
    cy: float
    confidence: float
    glyph: Glyph | None = field(default=None, repr=False)


@dataclass
class MorseCard:
    cx: float
    cy: float
    angle: float
    bits: str
    code_id: int | None
    raw_id: int | None
    status: str
    score: float
    start: Glyph = field(repr=False)
    end: Glyph = field(repr=False)   # last data glyph (no physical end marker)
    slots: list = field(default_factory=list)
    flip: bool = False               # reversed orientation (sim ground truth)
    pitch: float | None = None       # per-object semitone override (sim)

    @property
    def label(self):
        if self.code_id is None:
            return f"? {self.bits}"
        return f"ID{self.code_id:02d}"


def cfg_with_defaults(cfg=None):
    out = dict(DEFAULTS)
    if cfg:
        out.update(cfg)
    return out


def hamming(a, b):
    return sum(x != y for x, y in zip(a, b))


def generate_codebook(count=20, bits=8, min_distance=3):
    """Return deterministic 1-based ID -> bit-string codebook."""
    if bits == 8 and min_distance <= 3 and count <= len(_CODEBOOK_8_20):
        # The greedy below tops out at 16 codes for 8 bits / distance 3;
        # this pre-verified table reaches the theoretical maximum of 20
        # and pins the two reference cards to IDs 1 and 2.
        return {i + 1: code for i, code in enumerate(_CODEBOOK_8_20[:count])}
    all_codes = [format(i, f"0{bits}b") for i in range(1, 1 << bits)]
    target_weight = bits / 2.0
    all_codes.sort(key=lambda s: (abs(s.count("1") - target_weight), s))
    picked = []
    for code in all_codes:
        if all(hamming(code, prev) >= min_distance for prev in picked):
            picked.append(code)
            if len(picked) == count:
                break
    if len(picked) < count:
        raise ValueError("could not build codebook with requested spacing")
    return {i + 1: code for i, code in enumerate(picked)}


def decode_bits(bits, codebook=None, correction_distance=1):
    """Decode a complete bit string using exact match, then 1-bit correction."""
    if "?" in bits:
        return None, None, "unknown"
    raw_id = int(bits, 2) + 1
    if codebook is None:
        return raw_id, raw_id, "binary"
    exact = {v: k for k, v in codebook.items()}
    if bits in exact:
        return exact[bits], raw_id, "exact"
    ranked = sorted((hamming(bits, code), cid) for cid, code in codebook.items())
    if ranked and ranked[0][0] <= correction_distance:
        if len(ranked) == 1 or ranked[1][0] > ranked[0][0]:
            return ranked[0][1], raw_id, "corrected"
    return None, raw_id, "unknown"


def prepare_gray(frame, cfg=None):
    """Grayscale + blur + optional flat-field normalization. The same
    image feeds both the threshold and the blob-contrast gate, so the
    backlit-plate correction applies consistently."""
    c = cfg_with_defaults(cfg)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    if c.get("preprocess_enabled"):
        if c.get("autocontrast_enabled"):
            lo = float(c.get("autocontrast_low_pct", 1.0))
            hi = float(c.get("autocontrast_high_pct", 99.0))
            a, z = np.percentile(gray[::2, ::2], (lo, hi))
            if z > a + 1:
                gray = np.clip((gray.astype(np.float32) - a) * 255.0 / (z - a),
                               0, 255).astype(np.uint8)
        if c.get("gamma_enabled"):
            gamma = max(0.05, float(c.get("gamma_value", 1.0)))
            lut = np.array([((i / 255.0) ** gamma) * 255.0
                            for i in range(256)], dtype=np.uint8)
            gray = cv2.LUT(gray, lut)
        if c.get("bilateral_enabled"):
            d = max(1, int(c.get("bilateral_d", 5)))
            sigma = max(1.0, float(c.get("bilateral_sigma", 22.0)))
            gray = cv2.bilateralFilter(gray, d, sigma, sigma)
        if c.get("clahe_enabled"):
            tile = max(2, int(c.get("clahe_tile_grid", 12)))
            clip = max(0.1, float(c.get("clahe_clip_limit", 2.0)))
            gray = cv2.createCLAHE(clipLimit=clip,
                                   tileGridSize=(tile, tile)).apply(gray)
        if c.get("unsharp_enabled"):
            amount = max(0.0, float(c.get("unsharp_amount", 0.6)))
            soft = cv2.GaussianBlur(gray, (0, 0), 1.2)
            gray = cv2.addWeighted(gray, 1.0 + amount, soft, -amount, 0)

    b = int(c["blur_px"]) | 1
    if b > 1:
        gray = cv2.GaussianBlur(gray, (b, b), 0)
    if c.get("normalize_illumination"):
        # flat-field: divide by a heavy blur of the frame so the (uneven)
        # backlight maps to a constant ~180 and marks keep their contrast
        frac = float(c.get("normalize_kernel_frac", 0.25))
        k = max(31, int(min(gray.shape) * frac) | 1)
        bg = cv2.GaussianBlur(gray, (k, k), 0)
        gray = cv2.divide(gray, cv2.max(bg, 1), scale=180)
    return gray


#: morphology probes the mask with a line kernel at these four angles
MORPH_ANGLES = (0, 45, 90, 135)


def morph_line_kernel(angle_deg, length, thickness):
    """One directional line kernel — the exact structuring element the
    morphology method opens the mask with. The diagnostics view draws this
    same array, so the picture shows the real kernel, not a redrawn guess."""
    k = np.zeros((length, length), np.uint8)
    mid = radius = length // 2
    a = math.radians(angle_deg)
    dx, dy = round(math.cos(a) * radius), round(math.sin(a) * radius)
    cv2.line(k, (mid - dx, mid - dy), (mid + dx, mid + dy), 1,
             thickness, cv2.LINE_8)
    return k


def extraction_detail(mask, cfg=None):
    """Everything the chosen extraction method produced — not just its masks.

    The three methods reach the same line/dot split by very different means,
    and a method can only be judged by what it actually did to THIS frame:
    which segments Hough voted for, which of the four morphology orientations
    fired, or — for contour — that no pixel line map exists at all and the
    split happens per blob. Those intermediates used to be discarded inside
    this function, so every method ended up shown as the same rasterized
    union and switching methods changed nothing visible. They are kept here:

        mode          chosen method
        mask          candidate mask (unchanged threshold result)
        line_mask     pixels the method calls LINE
        dot_mask      candidate minus (dilated) line map
        segments      [(x1, y1, x2, y2), ...] Hough voted for — hough only
        edges         the Canny image Hough voted on, or None
        orientations  {angle_deg: opened_mask} — morphology only
        kernels       {angle_deg: kernel}      — morphology only
    """
    c = cfg_with_defaults(cfg)
    mode = c.get("extraction_mode", "contour")
    line_mask = np.zeros_like(mask)
    detail = {"mode": mode, "mask": mask, "segments": [], "edges": None,
              "orientations": {}, "kernels": {}, "line_thickness": 0,
              "kernel_length": 0, "kernel_thickness": 0}

    if mode == "hough":
        edges = cv2.Canny(mask, 50, 150, apertureSize=3)
        detail["edges"] = edges
        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 180.0,
            threshold=max(1, int(c.get("hough_threshold", 10))),
            minLineLength=max(2, int(c.get("hough_min_line_length", 6))),
            maxLineGap=max(0, int(c.get("hough_max_line_gap", 3))))
        thick = max(1, int(c.get("hough_line_thickness", 3)))
        detail["line_thickness"] = thick
        if lines is not None:
            for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
                detail["segments"].append((int(x1), int(y1), int(x2), int(y2)))
                cv2.line(line_mask, (x1, y1), (x2, y2), 255, thick,
                         cv2.LINE_AA)
            line_mask = cv2.bitwise_and(line_mask, mask)
    elif mode == "morphology":
        length = max(3, int(c.get("morph_line_length", 9)))
        thick = max(1, int(c.get("morph_line_thickness", 1)))
        detail["kernel_length"], detail["kernel_thickness"] = length, thick
        # Four orientations make the operation rotation-tolerant while still
        # rejecting compact dots. Kernels are intentionally small for the
        # installation's low-pixel glyphs.
        for angle in MORPH_ANGLES:
            kernel = morph_line_kernel(angle, length, thick)
            opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            detail["kernels"][angle] = kernel
            detail["orientations"][angle] = opened
            line_mask = cv2.bitwise_or(line_mask, opened)

    dot_mask = cv2.subtract(mask, cv2.dilate(
        line_mask, np.ones((3, 3), np.uint8)))
    detail["line_mask"], detail["dot_mask"] = line_mask, dot_mask
    return detail


def extraction_maps(mask, cfg=None):
    """Return (candidate_mask, line_mask, dot_mask) for visual diagnosis.

    candidate_mask remains the original threshold result so tiny components
    are never silently discarded.  The two derived masks influence dot/dash
    classification and are exposed by the web tuner for pixel-level review.
    extraction_detail() carries the same masks plus the method's own
    intermediates (Hough segments / morphology orientations).
    """
    d = extraction_detail(mask, cfg)
    return d["mask"], d["line_mask"], d["dot_mask"]


def preprocess(frame, cfg=None, gray=None):
    c = cfg_with_defaults(cfg)
    if gray is None:
        gray = prepare_gray(frame, c)
    thresh_type = cv2.THRESH_BINARY_INV if c["dark_on_light"] else cv2.THRESH_BINARY

    mode = c.get("threshold_mode", "percentile")
    if c.get("threshold", 0) > 0:
        mode = "fixed"
    if mode == "fixed":
        _, mask = cv2.threshold(gray, c["threshold"], 255, thresh_type)
    elif mode == "otsu":
        _, mask = cv2.threshold(gray, 0, 255, thresh_type + cv2.THRESH_OTSU)
    elif mode == "adaptive":
        block = int(c.get("adaptive_block_px", 0))
        if block <= 0:
            block = min(gray.shape) // 12
        block = max(31, block | 1)
        cc = float(c.get("adaptive_c", 12))
        if not c["dark_on_light"]:
            cc = -cc  # bright marks must beat the local mean upward
        mask = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, thresh_type, block, cc)
    else:  # percentile: marks are a small tail of the histogram, dark or
        # bright depending on polarity
        lo, hi = c.get("threshold_clamp", [40, 120])
        p = float(c.get("threshold_percentile", 2.0))
        if c["dark_on_light"]:
            t = float(np.percentile(gray[::4, ::4], p))
            t = max(float(lo), min(float(hi), t))
        else:
            t = float(np.percentile(gray[::4, ::4], 100.0 - p))
            t = max(255.0 - float(hi), min(255.0 - float(lo), t))
        _, mask = cv2.threshold(gray, t, 255, thresh_type)

    if c["morph_open_px"] > 1:
        ko = np.ones((c["morph_open_px"], c["morph_open_px"]), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, ko)
    if c["morph_close_px"] > 1:
        kc = np.ones((c["morph_close_px"], c["morph_close_px"]), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kc)
    return mask


def find_glyphs(mask, cfg=None, gray=None, line_mask=None):
    """Extract candidate glyphs. Kinds are provisional: 'start' square
    candidates, 'dash' / 'dot' data candidates. Final dot/dash decisions
    happen per-chain once the card axis is known."""
    c = cfg_with_defaults(cfg)
    frame_area = mask.shape[0] * mask.shape[1]
    min_area = max(c["min_glyph_area_px"], c["min_glyph_area_frac"] * frame_area)
    # absolute px cap takes precedence when set (>0); else the frame-fraction.
    # A fixed installation that only zooms out keeps marker px bounded, so an
    # absolute max is stable there (tied to the processing resolution).
    if c.get("max_glyph_area_px", 0) > 0:
        max_area = float(c["max_glyph_area_px"])
    else:
        max_area = frame_area * c["max_glyph_area_frac"]
    median_gray = float(np.median(gray[::8, ::8])) if gray is not None else None

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    raw = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < min_area or area > max_area:
            continue
        m = cv2.moments(cnt)
        if m["m00"] <= 1e-6:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        line_score = 0.0
        if line_mask is not None and cv2.countNonZero(line_mask) > 0:
            blob = np.zeros((h, w), np.uint8)
            cv2.drawContours(blob, [cnt - [x, y]], -1, 255, cv2.FILLED)
            overlap = cv2.bitwise_and(blob, line_mask[y:y + h, x:x + w])
            line_score = cv2.countNonZero(overlap) / max(cv2.countNonZero(blob), 1)
        if median_gray is not None and c["min_blob_contrast"] > 0:
            sub_gray = gray[y:y + h, x:x + w]
            sub_mask = mask[y:y + h, x:x + w]
            blob_mean = cv2.mean(sub_gray, sub_mask)[0]
            contrast = (median_gray - blob_mean if c["dark_on_light"]
                        else blob_mean - median_gray)
            if contrast < c["min_blob_contrast"]:
                continue  # mid-gray shadow / faint smudge, not a mark
        (_, _), (rw, rh), rang = cv2.minAreaRect(cnt)
        major, minor = max(rw, rh), max(min(rw, rh), 1e-6)
        rect_angle = rang if rw >= rh else rang + 90.0
        # shape-quality gate (scale-invariant): a real filled mark is convex
        # and fills its rotated box; finger edges / wiggly streaks are not.
        # This is the primary noise filter, so the area floor can stay tiny.
        rectangularity = area / max(rw * rh, 1e-6)
        hull_area = cv2.contourArea(cv2.convexHull(cnt))
        solidity = area / hull_area if hull_area > 1e-6 else 0.0
        # under shape_classify a compact concave/low-fill blob may be a valid
        # star or triangle, so the noise gate applies only to elongated blobs
        # (finger streaks); the shape classifier + chain geometry handle rest.
        lenient = c.get("shape_classify") and (major / minor) < c["dash_aspect_min"]
        if not lenient and (solidity < c["min_solidity"]
                            or rectangularity < c["min_rectangularity"]):
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.04 * peri, True) if peri else cnt
        circularity = 4.0 * math.pi * area / (peri * peri) if peri > 1e-6 else 0.0
        extent = area / max(float(w * h), 1.0)
        raw.append(Glyph(
            kind="unknown",
            cx=m["m10"] / m["m00"],
            cy=m["m01"] / m["m00"],
            area=area,
            bbox=(x, y, w, h),
            contour=cnt,
            circularity=circularity,
            extent=extent,
            vertices=len(approx),
            aspect=major / minor,
            rect_angle=rect_angle,
            solidity=solidity,
            rectangularity=rectangularity,
            line_score=line_score,
        ))

    if c.get("shape_classify"):
        # descriptor-based: identify all 7 physical marks by shape, so the
        # start anchor is found by its shape (square/triangle/pentagon/
        # hexagon/star) rather than by being bigger than the dots.
        templates = c.get("shape_templates") or None
        kept = []
        for g in raw:
            if templates:
                lab = classify_by_templates(g, templates, c["template_max_dist"])
                if not lab:
                    continue  # matches no saved marker -> noise, drop it
                g.shape = lab
            else:
                g.shape = classify_shape(g, c)
            g.kind = {"circle": "dot", "line": "dash"}.get(g.shape, "start")
            kept.append(g)
        return kept

    compact_areas = [g.area for g in raw if g.aspect <= c["compact_aspect_max"]]
    dot_area = float(np.median(compact_areas)) if compact_areas else min_area
    square_area = max(min_area, dot_area * c["square_area_ratio"])

    for g in raw:
        # square candidacy is deliberately loose (hand-drawn squares merge
        # with plate-edge shadows); the chain gates reject false anchors
        if (g.aspect <= c["square_aspect_max"] and g.area >= square_area
                and g.vertices <= c["square_max_vertices"]
                and g.extent >= c["square_min_extent"]):
            g.kind = "start"
        elif g.aspect >= c["dash_aspect_min"]:
            g.kind = "dash"
        elif g.aspect <= c["compact_aspect_max"]:
            g.kind = "dot"
    return raw


def classify_shape(g, cfg):
    """Label a glyph as one of the 7 physical marks by scale-invariant
    descriptors. Order: elongation -> concavity(star) -> corner count.
    Corner count (approxPolyDP vertices at 0.04*perimeter) is stable across
    blur/scale: triangle=3, square=4, pentagon=5, hexagon=6, circle~8+.
    Pentagon/hexagon are the only convex marks in the 5..7 vertex band, so
    they slot cleanly between square and circle; templates (shape_templates)
    are the robust fallback when a corner count wobbles by one."""
    c = cfg_with_defaults(cfg)
    if g.aspect >= c["dash_aspect_min"]:
        return "line"
    if g.solidity < c["star_solidity_max"]:
        return "star"           # concave points -> low solidity
    v = g.vertices
    if v <= c["triangle_max_vertices"]:
        return "triangle"       # ~3 corners
    if v <= 4:
        return "square"         # 4 corners
    if v <= c["pentagon_max_vertices"]:
        return "pentagon"       # 5 corners
    if v < c["circle_min_vertices"]:
        # 6-7 corners = hexagon, UNLESS the blob is too round to be a hexagon
        # (a small circle sometimes rounds to 7 corners): circularity splits
        # them cleanly — hexagon <=~0.85, circle >=~0.90.
        if g.circularity >= c["circle_min_circularity"]:
            return "circle"
        return "hexagon"
    return "circle"             # >=8 corners (rounded polygon fit)


def shape_features(g):
    """Scale/rotation-invariant descriptor vector for template matching,
    each component ~0..1 so plain Euclidean distance is meaningful."""
    return [
        float(g.circularity),
        float(g.solidity),
        float(g.rectangularity),
        min(g.vertices, 12) / 12.0,
        min(g.aspect, 6.0) / 6.0,
    ]


def classify_by_templates(g, templates, max_dist):
    """Return the label of the nearest saved template, or None if none is
    within max_dist (treated as noise)."""
    v = shape_features(g)
    best_label, best_d = None, float("inf")
    for t in templates:
        tv = t.get("v", [])
        if len(tv) != len(v):
            continue
        d = math.sqrt(sum((a - b) ** 2 for a, b in zip(v, tv)))
        if d < best_d:
            best_d, best_label = d, t.get("label")
    return best_label if best_d <= max_dist else None


def _fit_direction(points, ref_dir):
    """Principal direction of Nx2 points, sign-aligned with ref_dir."""
    pts = np.asarray(points, np.float64)
    d = pts - pts.mean(axis=0)
    cov = d.T @ d
    _, evecs = np.linalg.eigh(cov)
    u = evecs[:, -1]
    if u[0] * ref_dir[0] + u[1] * ref_dir[1] < 0:
        u = -u
    return float(u[0]), float(u[1])


def _collect_inliers(square, pool, u, perp_tol):
    """One-sided collinear glyphs: along > 0 keeps only the direction away
    from the square, which disambiguates reading orientation."""
    v = (-u[1], u[0])
    inl = []
    for g in pool:
        px, py = g.cx - square.cx, g.cy - square.cy
        along = px * u[0] + py * u[1]
        across = px * v[0] + py * v[1]
        if along > 0 and abs(across) <= perp_tol:
            inl.append((along, across, g))
    inl.sort(key=lambda item: item[0])
    return inl


def _best_chain(square, pool, c):
    """Best chain of exactly cfg['slots'] glyphs anchored at this square.
    Returns (score, chain_glyphs, u) or None."""
    slots = int(c["slots"])
    if len(pool) < slots:
        return None
    areas = [g.area for g in pool]
    perp_tol = c["chain_perp_tol"] * math.sqrt(max(float(np.median(areas)), 1.0))

    neigh = sorted(pool, key=lambda g: (g.cx - square.cx) ** 2 + (g.cy - square.cy) ** 2)
    best = None
    for seed in neigh[:int(c["chain_neighbors"])]:
        dx, dy = seed.cx - square.cx, seed.cy - square.cy
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            continue
        u = (dx / dist, dy / dist)
        inl = _collect_inliers(square, pool, u, perp_tol)
        if len(inl) >= slots:
            # refine the axis with a straight-line fit through square + chain
            pts = [(square.cx, square.cy)] + [(g.cx, g.cy) for _, _, g in inl[:slots]]
            u = _fit_direction(pts, u)
            inl = _collect_inliers(square, pool, u, perp_tol)
        if len(inl) < slots:
            continue
        chain = inl[:slots]
        alongs = [a for a, _, _ in chain]
        gaps = [alongs[0]] + [alongs[i + 1] - alongs[i] for i in range(slots - 1)]
        med_gap = float(np.median(gaps))
        if med_gap <= 1e-6:
            continue
        if max(gaps) > c["chain_gap_ratio_max"] * med_gap:
            continue  # a hole this big means we bridged into another card
        if gaps[0] > c["chain_first_gap_ratio_max"] * med_gap:
            continue  # first symbol too far from the square
        chain_areas = [g.area for _, _, g in chain]
        if (not c.get("shape_classify")
                and square.area < c["square_area_ratio"] * float(np.median(chain_areas))):
            continue  # anchor is not actually bigger than its symbols
            # (skipped under shape_classify: the anchor is found by shape,
            #  and 26.07.20 anchors are ~ the same size as the dots)

        resid = float(np.mean([abs(x) for _, x, _ in chain])) / max(perp_tol, 1e-6)
        gap_cv = float(np.std(gaps)) / max(float(np.mean(gaps)), 1e-6)
        penalty = 0.0
        if len(inl) > slots and (inl[slots][0] - alongs[-1]) < 1.6 * med_gap:
            penalty = 0.35  # a 9th glyph right behind: suspicious segmentation
        score = 1.0 - 0.4 * resid - 0.3 * min(gap_cv, 1.0) - penalty
        cand = (score, [g for _, _, g in chain], u)
        if best is None or cand[0] > best[0]:
            best = cand
    return best


def _anchorless_candidates(pool, c):
    """Every plausible straight run of exactly cfg['slots'] collinear glyphs
    with NO start anchor (the start-marker requirement is turned off), scored.

    Enumerated ONCE per frame. Re-searching the whole pool after peeling off
    each card is O(cards x pool^2) and stalls the pipeline on a full table
    (20 objects = ~180 glyphs took ~33 s/frame); the caller instead peels
    non-overlapping runs off this list, exactly like the anchored path.
    Reading orientation is fixed by convention (increasing x, or increasing y
    when the chain is near-vertical) so the read is stable across frames."""
    slots = int(c["slots"])
    if len(pool) < slots:
        return []
    xs = np.array([g.cx for g in pool], np.float64)
    ys = np.array([g.cy for g in pool], np.float64)
    areas = np.array([g.area for g in pool], np.float64)
    perp_tol = c["chain_perp_tol"] * math.sqrt(max(float(np.median(areas)), 1.0))
    gap_max = c["chain_gap_ratio_max"]
    k = int(c["chain_neighbors"])
    best = {}                      # frozenset(glyph indices) -> (score, chain, u)
    seen = set()                   # collinear bands already expanded
    for i in range(len(pool)):
        dx, dy = xs - xs[i], ys - ys[i]
        d2 = dx * dx + dy * dy
        for j in np.argsort(d2)[1:k + 1]:
            dist = math.sqrt(float(d2[j]))
            if dist < 1e-6:
                continue
            u = (float(dx[j]) / dist, float(dy[j]) / dist)
            # collect the band around the seed line, refit the axis through
            # it, then re-collect about the band's own centroid. Measuring the
            # second band from the centroid instead of this origin glyph makes
            # the result identical for every origin on the same line, so the
            # `seen` set collapses the ~slots duplicate hypotheses each chain
            # would otherwise generate.
            idx = np.nonzero(np.abs(-dx * u[1] + dy * u[0]) <= perp_tol)[0]
            if idx.size < slots:
                continue
            u = _fit_direction(np.column_stack((xs[idx], ys[idx])), u)
            px, py = xs - float(xs[idx].mean()), ys - float(ys[idx].mean())
            along = px * u[0] + py * u[1]
            across = -px * u[1] + py * u[0]
            idx = np.nonzero(np.abs(across) <= perp_tol)[0]
            if idx.size < slots:
                continue
            idx = idx[np.argsort(along[idx])]
            line_key = idx.tobytes()
            if line_key in seen:
                continue
            seen.add(line_key)
            wa_all, wx_all = along[idx], np.abs(across[idx])
            for w in range(idx.size - slots + 1):
                win = idx[w:w + slots]
                key = frozenset(win.tolist())
                gaps = np.diff(wa_all[w:w + slots])
                med_gap = float(np.median(gaps)) if gaps.size else 0.0
                if med_gap <= 1e-6:
                    continue
                if float(gaps.max()) > gap_max * med_gap:
                    continue  # a hole this big bridges two separate cards
                resid = float(np.mean(wx_all[w:w + slots])) / max(perp_tol, 1e-6)
                gap_cv = float(np.std(gaps)) / max(float(np.mean(gaps)), 1e-6)
                score = 1.0 - 0.4 * resid - 0.3 * min(gap_cv, 1.0)
                if key in best and best[key][0] >= score:
                    continue
                chain = [pool[t] for t in win]
                uu = u
                # canonical orientation for frame-to-frame stability
                if (abs(uu[0]) < 1e-3 and uu[1] < 0) or (abs(uu[0]) >= 1e-3 and uu[0] < 0):
                    uu = (-uu[0], -uu[1])
                    chain = chain[::-1]
                best[key] = (score, chain, uu)
    return list(best.values())


def detect_cards(frame, cfg=None, codebook=None):
    c = cfg_with_defaults(cfg)
    if codebook is None and c["decode_mode"] == "codebook":
        codebook = generate_codebook(c["code_count"], c["slots"], c["min_hamming"])
    gray = prepare_gray(frame, c)
    mask = preprocess(frame, c, gray=gray)
    mask, line_mask, _ = extraction_maps(mask, c)
    glyphs = find_glyphs(mask, c, gray=gray, line_mask=line_mask)

    if not c.get("require_start", True):
        cards = _detect_cards_anchorless(glyphs, c, codebook)
        return mask, cards, glyphs

    starts = [g for g in glyphs if g.kind == "start"]
    candidates = []
    for s in starts:
        # every other glyph may serve as data: a dot misjudged as a square
        # candidate elsewhere must not break this square's chain
        pool = [g for g in glyphs if g is not s]
        found = _best_chain(s, pool, c)
        if found:
            candidates.append((found[0], s, found[1], found[2]))

    candidates.sort(key=lambda item: -item[0])
    used, cards = set(), []
    for chain_score, s, chain, u in candidates:
        gids = {id(s)} | {id(g) for g in chain}
        if gids & used:
            continue
        used |= gids
        card = read_card(s, chain, u, c, codebook, chain_score)
        if c["allow_end_circle"]:
            _consume_end_circle(card, glyphs, used, u, c)
        cards.append(card)

    cards.sort(key=lambda card: (card.cx, card.cy))
    return mask, cards, glyphs


def _detect_cards_anchorless(glyphs, c, codebook):
    """No-anchor path: pull collinear blob chains straight from the data
    glyphs, no start square needed. Used when require_start is off, so the
    marks can be read/stabilized before an anchor protocol is finalized."""
    pool = [g for g in glyphs if g.kind in ("dot", "dash", "start")]
    candidates = _anchorless_candidates(pool, c)
    candidates.sort(key=lambda item: -item[0])
    used, cards = set(), []
    limit = max(1, int(c["max_cards"]))       # table capacity per frame
    for score, chain, u in candidates:
        if len(cards) >= limit:
            break
        gids = {id(g) for g in chain}
        if gids & used:
            continue
        used |= gids
        cards.append(read_card(None, chain, u, c, codebook, score))
    cards.sort(key=lambda card: (card.cx, card.cy))
    return cards


def _consume_end_circle(card, glyphs, used, u, c):
    """Legacy protocol hook: if a big round glyph sits just past the last
    data glyph on the axis, treat it as an end marker (off by default)."""
    last = card.slots[-1].glyph if card.slots else None
    if last is None or len(card.slots) < 2:
        return
    step = math.hypot(last.cx - card.slots[-2].glyph.cx,
                      last.cy - card.slots[-2].glyph.cy)
    for g in glyphs:
        if id(g) in used or g is card.start:
            continue
        px, py = g.cx - last.cx, g.cy - last.cy
        along = px * u[0] + py * u[1]
        across = abs(-px * u[1] + py * u[0])
        if (0.4 * step <= along <= 1.8 * step and across <= 0.6 * step
                and g.circularity >= c["end_min_circularity"]
                and g.area >= c["square_area_ratio"] * 0.8 * last.area):
            used.add(id(g))
            card.end = g
            return


def read_card(start, chain, u, cfg, codebook, chain_score=1.0):
    """Read an ordered chain of data glyphs. With a start square, the axis
    u points from the square toward the symbols. When start is None
    (anchorless mode) every glyph in the chain is data, reading direction
    is a fixed convention, and orientation is disambiguated only by trying
    the reverse for a codebook hit."""
    v = (-u[1], u[0])

    def build(seq):
        bits, sl = [], []
        for idx, g in enumerate(seq):
            b = classify_slot(g, u, v, cfg)
            bits.append(b)
            sl.append(SlotRead(idx, b, g.cx, g.cy, slot_confidence(g, u, v), g))
        return "".join(bits), sl

    bit_string, slots = build(chain)
    code_id, raw_id, status = decode_bits(
        bit_string, codebook, cfg["correction_distance"])

    if start is None:
        # no anchor: the chain could be read either way. Prefer whichever
        # end yields a known codeword; otherwise keep the canonical order.
        if code_id is None and codebook is not None:
            rb, rslots = build(list(reversed(chain)))
            rid, rraw, rstatus = decode_bits(
                rb, codebook, cfg["correction_distance"])
            if rid is not None:
                chain = list(reversed(chain))
                bit_string, slots = rb, rslots
                code_id, raw_id, status = rid, rraw, rstatus
        if code_id is None:
            status = "nostart"

    score = chain_score
    if status == "corrected":
        score *= 0.85
    elif status in ("unknown", "nostart"):
        score *= 0.5
    angle = math.degrees(math.atan2(u[1], u[0]))
    anchor = start if start is not None else chain[0]
    pts = ([(anchor.cx, anchor.cy)] if start is not None else []) \
        + [(g.cx, g.cy) for g in chain]
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return MorseCard(
        cx=cx,
        cy=cy,
        angle=angle,
        bits=bit_string,
        code_id=code_id,
        raw_id=raw_id,
        status=status,
        score=score,
        start=anchor,
        end=chain[-1],
        slots=slots,
    )


def _angle_diff_deg(a, b):
    d = (a - b) % 180.0
    return min(d, 180.0 - d)


def classify_slot(glyph, u, v, cfg):
    """Dot vs dash relative to the card axis: dashes are elongated ALONG
    the reading direction. When the shape classifier already labelled the
    glyph, trust that (line=1, circle=0)."""
    if glyph.shape == "line":
        return "1"
    if glyph.shape == "circle":
        return "0"
    if cfg.get("extraction_mode", "contour") in ("hough", "morphology"):
        return ("1" if glyph.line_score >=
                float(cfg.get("method_line_score_min", 0.22)) else "0")
    pts = glyph.contour.reshape(-1, 2).astype(np.float64)
    if len(pts) == 0:
        return "0"
    rel = pts - np.array([[glyph.cx, glyph.cy]])
    axis_len = float(np.ptp(rel @ np.array(u)))
    perp_len = float(np.ptp(rel @ np.array(v)))
    if axis_len >= max(perp_len, 1.0) * cfg["dash_ratio_min"]:
        return "1"
    # fallback: clearly elongated glyph whose own long axis roughly follows
    # the card axis (hand-drawn dashes wobble off-axis a little)
    if glyph.aspect >= cfg["dash_aspect_min"]:
        axis_deg = math.degrees(math.atan2(u[1], u[0]))
        if _angle_diff_deg(glyph.rect_angle, axis_deg) <= 40.0:
            return "1"
    return "0"


def slot_confidence(glyph, u, v):
    pts = glyph.contour.reshape(-1, 2).astype(np.float64)
    if len(pts) == 0:
        return 0.0
    rel = pts - np.array([[glyph.cx, glyph.cy]])
    axis_len = float(np.ptp(rel @ np.array(u)))
    perp_len = float(np.ptp(rel @ np.array(v)))
    ratio = axis_len / max(perp_len, 1.0)
    if ratio >= 1.7:
        return min(1.0, ratio / 4.0)
    return min(1.0, max(glyph.circularity, glyph.extent))


def render_card(frame, bits, center, angle_deg=0.0, step=34, dot_r=6,
                dash_len=22, dash_thick=7, marker=21, color=0,
                jitter=0.0, rng=None, marker_shape="square", draw_start=True):
    """Draw a synthetic card (start marker + dot/dash symbols, no end
    marker) into a BGR or gray frame. marker_shape follows the 26.07.20
    plate designs: 'square', 'triangle', 'pentagon', 'hexagon', or 'star'.
    draw_start=False omits the anchor entirely (no-start / anchorless path).
    jitter (0..~0.2) adds hand-drawn style position wobble in units of
    step; pass a random.Random for reproducible layouts."""
    slots = len(bits)
    end_x = (slots + 1) * step
    origin_x = end_x / 2.0
    r = math.radians(angle_deg)
    rot = np.array([[math.cos(r), -math.sin(r)],
                    [math.sin(r), math.cos(r)]])

    def wobble():
        if jitter <= 0.0 or rng is None:
            return 0.0, 0.0
        return (rng.uniform(-jitter, jitter) * step,
                rng.uniform(-jitter, jitter) * step)

    def to_img(x, y):
        p = (np.array([x - origin_x, y]) @ rot.T) + np.array(center)
        return int(round(p[0])), int(round(p[1]))

    jx, jy = wobble()
    sx, sy = to_img(0.0 + jx, jy)
    m2 = marker // 2
    if not draw_start:
        pass
    elif marker_shape == "triangle":
        pts = np.array([[sx, sy - m2], [sx - m2, sy + m2], [sx + m2, sy + m2]])
        cv2.fillPoly(frame, [pts], color, cv2.LINE_AA)
    elif marker_shape in ("pentagon", "hexagon"):
        # filled regular convex polygon, apex up. 5 vs 6 corners give distinct
        # vertex counts (triangle=3, square=4, pentagon=5, hexagon=6, circle=8+)
        sides = 5 if marker_shape == "pentagon" else 6
        rad = m2 * 1.12          # inscribe so the footprint ~matches the square
        pts = [[sx + rad * math.cos(math.radians(-90 + i * 360 / sides)),
                sy + rad * math.sin(math.radians(-90 + i * 360 / sides))]
               for i in range(sides)]
        cv2.fillPoly(frame, [np.array(pts, np.int32)], color, cv2.LINE_AA)
    elif marker_shape == "star":
        pts = []
        for i in range(10):
            rad = m2 * 1.15 if i % 2 == 0 else m2 * 0.5
            a = math.radians(-90 + i * 36)
            pts.append([sx + rad * math.cos(a), sy + rad * math.sin(a)])
        cv2.fillPoly(frame, [np.array(pts, np.int32)], color, cv2.LINE_AA)
    else:
        cv2.rectangle(frame, (sx - m2, sy - m2), (sx + m2, sy + m2), color,
                      cv2.FILLED)
    for i, bit in enumerate(bits):
        jx, jy = wobble()
        x = (i + 1) * step + jx
        if bit == "1":
            a = to_img(x - dash_len / 2, jy)
            b = to_img(x + dash_len / 2, jy)
            cv2.line(frame, a, b, color, dash_thick, cv2.LINE_AA)
        else:
            cx, cy = to_img(x, jy)
            cv2.circle(frame, (cx, cy), dot_r, color, cv2.FILLED, cv2.LINE_AA)
    return frame


def render_scene(cards, size=(900, 520), bg=245, color=0, jitter=0.0, rng=None):
    """cards: [(bits, cx, cy, angle_deg), ...]."""
    frame = np.full((size[1], size[0], 3), bg, np.uint8)
    if isinstance(color, (int, float)):
        color = (color, color, color)  # else cv2 reads a bare int as blue-only
    for bits, cx, cy, angle in cards:
        render_card(frame, bits, (cx, cy), angle, color=color,
                    jitter=jitter, rng=rng)
    return frame
