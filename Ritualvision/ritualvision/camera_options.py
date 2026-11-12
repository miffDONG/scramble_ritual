"""카메라 옵션 설정부 (camera options).

Applies camera.xml <settings> to the driver and crops the <frame> region of interest.
Values follow reacTIVision: "default" leaves the driver value, "auto" enables the automatic
mode where the driver has one, "min" / "max" or a number set it.
"""

import cv2

PROPS = {
    "brightness": cv2.CAP_PROP_BRIGHTNESS,
    "contrast": cv2.CAP_PROP_CONTRAST,
    "gain": cv2.CAP_PROP_GAIN,
    "exposure": cv2.CAP_PROP_EXPOSURE,
    "shutter": cv2.CAP_PROP_EXPOSURE,
    "sharpness": cv2.CAP_PROP_SHARPNESS,
    "gamma": cv2.CAP_PROP_GAMMA,
    "focus": cv2.CAP_PROP_FOCUS,
    "white": cv2.CAP_PROP_WB_TEMPERATURE,
}
# DirectShow has no min/max query through OpenCV; these are the usual UVC ranges
EXTREMES = {"exposure": (-13, -1), "shutter": (-13, -1), "focus": (0, 255)}


def apply_settings(cap, settings, log=print):
    applied = {}
    for key, raw in settings.items():
        prop = PROPS.get(key)
        val = str(raw).strip().lower()
        if prop is None or val in ("", "default"):
            continue
        if val == "auto":
            if key in ("exposure", "shutter"):
                ok = cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)
            elif key == "focus":
                ok = cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
            elif key == "white":
                ok = cap.set(cv2.CAP_PROP_AUTO_WB, 1)
            else:
                continue
            applied[key] = ("auto", ok)
            continue
        if val in ("min", "max"):
            lo, hi = EXTREMES.get(key, (0, 255))
            num = lo if val == "min" else hi
        else:
            try:
                num = float(val)
            except ValueError:
                log(f"[camera] ignoring {key}={raw!r}")
                continue
        if key in ("exposure", "shutter"):
            cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)       # manual
        if key == "focus":
            cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
        ok = cap.set(prop, num)
        applied[key] = (num, ok, cap.get(prop))
    for k, v in applied.items():
        log(f"[camera] {k}: {v}")
    return applied


def crop(frame, cfg):
    """<frame width height xoff yoff> ("max" means the whole capture, offsets in camera
    pixels), then <settings mirror="true"> flips the result left-right.

    Returns (frame, (ox, oy, W, H)): a point (x, y) of the returned frame is at
    ((x + ox) / W, (y + oy) / H) of the <frame> region, the TUIO coordinates. With cfg.pad_y
    the capture is the middle of a frame 2 * pad_y rows taller (camera.xml coordinates): the
    region is laid on that taller frame and the missing rows count in H, so TUIO y keeps its
    meaning whichever size the camera sends."""
    h, w = frame.shape[:2]
    pad = int(getattr(cfg, "pad_y", 0) or 0)
    full_h = h + 2 * pad
    fw = w if str(cfg.frame_width) == "max" else min(int(cfg.frame_width), w)
    fh = full_h if str(cfg.frame_height) == "max" else min(int(cfg.frame_height), full_h)
    x0 = max(0, min(cfg.xoff, w - fw))
    y0 = max(0, min(cfg.yoff, full_h - fh))
    r0, r1 = max(0, y0 - pad), min(h, y0 + fh - pad)       # rows of the region the camera sent
    if fw != w or r0 != 0 or r1 != h:
        frame = frame[r0:r1, x0:x0 + fw]
    space = (0, r0 + pad - y0, fw, fh)
    return (cv2.flip(frame, 1) if getattr(cfg, "mirror", False) else frame), space
