"""Built-in exhibit profile (IR-lit table): the camera and line settings Ritualvision needs,
kept in the program so every copy of Ritualvision.exe behaves the same, wherever it runs and
whatever camera.xml (written by the exhibit server or left by reacTIVision) sits next to it.

Measured on the exhibit table (IR filter, IR LED strips, USB camera 0C45:636D), FIXLOG 11:
  1280x720 @ 60  at 1280x800 this camera only runs 120 / 30 / 15 fps (any request from 46 to
              119 opens 120). 120 caps the exposure at about 1/128 s and the image is dark;
              1280x720 has a real 60 fps mode, ~1.9x brighter than 120 (30 fps would be
              ~2.8x, FIXLOG 11-12). 720 is the middle of the 800-row sensor, not a rescale:
              same marker size, every y 40 px up. pad_y = 40 puts those 40 rows back in the
              coordinates, so TUIO and camera.xml <frame> stay in 1280x800 terms.
              reacTIVision looked brighter for the same reason: its camera.xml asks for
              uncompressed YUY2, which this camera only gives at 10 fps.
  exposure -5 with gain / brightness / contrast / gamma / sharpness at the driver defaults: written
              every start, because DirectShow keeps whatever the last program set ("default"
              in camera.xml means "leave it"), so the image depended on what ran before.
  valley, no face check, mirror: see recognizer.Recognizer and FIXLOG 5, 8, 9.
  edge_band 40, dash_outer_edge 1.0: a tile at the image's left / right border is squeezed
              and blurred by the lens; a lower line threshold in a 40 px band and dashes
              searched out to 1.0 rc for such a tile (FIXLOG 13).

camera.xml still gives the camera id and the <frame> region (in 1280x800 camera pixels). Run with
--profile none to use camera.xml (and its <settings>) alone, as before.
"""

EXHIBIT = {
    "width": 1280,
    "height": 720,
    "pad_y": 40,
    "fps": 60,
    "compress": True,                       # MJPG
    "settings": {"exposure": -5, "gain": 15, "brightness": 0, "contrast": 32, "gamma": 100,
                 "sharpness": 3},
    "mirror": True,
    "face_check": False,
    "valley": True,
    "edge_band": 40,
    "dash_outer_edge": 1.0,
}

PROFILES = {"exhibit": EXHIBIT, "none": None}


def apply(cam_cfg, name="exhibit", log=print):
    """Override camera.xml values in `cam_cfg` with the named profile; logs what changed."""
    prof = PROFILES.get(name)
    if prof is None:
        log("  profile none (camera.xml only)")
        return cam_cfg
    changed = []
    for key in ("width", "height", "pad_y", "fps", "compress", "mirror", "face_check", "valley",
                "edge_band", "dash_outer_edge"):
        old = getattr(cam_cfg, key)
        if old != prof[key]:
            changed.append(f"{key} {old}->{prof[key]}")
        setattr(cam_cfg, key, prof[key])
    for key, val in prof["settings"].items():
        old = cam_cfg.settings.get(key, "default")
        if str(old).strip().lower() != str(val):
            changed.append(f"{key} {old}->{val}")
        cam_cfg.settings[key] = val
    log(f"  profile {name}" + (f" (over camera.xml: {', '.join(changed)})" if changed else ""))
    return cam_cfg
