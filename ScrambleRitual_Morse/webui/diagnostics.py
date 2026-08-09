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
  * captions go in a strip appended UNDER the image, so no judgement is ever
    made through text burned over the pixels being judged.
  * captions are ASCII: cv2.putText has no Unicode font, so Korean would
    render as boxes.
"""

import cv2
import numpy as np


#: stream names the server serves and the UI tabs offer, in tab order
VIEWS = ("compare", "enhanced", "extract", "lines", "dots", "overlay",
         "mask", "raw")

DIAG_LINE_BGR = (235, 90, 255)     # magenta — the method calls this a LINE (1)
DIAG_DOT_BGR = (60, 215, 60)       # green   — the method calls this a DOT (0)
DIAG_CAND_BGR = (0, 190, 255)      # amber   — contour: one undivided candidate map
DIAG_DIM = 0.34                    # background darkening so tints stay readable
CAPTION_BG = 22
CAPTION_LH = 19

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
    controls the UI is showing."""
    mode = str(cfg.get("extraction_mode", "contour"))
    if mode == "hough":
        params = (f"vote={cfg.get('hough_threshold')} "
                  f"minLen={cfg.get('hough_min_line_length')}px "
                  f"maxGap={cfg.get('hough_max_line_gap')}px "
                  f"thick={cfg.get('hough_line_thickness')}px")
    elif mode == "morphology":
        params = (f"kernel={cfg.get('morph_line_length')}x"
                  f"{cfg.get('morph_line_thickness')}px, 4 directions")
    else:
        params = (f"dot aspect<={cfg.get('compact_aspect_max')} "
                  f"dash aspect>={cfg.get('dash_aspect_min')}")
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


def draw_extraction(gray, mask, line_mask, dot_mask, glyphs, cfg):
    """The one view that answers "what did the chosen method actually do?".

    Pixel layer  — how the method split the candidate mask.
    Glyph layer  — the dot/dash verdict each blob would get, with the number
                   that decided it (line-map overlap, or aspect for contour),
                   so moving a slider is visibly connected to a verdict flip.
    """
    mode, params = extraction_summary(cfg)
    vis = dim_base(gray)
    if mode == "contour":
        # contour builds no line map; the whole candidate mask is one pool and
        # the split happens per blob, by geometry
        tint(vis, mask, DIAG_CAND_BGR, 0.72)
    else:
        tint(vis, dot_mask, DIAG_DOT_BGR, 0.82)
        tint(vis, line_mask, DIAG_LINE_BGR, 0.95)   # line wins any overlap

    n_line = 0
    for g in glyphs:
        is_line = glyph_is_line(g, cfg)
        n_line += bool(is_line)
        color = DIAG_LINE_BGR if is_line else DIAG_DOT_BGR
        x, y, w, h = g.bbox
        cv2.rectangle(vis, (x - 2, y - 2), (x + w + 2, y + h + 2), color, 1)
        value = (f"{g.line_score:.2f}" if mode in ("hough", "morphology")
                 else f"{g.aspect:.1f}")
        cv2.putText(vis, f"{'1' if is_line else '0'}:{value}", (x - 2, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, color, 1, cv2.LINE_AA)

    n_dot = len(glyphs) - n_line
    if mode in ("hough", "morphology"):
        rule = (f"dash when line-map overlap >= "
                f"{float(cfg.get('method_line_score_min', 0.22)):.2f}")
    else:
        rule = "dash when elongated along the chain axis (no line map built)"
    return caption(vis, [
        (f"EXTRACTION [{mode}]  {params}", DIAG_CAND_BGR),
        (f"{rule}   ->   dash(1)={n_line}  dot(0)={n_dot}",
         (225, 225, 225)),
    ])
