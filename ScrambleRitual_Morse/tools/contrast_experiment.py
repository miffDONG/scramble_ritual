"""Batch contrast/noise experiment for the July 25 installation snapshots.

Outputs reproducible enhanced frames, binary masks, contact sheets, CSV metrics,
and an HTML gallery.  No hand-picked ROI is used: every method sees the same
full-resolution frame and the same final adaptive threshold/morphology stage.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
import sys

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tracker.morse import detect_cards, generate_codebook


METHODS = (
    "baseline",
    "autocontrast",
    "gamma",
    "clahe",
    "flatfield_clahe",
    "retinex_clahe",
)


def odd(value: int, minimum: int = 3) -> int:
    value = max(minimum, int(value))
    return value if value % 2 else value + 1


def percentile_stretch(gray: np.ndarray, lo=1.0, hi=99.0) -> np.ndarray:
    a, b = np.percentile(gray[::2, ::2], (lo, hi))
    if b <= a + 1:
        return gray.copy()
    return np.clip((gray.astype(np.float32) - a) * 255.0 / (b - a), 0, 255).astype(np.uint8)


def gamma_auto(gray: np.ndarray, target=150.0) -> np.ndarray:
    median = max(float(np.median(gray)), 1.0) / 255.0
    gamma = np.log(target / 255.0) / np.log(median)
    gamma = float(np.clip(gamma, 0.45, 1.8))
    lut = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)], np.uint8)
    return cv2.LUT(gray, lut)


def enhance(gray: np.ndarray, method: str) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(12, 12))
    if method == "baseline":
        return gray.copy()
    if method == "autocontrast":
        return percentile_stretch(gray)
    if method == "gamma":
        return percentile_stretch(gamma_auto(gray), 0.5, 99.5)
    if method == "clahe":
        return clahe.apply(gray)

    # Large-scale illumination estimate: ~12.5% of the short image side.
    k = odd(min(gray.shape) // 8, 101)
    bg = cv2.GaussianBlur(gray, (k, k), 0)
    if method == "flatfield_clahe":
        flat = cv2.divide(gray, cv2.max(bg, 1), scale=180)
        return clahe.apply(percentile_stretch(flat, 0.5, 99.5))
    if method == "retinex_clahe":
        g = gray.astype(np.float32) + 1.0
        b = bg.astype(np.float32) + 1.0
        retinex = np.log(g) - np.log(b)
        retinex = cv2.normalize(retinex, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        # Mild bilateral denoising preserves dot/dash boundaries.
        return clahe.apply(cv2.bilateralFilter(retinex, 7, 28, 28))
    raise ValueError(method)


def make_mask(enhanced: np.ndarray) -> np.ndarray:
    # Local threshold tolerates the remaining light-table bands. Dark glyphs
    # become white. A small opening rejects sensor grain; close repairs ink.
    block = odd(min(enhanced.shape) // 18, 51)
    denoised = cv2.bilateralFilter(enhanced, 5, 22, 22)
    mask = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, block, 9,
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return mask


def metrics(image: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    h, w = image.shape
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    areas = stats[1:, cv2.CC_STAT_AREA] if n > 1 else np.array([], dtype=int)
    plausible = areas[(areas >= 12) & (areas <= h * w * 0.002)]
    specks = areas[(areas > 0) & (areas < 12)]
    local = image.astype(np.float32) - cv2.GaussianBlur(image, (0, 0), 9)
    hist = np.bincount(image.ravel(), minlength=256).astype(np.float64)
    p = hist[hist > 0] / hist.sum()
    entropy = float(-(p * np.log2(p)).sum())
    fg = float(np.count_nonzero(mask) / mask.size)
    # Heuristic only: rewards usable local separation while penalizing a mask
    # flooded by bands/edges and tiny isolated components.
    score = float(local.std() / (1.0 + 18.0 * fg + len(specks) / 400.0))
    return {
        "entropy": entropy,
        "local_contrast": float(local.std()),
        "foreground_ratio": fg,
        "components": int(len(areas)),
        "plausible_components": int(len(plausible)),
        "tiny_specks": int(len(specks)),
        "review_score": score,
    }


def roi_to_px(roi, w: int, h: int):
    if not roi or len(roi) != 4:
        return None
    x1, y1, x2, y2 = roi
    return (round(x1 * w), round(y1 * h), round(x2 * w), round(y2 * h))


def run_project_decoder(gray: np.ndarray, project_cfg: dict):
    """Run the same tracker.morse detector/config used by scripts/start.bat."""
    morse_cfg = dict(project_cfg["morse"])
    runtime = project_cfg.get("webui", {})
    frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    h, w = frame.shape[:2]
    maxd = int(runtime.get("proc_max_dim", 1280))
    if max(w, h) > maxd:
        scale = maxd / max(w, h)
        frame = cv2.resize(frame, (round(w * scale), round(h * scale)),
                           interpolation=cv2.INTER_AREA)
        h, w = frame.shape[:2]
    roi = roi_to_px(runtime.get("roi"), w, h)
    det_frame = frame
    if roi:
        x1, y1, x2, y2 = roi
        fill = 255 if morse_cfg.get("dark_on_light", True) else 0
        det_frame = np.full_like(frame, fill)
        det_frame[y1:y2, x1:x2] = frame[y1:y2, x1:x2]
    codebook = None
    if morse_cfg.get("decode_mode", "codebook") == "codebook":
        codebook = generate_codebook(
            int(morse_cfg["code_count"]), int(morse_cfg["slots"]),
            int(morse_cfg["min_hamming"]))
    _, cards, glyphs = detect_cards(det_frame, morse_cfg, codebook)
    if roi:
        x1, y1, x2, y2 = roi
        cards = [c for c in cards if x1 <= c.cx <= x2 and y1 <= c.cy <= y2]
        glyphs = [g for g in glyphs if x1 <= g.cx <= x2 and y1 <= g.cy <= y2]
    return frame, cards, glyphs, roi


def decoder_overlay(frame, cards, glyphs, roi) -> np.ndarray:
    vis = frame.copy()
    tint = vis.copy()
    if roi:
        cv2.rectangle(vis, roi[:2], roi[2:], (255, 180, 0), 2)
    for g in glyphs:
        x, y, w, h = g.bbox
        cv2.rectangle(vis, (x, y), (x + w, y + h), (80, 80, 80), 1)
    palette = [(0, 230, 255), (255, 80, 220), (70, 240, 70),
               (255, 170, 40), (60, 100, 255)]
    for card_i, card in enumerate(cards):
        color = palette[card_i % len(palette)]
        pts = [(round(s.cx), round(s.cy)) for s in card.slots]
        if len(pts) >= 2:
            hull_pts = []
            for slot in card.slots:
                x, y, w, h = slot.glyph.bbox
                pad = 5
                hull_pts.extend([(x - pad, y - pad), (x + w + pad, y - pad),
                                 (x + w + pad, y + h + pad), (x - pad, y + h + pad)])
            hull = cv2.convexHull(np.array(hull_pts, np.int32))
            cv2.fillConvexPoly(tint, hull, color)
            cv2.polylines(vis, [np.array(pts, np.int32)], False, color, 3,
                          cv2.LINE_AA)
        for slot in card.slots:
            x, y, w, h = slot.glyph.bbox
            cv2.rectangle(vis, (x, y), (x + w, y + h), color, 2)
            cv2.circle(vis, (round(slot.cx), round(slot.cy)), 4, color, -1,
                       cv2.LINE_AA)
            tag = f"{slot.index + 1}:{slot.bit}"
            tag_y = max(13, y - 5)
            cv2.putText(vis, tag, (x, tag_y), cv2.FONT_HERSHEY_SIMPLEX,
                        .42, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(vis, tag, (x, tag_y), cv2.FONT_HERSHEY_SIMPLEX,
                        .42, color, 1, cv2.LINE_AA)
        label = f"ID={card.code_id or '?'} bits={card.bits} {card.status} s={card.score:.2f}"
        org = (max(8, round(card.cx) - 100), max(28, round(card.cy) - 24))
        cv2.putText(vis, label, org, cv2.FONT_HERSHEY_SIMPLEX, .58,
                    (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(vis, label, org, cv2.FONT_HERSHEY_SIMPLEX, .58,
                    color, 2, cv2.LINE_AA)
    return cv2.addWeighted(tint, 0.14, vis, 0.86, 0)


def label_panel(image: np.ndarray, label: str) -> np.ndarray:
    shown = (cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
             if image.ndim == 2 else image.copy())
    cv2.rectangle(shown, (0, 0), (430, 44), (18, 18, 18), -1)
    cv2.putText(shown, label, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                0.72, (255, 255, 255), 2, cv2.LINE_AA)
    return shown


def contact_sheet(panels: list[np.ndarray], width=640) -> np.ndarray:
    resized = []
    for panel in panels:
        scale = width / panel.shape[1]
        resized.append(cv2.resize(panel, (width, round(panel.shape[0] * scale)),
                                  interpolation=cv2.INTER_AREA))
    rows = []
    for i in range(0, len(resized), 2):
        row = resized[i:i + 2]
        if len(row) == 1:
            row.append(np.zeros_like(row[0]))
        rows.append(np.hstack(row))
    return np.vstack(rows)


def write_html(out: Path, image_names: list[str], rows: list[dict]) -> None:
    ranked = sorted(rows, key=lambda r: float(r["review_score"]), reverse=True)
    trs = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(str(r[k]))}</td>" for k in
        ("source", "method", "detected_cards", "object_ids", "bits",
         "statuses", "card_scores", "local_contrast", "foreground_ratio",
         "review_score")) + "</tr>"
        for r in ranked
    )
    galleries = "\n".join(
        f'<section><h2>{html.escape(name)}</h2>'
        f'<img src="sheets/{html.escape(Path(name).stem)}_enhanced.jpg">'
        f'<img src="sheets/{html.escape(Path(name).stem)}_masks.jpg">'
        f'<img src="sheets/{html.escape(Path(name).stem)}_decoded.jpg"></section>'
        for name in image_names
    )
    doc = f"""<!doctype html><meta charset="utf-8"><title>Morse contrast experiment</title>
<style>body{{font:14px system-ui;margin:24px;background:#171717;color:#eee}}img{{max-width:100%;display:block;margin:10px 0 28px}}table{{border-collapse:collapse}}td,th{{padding:6px 9px;border:1px solid #555}}th{{position:sticky;top:0;background:#222}}small{{color:#bbb}}</style>
<h1>Morse snapshot contrast experiment</h1>
<p>Each enhanced, mask, and decoded sheet uses identical ordering. In decoded overlays, cyan is the configured table ROI, gray boxes are all glyph candidates, and each colored band/line is one selected 8-symbol chain. Slot tags use <code>order:bit</code>; the chain label shows ID, full bits, decode status, and score. ID/bits/status come from the same tracker.morse detector and tracker/config.json used by scripts/start.bat. A question mark means a chain was found but did not decode to the protected codebook.</p>
<table><thead><tr><th>source</th><th>method</th><th>cards</th><th>object IDs</th><th>bits</th><th>status</th><th>card scores</th><th>local contrast</th><th>foreground ratio</th><th>review score</th></tr></thead><tbody>{trs}</tbody></table>{galleries}"""
    (out / "index.html").write_text(doc, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out
    for sub in ("enhanced", "masks", "decoded", "sheets"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    cfg_path = Path(__file__).resolve().parents[1] / "tracker" / "config.json"
    project_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    rows: list[dict] = []
    names = []
    for path in args.inputs:
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if frame is None:
            raise SystemExit(f"cannot read {path}")
        names.append(path.name)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        enhanced_panels, mask_panels, decoded_panels = [], [], []
        for method in METHODS:
            result = enhance(gray, method)
            mask = make_mask(result)
            stem = f"{path.stem}__{method}"
            cv2.imwrite(str(out / "enhanced" / f"{stem}.png"), result)
            cv2.imwrite(str(out / "masks" / f"{stem}.png"), mask)
            m = metrics(result, mask)
            proc_frame, cards, glyphs, roi = run_project_decoder(result, project_cfg)
            ids = [str(c.code_id) if c.code_id is not None else "?" for c in cards]
            row = {
                "source": path.name,
                "method": method,
                "detected_cards": len(cards),
                "object_ids": " | ".join(ids),
                "bits": " | ".join(c.bits for c in cards),
                "statuses": " | ".join(c.status for c in cards),
                "card_scores": " | ".join(f"{c.score:.3f}" for c in cards),
                **m,
            }
            rows.append(row)
            id_text = ",".join(ids) if ids else "none"
            label = f"{method}  IDs={id_text}  chains={len(cards)}"
            enhanced_panels.append(label_panel(result, label))
            mask_panels.append(label_panel(mask, label))
            overlay = decoder_overlay(proc_frame, cards, glyphs, roi)
            cv2.imwrite(str(out / "decoded" / f"{stem}.jpg"), overlay,
                        [cv2.IMWRITE_JPEG_QUALITY, 92])
            decoded_panels.append(label_panel(overlay, label))
        cv2.imwrite(str(out / "sheets" / f"{path.stem}_enhanced.jpg"),
                    contact_sheet(enhanced_panels), [cv2.IMWRITE_JPEG_QUALITY, 91])
        cv2.imwrite(str(out / "sheets" / f"{path.stem}_masks.jpg"),
                    contact_sheet(mask_panels), [cv2.IMWRITE_JPEG_QUALITY, 91])
        cv2.imwrite(str(out / "sheets" / f"{path.stem}_decoded.jpg"),
                    contact_sheet(decoded_panels), [cv2.IMWRITE_JPEG_QUALITY, 91])

    fields = ["source", "method", "detected_cards", "object_ids", "bits",
              "statuses", "card_scores", "entropy", "local_contrast",
              "foreground_ratio", "components", "plausible_components",
              "tiny_specks", "review_score"]
    with (out / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    write_html(out, names, rows)
    print(f"Wrote {len(rows)} variants to {out}")


if __name__ == "__main__":
    main()
