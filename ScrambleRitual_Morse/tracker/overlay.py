"""Card-read overlay — the picture of what the decoder actually recognized.

Every runner (the web tuner "오버레이" tab, `tracker.morse_run`, the contrast
experiment) draws this same layer, so a read is judged the same way
everywhere. It answers two questions at a glance:

  1. WHICH blobs were tied together into one ID — a tinted hull around the
     anchor and its symbols plus the chain polyline in that ID's colour, so
     two cards lying end to end never look like one run of marks.
  2. WHAT each blob was read as — a per-slot box tagged `index:bit`, the slot
     dot painted in the same dot/dash colours the extraction views use, and a
     bit strip next to the ID chip that redraws the decoded word as ○/─
     symbols. Holding that strip against the physical marks underneath is the
     fastest way to spot a flipped slot.

Colour is keyed to the decoded ID, not to detection order, so an object keeps
its colour frame to frame; a chain with no ID is always red.

Text is ASCII: cv2.putText has no Unicode font, so Korean renders as boxes.
"""

import math

import cv2
import numpy as np


#: per-ID colours (BGR), indexed by code_id so a card keeps its colour
CARD_PALETTE = [
    (0, 230, 255),    # amber
    (255, 80, 220),   # magenta
    (70, 240, 70),    # green
    (255, 170, 40),   # blue
    (60, 100, 255),   # red-orange
    (230, 230, 90),   # cyan
    (150, 120, 255),  # pink
]
UNKNOWN_CARD_BGR = (60, 60, 255)     # chain assembled, no ID decoded
DASH_BGR = (235, 90, 255)            # bit 1 — the 선 후보 view's magenta
DOT_BGR = (60, 215, 60)              # bit 0 — the 점 후보 view's green
CHIP_BG = (18, 18, 18)


def card_color(card):
    """Stable BGR for a card: by decoded ID, red while it has none."""
    code_id = getattr(card, "code_id", None)
    if code_id is None:
        return UNKNOWN_CARD_BGR
    return CARD_PALETTE[int(code_id) % len(CARD_PALETTE)]


def bit_color(bit):
    return DASH_BGR if bit == "1" else DOT_BGR if bit == "0" else UNKNOWN_CARD_BGR


def overlay_scale(shape):
    """Stroke/text scale for the frame size. A 1200x1600 still needs heavier
    strokes than a 640x480 preview or the labels are unreadable."""
    return max(0.75, min(2.4, min(shape[0], shape[1]) / 640.0))


def _pt(x, y):
    return int(round(x)), int(round(y))


def measure_text_block(lines, scale=1.0):
    """(w, h) the box text_block() would occupy. Callers place many readouts
    on one frame and need the footprint BEFORE drawing, to keep two of them
    off each other."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs, th = 0.5 * scale, max(1, int(round(1.1 * scale)))
    sizes = [cv2.getTextSize(t, font, fs, th) for t in lines]
    gap = max(4, int(round(6 * scale)))
    lh = max(s[0][1] for s in sizes) + gap
    return max(s[0][0] for s in sizes) + 10, lh * len(lines) + gap


def text_chip(vis, text, org, color, scale=1.0, bg=CHIP_BG):
    """Text on a filled backing box, clamped inside the frame — judgement text
    has to stay readable over a backlit plate as well as over dark ink.
    `org` is the text baseline start; returns the BOX drawn, (x0, y0, x1, y1),
    so callers can stack under it or keep the next one clear of it."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = 0.5 * scale
    th = max(1, int(round(1.1 * scale)))
    (tw, tht), base = cv2.getTextSize(text, font, fs, th)
    x = max(4, min(int(org[0]), vis.shape[1] - tw - 6))
    y = max(tht + 8, min(int(org[1]), vis.shape[0] - base - 4))
    box = (x - 5, y - tht - 6, x + tw + 5, y + base + 3)
    cv2.rectangle(vis, box[:2], box[2:], bg, -1)
    cv2.putText(vis, text, (x, y), font, fs, color, th, cv2.LINE_AA)
    return box


def text_block(vis, lines, org, color, scale=1.0, bg=CHIP_BG, to_left=False):
    """Several lines in ONE backing box, clamped inside the frame. Stacking
    single-line chips overlaps them; a block keeps a multi-value readout (ID /
    tilt / flip / tension) legible as one unit. `to_left` grows the box to the
    left of `org`, so a readout near the right edge stays on screen."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    fs = 0.5 * scale
    th = max(1, int(round(1.1 * scale)))
    sizes = [cv2.getTextSize(t, font, fs, th) for t in lines]
    tw = max(s[0][0] for s in sizes)
    gap = max(4, int(round(6 * scale)))
    lh = max(s[0][1] for s in sizes) + gap
    box_h = lh * len(lines) + gap
    x, y = int(org[0]), int(org[1])
    if to_left:
        x -= tw + 12
    x = max(5, min(x, vis.shape[1] - tw - 7))
    y = max(2, min(y, vis.shape[0] - box_h - 3))
    box = (x - 5, y, x + tw + 5, y + box_h)
    cv2.rectangle(vis, box[:2], box[2:], bg, -1)
    for i, text in enumerate(lines):
        cv2.putText(vis, text, (x, y + lh * (i + 1)), font, fs, color, th,
                    cv2.LINE_AA)
    return box


def draw_bit_strip(vis, bits, origin, color, scale=1.0):
    """Redraw the decoded word as the symbols it claims to have seen: a start
    block, then ○ per 0 and ─ per 1 in reading order. Compared against the
    physical chain, a flipped slot shows up without reading any digits."""
    step = int(round(11 * scale))
    r = max(2, int(round(3 * scale)))
    th = max(1, int(round(1.6 * scale)))
    x, y = int(origin[0]), int(origin[1])
    # its own backing box: the strip is read against the marks underneath, so
    # it must not blend into them
    box = (x - r - 4, y - r - 4,
           x + (len(bits) + 1) * step - step + r + 4, y + r + 4)
    cv2.rectangle(vis, box[:2], box[2:], CHIP_BG, -1)
    cv2.rectangle(vis, (x - r, y - r), (x + r, y + r), color, -1)
    for i, bit in enumerate(bits):
        bx = x + (i + 1) * step
        if bit == "1":
            cv2.line(vis, (bx - r, y), (bx + r, y), DASH_BGR, th, cv2.LINE_AA)
        elif bit == "0":
            cv2.circle(vis, (bx, y), r, DOT_BGR, -1, cv2.LINE_AA)
        else:                       # '?' — the slot never resolved
            cv2.drawMarker(vis, (bx, y), UNKNOWN_CARD_BGR,
                           cv2.MARKER_TILTED_CROSS, 2 * r, th, cv2.LINE_AA)
    return box


def _chain_points(card):
    """Chain points in reading order: the anchor first (when the card has a
    real start marker), then every slot centre."""
    pts = []
    start = getattr(card, "start", None)
    first_glyph = card.slots[0].glyph if card.slots else None
    if start is not None and start is not first_glyph:
        pts.append((start.cx, start.cy))
    pts.extend((s.cx, s.cy) for s in card.slots)
    return pts


def _group_hull(card, pad):
    """Convex hull over the padded bboxes of every glyph this card owns — the
    'these blobs are one ID' region."""
    glyphs = [s.glyph for s in card.slots if s.glyph is not None]
    start = getattr(card, "start", None)
    if start is not None:
        glyphs.append(start)
    corners = []
    for g in glyphs:
        x, y, w, h = g.bbox
        corners.extend([(x - pad, y - pad), (x + w + pad, y - pad),
                        (x + w + pad, y + h + pad), (x - pad, y + h + pad)])
    if len(corners) < 3:
        return None
    return cv2.convexHull(np.array(corners, np.int32))


def draw_card_reads(vis, cards, scale=None, tint_alpha=0.16, slot_tags=True,
                    note_fn=None, taken=None):
    """Draw the recognized cards onto `vis` (in place) and return it.

    scale       stroke/text scale; derived from the frame size when None
    tint_alpha  strength of the group hull tint (0 disables the tint)
    slot_tags   per-slot `index:bit` labels; off for crowded live views
    note_fn     optional card -> str appended to the ID chip (the morse
                runner uses it for the sound fragment a card selects)
    taken       list to append the drawn label boxes to, so a second layer
                on the same frame (the object readout) can keep clear of them
    """
    if not cards:
        return vis
    s = overlay_scale(vis.shape) if scale is None else scale
    chain_th = max(2, int(round(2.2 * s)))
    box_th = max(1, int(round(1.4 * s)))
    tint = vis.copy() if tint_alpha > 0 else None

    for card in cards:
        color = card_color(card)
        pts = _chain_points(card)
        if not pts:
            continue

        # --- group: one hull over every blob this ID claims ---
        hull = _group_hull(card, pad=int(round(4 * s)))
        if hull is not None:
            if tint is not None:
                cv2.fillConvexPoly(tint, hull, color)
            cv2.polylines(vis, [hull], True, color, 1, cv2.LINE_AA)

        # --- connection: the chain in reading order, head at the last slot ---
        poly = np.array([_pt(x, y) for x, y in pts], np.int32)
        if len(poly) >= 2:
            cv2.polylines(vis, [poly], False, color, chain_th, cv2.LINE_AA)
            cv2.arrowedLine(vis, tuple(poly[-2]), tuple(poly[-1]), color,
                            chain_th, cv2.LINE_AA, tipLength=0.4)

        # --- anchor: where the read started, and what shape it was ---
        start = getattr(card, "start", None)
        if start is not None:
            a = _pt(start.cx, start.cy)
            cv2.drawMarker(vis, a, color, cv2.MARKER_DIAMOND,
                           int(round(18 * s)), box_th + 1, cv2.LINE_AA)
            # in anchorless mode (require_start off) the "anchor" is just the
            # first symbol — say so, or the picture claims a marker that the
            # physical card does not have
            if slot_tags:               # off on a crowded table, with the tags
                if card.slots and start is card.slots[0].glyph:
                    tag = "no anchor"
                else:
                    tag = getattr(start, "shape", "") or "start"
                cv2.putText(vis, tag, (a[0] - int(round(14 * s)),
                                       a[1] + int(round(20 * s))),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.36 * s, color, box_th,
                            cv2.LINE_AA)

        # --- per slot: what this blob was read as ---
        for slot in card.slots:
            col = bit_color(slot.bit)
            if slot.glyph is not None:
                x, y, w, h = slot.glyph.bbox
                cv2.rectangle(vis, (x, y), (x + w, y + h), color, box_th)
            cv2.circle(vis, _pt(slot.cx, slot.cy), max(2, int(round(3 * s))),
                       col, -1, cv2.LINE_AA)
            if not slot_tags:
                continue
            tag = f"{slot.index + 1}:{slot.bit}"
            org = (int(slot.cx) - int(round(8 * s)),
                   int(slot.cy) - int(round(10 * s)))
            cv2.putText(vis, tag, org, cv2.FONT_HERSHEY_SIMPLEX, 0.38 * s,
                        (0, 0, 0), box_th + 2, cv2.LINE_AA)
            cv2.putText(vis, tag, org, cv2.FONT_HERSHEY_SIMPLEX, 0.38 * s,
                        col, box_th, cv2.LINE_AA)

        # --- verdict: ID chip + the decoded word redrawn as symbols.  Placed
        # on the perpendicular of the chain so it never covers the marks.
        ang = math.radians(float(getattr(card, "angle", 0.0)))
        px, py = math.sin(ang), -math.cos(ang)
        if py > 0:                                  # keep the chip above
            px, py = -px, -py
        off = int(round(30 * s))
        note = f" {note_fn(card)}" if note_fn else ""
        label = f"{card.label} {card.status} s={card.score:.2f}{note}"
        box = text_chip(vis, label,
                        (card.cx + px * off - int(round(40 * s)),
                         card.cy + py * off), color, s)
        # the strip hangs directly under the chip it belongs to
        strip = draw_bit_strip(
            vis, card.bits,
            (box[0] + int(round(11 * s)), box[3] + int(round(7 * s))), color, s)
        if taken is not None:
            taken.extend((box, strip))

    if tint is not None:
        cv2.addWeighted(tint, tint_alpha, vis, 1.0 - tint_alpha, 0, dst=vis)
    return vis


def draw_read_summary(vis, cards, scale=None, org=None):
    """One line naming the IDs read this frame. Sits clear of the HUD line
    every runner prints at y=24."""
    s = overlay_scale(vis.shape) if scale is None else scale
    known = [c for c in cards if getattr(c, "code_id", None) is not None]
    ids = " ".join(f"ID{c.code_id:02d}" for c in known) or "-"
    text = f"read {len(known)}/{len(cards)}: {ids}"
    text_chip(vis, text, org or (10, 24 + int(round(26 * s))),
              (235, 235, 235), s)
    return vis
