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
    """<frame width height xoff yoff>: "max" means the whole capture."""
    h, w = frame.shape[:2]
    fw = w if str(cfg.frame_width) == "max" else min(int(cfg.frame_width), w)
    fh = h if str(cfg.frame_height) == "max" else min(int(cfg.frame_height), h)
    x0 = max(0, min(cfg.xoff, w - fw))
    y0 = max(0, min(cfg.yoff, h - fh))
    if fw == w and fh == h:
        return frame
    return frame[y0:y0 + fh, x0:x0 + fw]
