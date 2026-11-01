"""Ritualvision entry point.

  Ritualvision.exe                 camera from camera.xml, window, TUIO to reacTIVision.xml host:port
  Ritualvision.exe -n              no window (as the exhibit server launches reacTIVision)
  Ritualvision.exe --source F      image or video file instead of the camera
  Ritualvision.exe --source F --once --json   decode one frame, print the markers, exit
"""

import argparse
import json
import math
import os
import sys
import time

import cv2

from . import __version__
from . import camera_options
from .camera import CameraSource, FileSource, list_cameras
from .config import base_dir, bundled, load_app, load_camera, resolve_files
from .display import draw
from .recognizer import Recognizer, load_ids
from .size_gate import SizeGate
from .tracker import Tracker
from .tuio import TuioSender


# keys work with Caps Lock and with the Korean input mode on (2-beolsik jamo for the same key)
KEYS = {
    "mask": ("t", "T", "ㅅ", "\t"),
    "camera_view": ("1",),
    "mask_view": ("2",),
    "help": ("h", "H", "ㅗ"),
    "pause": ("p", "P", "ㅔ"),
    "snap": ("s", "S", "ㄴ"),
    "driver": ("c", "C", "ㅊ"),
    "size": ("r", "R", "ㄱ"),
    "quit": ("q", "Q", "ㅂ"),
    "esc": ("\x1b",),
    "off_down": ("[",), "off_up": ("]",), "win_down": (",",), "win_up": (".",),
}
KEYCODES = {code: name for name, chars in KEYS.items() for code in map(ord, chars)}


def key_action(k):
    """waitKeyEx code -> action name (None when the key means nothing here)."""
    if k < 0:
        return None
    return KEYCODES.get(k) or KEYCODES.get(k & 0xFFFF) or KEYCODES.get(k & 0xFF)


def parse_args(argv):
    p = argparse.ArgumentParser(prog="Ritualvision", description="Edge-dash fiducial tracker with TUIO 1.1 output")
    p.add_argument("-n", "--headless", action="store_true", help="no preview window")
    p.add_argument("-c", "--config-dir", help="folder with camera.xml / reacTIVision.xml (default: next to the exe)")
    p.add_argument("--source", help="image or video file instead of the camera")
    p.add_argument("--camera", type=int, help="camera index (overrides camera.xml)")
    p.add_argument("--port", type=int, help="TUIO port (overrides the xml)")
    p.add_argument("--ids", help="id table json (default ids.json next to the exe)")
    p.add_argument("--offset", type=float, help="line threshold offset (default 10)")
    p.add_argument("--once", action="store_true", help="process one frame and exit")
    p.add_argument("--json", action="store_true", help="print detections as JSON lines")
    p.add_argument("--list-cameras", action="store_true", help="probe camera indexes 0-5 and exit")
    p.add_argument("--mask", action="store_true", help="start in the line-mask (binarized) view")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    log = lambda *a: print(*a, flush=True)
    if args.list_cameras:
        cams = list_cameras()
        for i, backend, w, h in cams:
            log(f"camera {i}: {backend} {w}x{h}")
        if not cams:
            log("no camera found (indexes 0-5)")
        return 0
    cam_xml, app_xml, rv_xml = resolve_files(args.config_dir)
    cam_cfg = load_camera(cam_xml)
    app = load_app(app_xml, rv_xml)
    if args.camera is not None:
        cam_cfg.camera_id = args.camera
    if args.port:
        app.tuio_port = args.port
    if args.offset is not None:
        app.offset = args.offset
    ids_path = args.ids or os.path.join(args.config_dir or base_dir(), app.ids_file)
    if not os.path.exists(ids_path):
        ids_path = bundled("ids.json")
    ids = load_ids(ids_path)
    log(f"Ritualvision {__version__}")
    log(f"  config  {cam_xml} | {app_xml}" + (f" + {rv_xml}" if os.path.exists(rv_xml) else ""))
    log(f"  ids     {ids_path} ({len(ids)} markers)")
    log(f"  TUIO    udp://{app.tuio_host}:{app.tuio_port} /tuio/2Dobj")

    if args.source:
        src = FileSource(args.source)
    else:
        src = CameraSource(cam_cfg)
        try:
            src.open()
        except RuntimeError as e:
            log(f"ERROR: {e}. Check the camera index in camera.xml or pass --camera N.")
            return 2
        camera_options.apply_settings(src.cap, cam_cfg.settings, log)
        w, h, fps = src.actual_format()
        log(f"  camera  {cam_cfg.camera_id} ({src.backend}): {w}x{h} @ {fps} fps "
            f"(asked {cam_cfg.width}x{cam_cfg.height} @ {cam_cfg.fps})")
    src.start()

    rec = Recognizer(ids, block=app.block, offset=app.offset, invert=app.invert, equalize=app.equalize,
                     min_arm=app.min_anchor_px, max_arm=app.max_anchor_px)
    gate = SizeGate(os.path.join(args.config_dir or base_dir(), "ritualvision_state.json"))
    log(f"  size    {gate.status()}")
    tracker = Tracker(hold=app.hold_frames)
    tuio = TuioSender(app.tuio_host, app.tuio_port)
    headless = args.headless or args.once or app.display == "none"
    win = "Ritualvision"
    if not headless:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    show_mask = app.display == "dest" or args.mask
    show_help, paused, msg, msg_until = True, False, "", 0.0
    last_no, frames, t_fps, fps = -1, 0, time.perf_counter(), 0.0
    snap_dir = os.path.join(args.config_dir or base_dir(), "snapshots")

    try:
        while True:
            frame, no = src.read()
            if frame is None or (no == last_no and not args.source):
                if not headless:
                    act = key_action(cv2.waitKeyEx(1))
                    if act == "quit" or (act == "esc" and gate.state != "registering"):
                        break
                time.sleep(0.001)
                continue
            last_no = no
            frame = camera_options.crop(frame, cam_cfg)
            if not paused:
                rec.min_rc = gate.min_rc
                markers = rec.process(frame)
                gate.observe(markers)
                H, W = frame.shape[:2]
                objs = tracker.update([(m.fid, m.x / W, m.y / H, math.radians(m.angle)) for m in markers])
                tuio.send(objs)
            frames += 1
            now = time.perf_counter()
            if now - t_fps >= 1.0:
                fps = frames / (now - t_fps)
                frames, t_fps = 0, now
                if headless and not args.once:
                    log(f"{fps:5.1f} fps  markers {[m.fid for m in markers]}")
            if args.json or args.once:
                print(json.dumps([{"id": m.fid, "code": "".join(map(str, m.code)), "x": round(m.x, 1),
                                   "y": round(m.y, 1), "angle": round(m.angle, 2), "size": round(m.size, 1)}
                                  for m in markers]), flush=True)
            if args.once:
                break
            if not headless:
                if now > msg_until:
                    msg = ""
                view = lambda as_mask, hlp=False, m="": draw(
                    frame, rec.mask, markers, as_mask, fps, rec.offset, hlp, m, rec.block,
                    gate.status(), rec.last_noise, gate.prompt(markers))
                img = view(show_mask, show_help, msg)
                cv2.imshow(win, img)
                act = key_action(cv2.waitKeyEx(1))
                if act == "esc" and gate.state == "registering":
                    msg, msg_until = gate.cancel(), now + 2.0
                    act = None
                if act in ("quit", "esc") or cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                    break
                if act == "size":
                    msg, msg_until = gate.press_r(), now + 3.0
                elif act in ("mask", "camera_view", "mask_view"):
                    show_mask = (not show_mask) if act == "mask" else (act == "mask_view")
                    msg, msg_until = ("view: line mask" if show_mask else "view: camera"), now + 1.5
                elif act == "help":
                    show_help = not show_help
                elif act == "pause":
                    paused = not paused
                elif act == "off_down":
                    rec.offset = max(1.0, rec.offset - 1)
                elif act == "off_up":
                    rec.offset = min(80.0, rec.offset + 1)
                elif act == "win_down":
                    rec.block = max(3, (rec.block or 15) - 2)
                elif act == "win_up":
                    rec.block = min(101, (rec.block or 15) + 2)
                elif act == "driver" and isinstance(src, CameraSource):
                    src.show_driver_dialog()
                elif act == "snap":
                    os.makedirs(snap_dir, exist_ok=True)
                    stamp = time.strftime("%Y%m%d_%H%M%S")
                    cv2.imwrite(os.path.join(snap_dir, f"{stamp}_frame.png"), frame)
                    cv2.imwrite(os.path.join(snap_dir, f"{stamp}_view.png"), view(False))
                    cv2.imwrite(os.path.join(snap_dir, f"{stamp}_mask.png"), view(True))
                    msg, msg_until = f"saved snapshots/{stamp} (frame, view, mask)", now + 2.0
            if args.source and not args.once:
                time.sleep(1 / 60)
    except KeyboardInterrupt:
        pass
    finally:
        tuio.send([])           # empty alive: receivers drop every object
        src.stop()
        tuio.close()
        if not headless:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
