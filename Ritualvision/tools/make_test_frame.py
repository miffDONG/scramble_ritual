"""Synthesize an IR-camera-like test frame from the marker PNGs, with ground truth.

  python tools/make_test_frame.py --markers <folder with id_XX_*.png> --out test.png [--tile 130]

Dark table, grey tiles, uneven light, blur and sensor noise; a few tiles touch each other.
Writes test.png and test.truth.json ([{id, x, y, angle}]).
"""

import argparse
import glob
import json
import math
import os
import random

import cv2
import numpy as np


def load_markers(folder):
    out = {}
    for p in glob.glob(os.path.join(folder, "id_*.png")):
        fid = int(os.path.basename(p).split("_")[1])
        out[fid] = cv2.imread(p, cv2.IMREAD_UNCHANGED)
    return out


def warp(rgba, cx, cy, size, angle, shape):
    """angle: clockwise degrees in image coordinates (Ritualvision convention)."""
    s = size / rgba.shape[0]
    M = cv2.getRotationMatrix2D((rgba.shape[1] / 2, rgba.shape[0] / 2), -angle, s)
    M[0, 2] += cx - rgba.shape[1] / 2
    M[1, 2] += cy - rgba.shape[0] / 2
    h, w = shape
    return cv2.warpAffine(rgba, M, (w, h), flags=cv2.INTER_AREA, borderValue=(0, 0, 0, 0))


def paste(canvas, rgba, cx, cy, size, angle):
    warped = warp(rgba, cx, cy, size, angle, canvas.shape)
    alpha = warped[:, :, 3].astype(np.float32) / 255
    ink = (warped[:, :, 0].astype(np.float32) < 128)          # black lines
    tile_val = np.where(ink, 55.0, 205.0)
    canvas[:] = canvas * (1 - alpha) + tile_val * alpha


def make(markers, ids, tile, seed=0, w=1280, h=800):
    """Tiles never overlap (they are physical objects) but many touch a neighbour."""
    rng = random.Random(seed)
    canvas = np.full((h, w), 38.0, np.float32)
    occupied = np.zeros((h, w), bool)
    truth = []
    cols = 6
    for k, fid in enumerate(ids):
        r, c = divmod(k, cols)
        gx = 110 + c * tile * 1.12 + (tile * 0.56 if r % 2 else 0)
        gy = 100 + r * tile * 1.02
        for attempt in range(200):
            cx = gx + rng.uniform(-30, 30)
            cy = gy + rng.uniform(-30, 30)
            ang = rng.uniform(0, 360)
            foot = warp(markers[fid], cx, cy, tile, ang, (h, w))[:, :, 3] > 0
            if not (foot & occupied).any() and 0.6 * tile < cx < w - 0.6 * tile and 0.6 * tile < cy < h - 0.6 * tile:
                break
        occupied |= foot
        paste(canvas, markers[fid], cx, cy, tile, ang)
        truth.append({"id": fid, "x": cx, "y": cy, "angle": ang})
    yy, xx = np.mgrid[0:h, 0:w]
    light = 0.75 + 0.35 * np.exp(-(((xx - w * 0.4) / (w * 0.6)) ** 2 + ((yy - h * 0.45) / (h * 0.7)) ** 2))
    img = canvas * light
    img = cv2.GaussianBlur(img, (0, 0), 0.9)
    img += np.random.default_rng(seed).normal(0, 6, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8), truth


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--markers", required=True)
    ap.add_argument("--out", default="test.png")
    ap.add_argument("--tile", type=float, default=130, help="tile height in pixels (vertex to vertex)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    mk = load_markers(a.markers)
    img, truth = make(mk, sorted(mk), a.tile, a.seed)
    cv2.imwrite(a.out, img)
    json.dump(truth, open(os.path.splitext(a.out)[0] + ".truth.json", "w"), indent=1)
    print(a.out, len(truth), "markers")
