"""
Visual diagnostics for the web tuner.

The preprocessing filters and the glyph-extraction method (contour / hough /
morphology) are chosen by LOOKING at what they do, not by reading numbers.
That only works if every option produces a picture, so the rules here are:

  * every diagnostic view is drawn ON the frame — a tint over the (dimmed)
    preprocessed grayscale, never a bare binary mask floating on black.
    A bare mask is useless feedback: `contour` builds no line map at all, so
    the old "선 후보" tab was a fully black screen, and `morphology` at its
    default kernel claims almost every pixel, so "점 후보" was black too.
  * each method is drawn by its OWN mechanism, not by the rasterized union
    the three happen to share. Hough shows the segments it voted for, over
    the Canny edges it voted on; morphology shows which of its four
    orientations claimed each pixel, next to the kernels drawn to scale;
    contour — which builds no pixel map at all — shows the rotated box and
    long axis per blob, the geometry it actually thresholds. Switching the
    method has to change the picture, or the option is untunable.
  * every thresholded number is drawn as a BAR with the threshold marked, so
    moving a slider is visibly connected to a verdict flip.
  * captions go in a strip appended UNDER the image, so no judgement is ever
    made through text burned over the pixels being judged.
  * captions are ASCII: cv2.putText has no Unicode font, so Korean would
    render as boxes.
"""

import cv2
import numpy as np

from tracker.morse import MORPH_ANGLES, cfg_with_defaults


#: stream names the server serves and the UI tabs offer, in tab order
VIEWS = ("compare", "enhanced", "extract", "lines", "dots", "overlay",
         "mask", "raw")

DIAG_LINE_BGR = (235, 90, 255)     # magenta — the method calls this a LINE (1)
DIAG_DOT_BGR = (60, 215, 60)       # green   — the method calls this a DOT (0)
DIAG_CAND_BGR = (0, 190, 255)      # amber   — contour: one undivided candidate map
DIAG_EDGE_BGR = (150, 120, 90)     # slate   — the Canny edges Hough votes on
DIAG_SEG_BGR = (255, 255, 255)     # white   — a segment Hough actually returned
DIAG_DIM = 0.34                    # background darkening so tints stay readable
CAPTION_BG = 22
CAPTION_LH = 19

#: morphology: one colour per orientation, so the picture says WHICH direction
#: claimed a blob (the union alone cannot). Angles match tracker.morse.
MORPH_COLORS = {0: (255, 90, 235), 45: (0, 175, 255),
                90: (255, 195, 60), 135: (190, 120, 255)}
MORPH_MULTI_BGR = (235, 235, 235)  # claimed by 2+ orientations = shape-blind

#: preprocessing stages in the order prepare_gray() applies them
PREPROCESS_STEPS = [
    ("autocontrast_enabled", "Autocontrast"),
    ("gamma_enabled", "Gamma"),
    ("bilateral_enabled", "Bilateral"),
    ("clahe_enabled", "CLAHE"),
    ("unsharp_enabled", "Unsharp"),
]


def preprocess_chain(cfg):
    """Names of the preprocessing stages actually running, in order."""
    if not cfg.get("preprocess_enabled"):
        return []
    return [name for key, name in PREPROCESS_STEPS if cfg.get(key)]


def extraction_summary(cfg):
    """(mode, ascii parameter summary) for the active extraction method — the
    knobs that matter for THIS method only, so the caption matches the
    controls the UI is showing. Defaults are filled in first: a config saved
    before a knob existed must still caption a number, never `None`."""
    c = cfg_with_defaults(cfg)
    mode = str(c.get("extraction_mode", "contour"))
    if mode == "hough":
        params = (f"vote={c.get('hough_threshold')} "
                  f"minLen={c.get('hough_min_line_length')}px "
                  f"maxGap={c.get('hough_max_line_gap')}px "
                  f"thick={c.get('hough_line_thickness')}px")
    elif mode == "morphology":
        params = (f"kernel={c.get('morph_line_length')}x"
                  f"{c.get('morph_line_thickness')}px, 4 directions")
    else:
        params = (f"dot aspect<={c.get('compact_aspect_max')} "
                  f"dash aspect>={c.get('dash_aspect_min')}")
    return mode, params


def glyph_is_line(glyph, cfg):
    """Would this glyph read as a dash (1)?

    Follows tracker.morse.classify_slot in the SAME ORDER, or the picture
    would contradict the decoder: the shape classifier wins first, then the
    line map, then geometry. The one thing it cannot use is the card axis
    (contour's real test is elongation ALONG the chain, which exists only once
    a chain is assembled) — standalone, the aspect-based kind that find_glyphs
    already assigned is the honest proxy.
    """
    if glyph.shape == "line":
        return True
    if glyph.shape == "circle":
        return False
    if str(cfg.get("extraction_mode", "contour")) in ("hough", "morphology"):
        return glyph.line_score >= float(cfg.get("method_line_score_min", 0.22))
    return glyph.kind == "dash"


def dim_base(gray):
    """Dimmed BGR copy of the (preprocessed) gray, so colour tints read on top
    while the frame content stays recognizable underneath."""
    base = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return (base.astype(np.float32) * DIAG_DIM).astype(np.uint8)


def tint(canvas, mask, color, alpha=0.92):
    """Paint `color` onto `canvas` wherever `mask` is set. In-place."""
    if mask is None or cv2.countNonZero(mask) == 0:
        return canvas
    sel = mask > 0
    col = np.array(color, np.float32)
    canvas[sel] = (canvas[sel].astype(np.float32) * (1.0 - alpha)
                   + col * alpha).astype(np.uint8)
    return canvas


def caption(img, lines):
    """Append a caption strip UNDER the image. `lines` is [(text, bgr), ...].
    Text never covers the pixels being judged."""
    if not lines:
        return img
    height = 8 + CAPTION_LH * len(lines)
    bar = np.full((height, img.shape[1], 3), CAPTION_BG, np.uint8)
    for i, (text, color) in enumerate(lines):
        cv2.putText(bar, text, (10, 4 + CAPTION_LH * (i + 1) - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    return np.vstack((img, bar))


def mask_over_frame(gray, mask, color):
    """A single derived mask shown in place on the frame."""
    return tint(dim_base(gray), mask, color)


def compare_wipe(original, enhanced, split=0.5):
    """Original left of the split, preprocessed right of it — same frame, same
    scale, so a subtle contrast change is judged at the seam instead of across
    two half-size thumbnails. split=1 is all original, split=0 all processed,
    which makes the slider a full A/B on its own."""
    h, w = original.shape[:2]
    x = max(0, min(w, int(round(w * float(split)))))
    out = original.copy()
    if x < w:
        out[:, x:] = enhanced[:, x:]
    if 0 < x < w:
        cv2.line(out, (x, 0), (x, h), (0, 200, 255), 2, cv2.LINE_AA)
    return out


def verdict_masks(mask, glyphs, cfg):
    """Per-blob dash/dot masks built from the verdicts themselves.

    contour builds no pixel line map, so "선 후보" / "점 후보" had nothing to
    show. The verdict per blob still exists — fill each blob's own pixels
    into the map its verdict puts it in. Not a pixel-level line map, and the
    captions say so, but it is the split the decoder will actually use."""
    line_like = np.zeros_like(mask)
    dot_like = np.zeros_like(mask)
    for g in glyphs:
        target = line_like if glyph_is_line(g, cfg) else dot_like
        cv2.drawContours(target, [g.contour], -1, 255, cv2.FILLED)
    return line_like, dot_like


def score_bar(vis, org, value, threshold, vmax, color, width=34):
    """A thresholded number drawn as a bar: filled part = the value, the tick
    = the threshold it is compared against. Reading whether the bar clears
    the tick is instant; comparing two decimals is not."""
    x, y = int(org[0]), int(org[1])
    vmax = max(vmax, 1e-6)
    fill = int(round(width * max(0.0, min(1.0, value / vmax))))
    cv2.rectangle(vis, (x, y), (x + width, y + 4), (55, 55, 55), -1)
    if fill > 0:
        cv2.rectangle(vis, (x, y), (x + fill, y + 4), color, -1)
    tx = x + int(round(width * max(0.0, min(1.0, threshold / vmax))))
    cv2.line(vis, (tx, y - 2), (tx, y + 6), (240, 240, 240), 1)
    return vis


def _draw_contour_layer(vis, mask, glyphs, cfg):
    """contour: no line map exists. What it thresholds is each blob's rotated
    box — draw that box and its long axis, which is where `aspect` comes
    from, so the number in the tag has a visible source."""
    tint(vis, mask, DIAG_CAND_BGR, 0.72)
    for g in glyphs:
        color = DIAG_LINE_BGR if glyph_is_line(g, cfg) else DIAG_DOT_BGR
        if g.contour is None or len(g.contour) < 2:
            continue
        (rcx, rcy), (rw, rh), rang = cv2.minAreaRect(g.contour)
        box = cv2.boxPoints(((rcx, rcy), (rw, rh), rang)).astype(np.int32)
        cv2.polylines(vis, [box], True, color, 1, cv2.LINE_AA)
        # long axis: the "along" length whose ratio to the short side is aspect
        half = max(rw, rh) / 2.0
        a = np.radians(rang if rw >= rh else rang + 90.0)
        dx, dy = np.cos(a) * half, np.sin(a) * half
        cv2.line(vis, (int(rcx - dx), int(rcy - dy)),
                 (int(rcx + dx), int(rcy + dy)), color, 1, cv2.LINE_AA)
    return vis


def _draw_hough_layer(vis, detail):
    """hough: show the edges it voted on and the segments it returned. The
    thickened line map alone hides both — with only the map on screen,
    vote / minLen / maxGap changes are invisible."""
    if detail.get("edges") is not None:
        tint(vis, detail["edges"], DIAG_EDGE_BGR, 0.55)
    tint(vis, detail["dot_mask"], DIAG_DOT_BGR, 0.7)
    tint(vis, detail["line_mask"], DIAG_LINE_BGR, 0.6)
    segments = detail.get("segments", ())
    # endpoints answer "did minLen/maxGap split this stroke?", but past a few
    # hundred segments the dots bury the lines they belong to
    ends = len(segments) <= 200
    for x1, y1, x2, y2 in segments:
        cv2.line(vis, (x1, y1), (x2, y2), DIAG_SEG_BGR, 1, cv2.LINE_AA)
        if ends:
            cv2.circle(vis, (x1, y1), 2, DIAG_CAND_BGR, -1, cv2.LINE_AA)
            cv2.circle(vis, (x2, y2), 2, DIAG_CAND_BGR, -1, cv2.LINE_AA)
    return vis


def _draw_kernel_legend(vis, detail):
    """The four kernels drawn twice — zoomed so the shape is visible at all,
    and at TRUE pixel size right under it, which is the comparison that
    matters: the kernel has to be longer than a dot and shorter than a dash."""
    kernels = detail.get("kernels") or {}
    if not kernels:
        return vis
    length = max(1, detail.get("kernel_length", 0))
    zoom = max(2, int(round(26 / length)))
    cell = length * zoom
    pad, x0, y0 = 8, 10, 10
    swatches = len(kernels) + 1                       # + the "2+ dirs" swatch
    panel_w = pad + swatches * (cell + pad)
    panel_h = cell + 20 + length + 18
    cv2.rectangle(vis, (x0 - 4, y0 - 4), (x0 + panel_w, y0 + panel_h),
                  (26, 26, 26), -1)
    for i, angle in enumerate(sorted(kernels)):
        color = MORPH_COLORS.get(angle, DIAG_LINE_BGR)
        kx = x0 + pad + i * (cell + pad)
        big = cv2.resize(kernels[angle] * 255, (cell, cell),
                         interpolation=cv2.INTER_NEAREST)
        patch = vis[y0:y0 + cell, kx:kx + cell]
        if patch.shape[:2] == (cell, cell):
            tint(patch, big, color, 1.0)
            cv2.rectangle(vis, (kx, y0), (kx + cell, y0 + cell), (90, 90, 90), 1)
        cv2.putText(vis, f"{angle}", (kx, y0 + cell + 13),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, color, 1, cv2.LINE_AA)
        ty = y0 + cell + 18
        true_patch = vis[ty:ty + length, kx:kx + length]
        if true_patch.shape[:2] == (length, length):
            tint(true_patch, kernels[angle] * 255, color, 1.0)
    mx = x0 + pad + len(kernels) * (cell + pad)
    cv2.rectangle(vis, (mx, y0), (mx + cell, y0 + cell), MORPH_MULTI_BGR, -1)
    cv2.putText(vis, "2+", (mx, y0 + cell + 13), cv2.FONT_HERSHEY_SIMPLEX,
                0.38, MORPH_MULTI_BGR, 1, cv2.LINE_AA)
    cv2.putText(vis, f"kernel {length}px (zoom x{zoom}, true size under label)",
                (x0 + pad, y0 + panel_h - 4), cv2.FONT_HERSHEY_SIMPLEX,
                0.36, (200, 200, 200), 1, cv2.LINE_AA)
    return vis


def _draw_morphology_layer(vis, detail):
    """morphology: colour each orientation separately, and mark pixels that
    more than one orientation claimed. The union alone is one flat magenta
    sheet in which a kernel too short to reject anything looks exactly like a
    well-tuned one; here a dash shows as ONE direction (the chain's) while an
    over-short kernel floods everything into the 2+ colour."""
    tint(vis, detail["dot_mask"], DIAG_DOT_BGR, 0.82)
    orientations = detail.get("orientations", {})
    if not orientations:
        return vis
    claims = np.zeros(vis.shape[:2], np.uint8)
    for opened in orientations.values():
        claims += (opened > 0).astype(np.uint8)
    # cached back into the detail dict: the caller reports the 2+ pixel count
    # in its numeric panel and must not pay for this pass twice
    detail["claims"] = claims
    for angle in MORPH_ANGLES:
        opened = orientations.get(angle)
        if opened is None:
            continue
        only = (((opened > 0) & (claims == 1)).astype(np.uint8)) * 255
        tint(vis, only, MORPH_COLORS.get(angle, DIAG_LINE_BGR), 0.85)
    tint(vis, ((claims > 1).astype(np.uint8)) * 255, MORPH_MULTI_BGR, 0.85)
    return _draw_kernel_legend(vis, detail)


def _draw_glyph_verdicts(vis, glyphs, cfg, mode):
    """Per blob: the box, the verdict, the deciding number, and that number
    as a bar against its threshold."""
    c = cfg_with_defaults(cfg)
    thr = float(c.get("method_line_score_min", 0.22))
    dash_aspect = float(c.get("dash_aspect_min", 1.85))
    n_line = 0
    for g in glyphs:
        is_line = glyph_is_line(g, cfg)
        n_line += bool(is_line)
        color = DIAG_LINE_BGR if is_line else DIAG_DOT_BGR
        x, y, w, h = g.bbox
        cv2.rectangle(vis, (x - 2, y - 2), (x + w + 2, y + h + 2), color, 1)
        if mode in ("hough", "morphology"):
            value, threshold, vmax = g.line_score, thr, 1.0
            text = f"{'1' if is_line else '0'}:{g.line_score:.2f}"
        else:
            value, threshold = g.aspect, dash_aspect
            vmax = max(3.0, dash_aspect * 1.6)
            text = f"{'1' if is_line else '0'}:{g.aspect:.1f}"
        cv2.putText(vis, text, (x - 2, y - 6), cv2.FONT_HERSHEY_SIMPLEX,
                    0.36, color, 1, cv2.LINE_AA)
        score_bar(vis, (x - 2, y + h + 4), value, threshold, vmax, color)
    return n_line


def draw_extraction(gray, detail, glyphs, cfg):
    """The one view that answers "what did the chosen method actually do?".

    Pixel layer  — the method's OWN intermediates (see the module docstring):
                   Hough segments over Canny edges, morphology per
                   orientation with its kernels, contour's rotated boxes.
    Glyph layer  — the dot/dash verdict each blob would get, the number that
                   decided it, and that number as a bar against its
                   threshold.
    `detail` is tracker.morse.extraction_detail() for this frame.
    """
    mode, params = extraction_summary(cfg)
    vis = dim_base(gray)
    if mode == "hough":
        _draw_hough_layer(vis, detail)
    elif mode == "morphology":
        _draw_morphology_layer(vis, detail)
    else:
        _draw_contour_layer(vis, detail["mask"], glyphs, cfg)

    n_line = _draw_glyph_verdicts(vis, glyphs, cfg, mode)
    n_dot = len(glyphs) - n_line
    thr = float(cfg_with_defaults(cfg).get("method_line_score_min", 0.22))
    if mode == "hough":
        rule = (f"segments={len(detail.get('segments', ()))} -> thickened to "
                f"{detail.get('line_thickness', 0)}px; dash when overlap >= {thr:.2f}")
    elif mode == "morphology":
        rule = (f"0/45/90/135 = pink/orange/blue/violet, white = 2+ directions "
                f"(kernel too short); dash when overlap >= {thr:.2f}")
    else:
        rule = ("no line map built - dash when the rotated box is elongated "
                "along the chain axis")
    return caption(vis, [
        (f"EXTRACTION [{mode}]  {params}", DIAG_CAND_BGR),
        (rule, (200, 200, 200)),
        (f"bar = deciding value, tick = threshold   ->   "
         f"dash(1)={n_line}  dot(0)={n_dot}", (225, 225, 225)),
    ])
