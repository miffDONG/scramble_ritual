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
import threading
import time

import cv2

from . import __version__
from . import camera_options
from .camera import FileSource, list_cameras, open_camera
from .config import base_dir, bundled, load_app, load_camera, resolve_files
from .display import draw, no_image
from .recognizer import Recognizer, load_ids
from .tracker import Tracker
from .tuio import TuioSender


# reacTIVision 1.5.1's keys (its own help text), plus m (marker overlay). reacTIVision keys for
# things Ritualvision has no counterpart of (calibration c a d w x j k l q r, finger f,
# invert i, SPACE) do nothing.
# Letters also work with Caps Lock and with the Korean input mode on (2-beolsik jamo).
KEYS = {
    "source": ("s", "S", "ㄴ"),         # camera image
    "target": ("t", "T", "ㅅ"),         # thresholded image (the line mask)
    "none": ("n", "N", "ㅜ"),           # no image (less CPU)
    "gradient": ("g", "G", "ㅎ"),       # threshold settings: offset / window with the arrows
    "options": ("o", "O", "ㅐ"),        # camera options (driver dialog)
    "equalize": ("e", "E", "ㄷ"),
    "pause": ("p", "P", "ㅔ"),
    "verbose": ("v", "V", "ㅍ"),        # print the markers to the console
    "help": ("h", "H", "ㅗ"),
    "overlay": ("m", "M", "ㅡ"),        # Ritualvision only: marker drawings on/off
    "esc": ("\x1b",),                   # quit
}
KEYCODES = {code: name for name, chars in KEYS.items() for code in map(ord, chars)}
# function and arrow keys: waitKeyEx on Windows reports the virtual-key code << 16
SPECIAL_KEYS = {
    0x700000: "fullscreen",             # F1
    0x250000: "left", 0x260000: "up", 0x270000: "right", 0x280000: "down",
}


def key_action(k):
    """waitKeyEx code -> action name (None when the key means nothing here)."""
    if k < 0:
        return None
    if k in SPECIAL_KEYS:
        return SPECIAL_KEYS[k]
    return KEYCODES.get(k) or KEYCODES.get(k & 0xFFFF) or KEYCODES.get(k & 0xFF)


class Pipeline:
    """Newest camera frame -> recognizer -> tracker -> TUIO. With a preview window it runs on
    its own thread so drawing the window does not slow the tracking: recognition takes ~15 ms
    per 1280x800 frame and drawing ~3 ms more, which in one loop held 60 fps to ~52."""

    def __init__(self, src, rec, tracker, tuio, cam_cfg, file_source, log_fps, log):
        self.src, self.rec, self.tracker, self.tuio = src, rec, tracker, tuio
        self.cam_cfg, self.file_source, self.log_fps, self.log = cam_cfg, file_source, log_fps, log
        self.paused, self.verbose = False, False
        self.markers, self.mask, self.fps = [], None, 0.0
        self.count = 0                  # frames processed; the window redraws when it changes
        self._last_no, self._frames, self._t_fps, self._verbose_t = -1, 0, time.perf_counter(), 0.0
        self._lock = threading.Lock()
        self._result = None
        self._run, self._thread, self.error = False, None, None

    def step(self):
        """Process the newest frame; False when no new frame has arrived yet."""
        frame, no = self.src.read()
        if frame is None or (no == self._last_no and not self.file_source):
            return False
        self._last_no = no
        frame = camera_options.crop(frame, self.cam_cfg)
        if not self.paused:
            markers = self.rec.process(frame)
            self.markers, self.mask = markers, self.rec.mask
            H, W = frame.shape[:2]
            objs = self.tracker.update([(m.fid, m.x / W, m.y / H, math.radians(m.angle), m.size)
                                        for m in markers])
            self.tuio.send(objs, (W, H))
            if self.verbose and time.perf_counter() - self._verbose_t >= 0.1:   # at most 10 lines/s
                self._verbose_t = time.perf_counter()
                self.log("  ".join(f"id {o.fid} ({o.x:.3f},{o.y:.3f}) {math.degrees(o.a):.0f}deg"
                                   for o in objs) or "(no markers)")
        self._frames += 1
        now = time.perf_counter()
        if now - self._t_fps >= 1.0:
            self.fps = self._frames / (now - self._t_fps)
            self._frames, self._t_fps = 0, now
            if self.log_fps:
                self.log(f"{self.fps:5.1f} fps  markers {[m.fid for m in self.markers]}")
        with self._lock:
            self.count += 1
            self._result = (self.count, frame, self.mask, self.markers)
        return True

    def latest(self):
        """(count, frame, mask, markers) of the newest processed frame, or None."""
        if self.error is not None:
            raise self.error
        with self._lock:
            return self._result

    def start(self):
        self._run = True
        self._thread = threading.Thread(target=self._loop, name="recognize", daemon=True)
        self._thread.start()

    def _loop(self):
        try:
            while self._run:
                if not self.step():
                    time.sleep(0.001)
                elif self.file_source:
                    time.sleep(1 / 60)
        except Exception as e:          # re-raised on the main thread by latest()
            self.error = e

    def stop(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=1)


def parse_args(argv):
    p = argparse.ArgumentParser(prog="Ritualvision", description="Edge-dash fiducial tracker with TUIO 1.1 output")
    p.add_argument("-n", "--headless", action="store_true", help="no preview window")
    p.add_argument("-c", "--config-dir", help="folder with camera.xml / reacTIVision.xml (default: next to the exe)")
    p.add_argument("--source", help="image or video file instead of the camera")
    p.add_argument("--camera", type=int, help="camera index (overrides camera.xml)")
    p.add_argument("--driver", choices=("auto", "opencv", "ocam"),
                   help="camera driver (overrides camera.xml <camera driver>; default auto)")
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
        for i, backend, a, b in cams:
            log(f"oCam {i}: {a} {b}  (oCam SDK)" if backend == "oCam SDK"
                else f"camera {i}: {backend} {a}x{b}")
        if not cams:
            log("no camera found (OpenCV indexes 0-5, oCam SDK)")
        return 0
    cam_xml, app_xml, rv_xml = resolve_files(args.config_dir)
    cam_cfg = load_camera(cam_xml)
    app = load_app(app_xml, rv_xml)
    if args.camera is not None:
        cam_cfg.camera_id = args.camera
    if args.driver:
        cam_cfg.driver = args.driver
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
        try:
            src = open_camera(cam_cfg, log)
        except RuntimeError as e:
            log(f"ERROR: {e}. Check the camera index in camera.xml or pass --camera N.")
            return 2
        src.apply_settings(cam_cfg.settings, log)
        w, h, fps = src.actual_format()
        log(f"  camera  {src.name} ({src.backend}): {w}x{h} @ {fps} fps "
            f"(asked {cam_cfg.width}x{cam_cfg.height} @ {cam_cfg.fps})"
            + ("  mirrored left-right" if cam_cfg.mirror else ""))
    src.start()

    rec = Recognizer(ids, block=app.block, offset=app.offset, invert=app.invert, equalize=app.equalize,
                     min_arm=app.min_anchor_px, max_arm=app.max_anchor_px,
                     face_check=cam_cfg.face_check, valley=cam_cfg.valley)
    log(f"  lines   valley, {app.offset:g}% darker than the filled-in brightness" if cam_cfg.valley
        else f"  lines   local mean - {app.offset:g}, face check {'on' if cam_cfg.face_check else 'off'}")
    # size range from the exhibit server's contact measurement (ritualvision.xml)
    srv_range = app.size_range()
    size_status = (f"size {app.size_ref:.1f}px -{app.size_lo:g}%/+{app.size_hi:g}%"
                   if srv_range else "size range off")
    log(f"  size    {size_status}" + (f" ({srv_range[0]:.1f}..{srv_range[1]:.1f}px)" if srv_range else ""))
    rec.min_rc, rec.max_rc = srv_range if srv_range else (0.0, 0.0)
    tracker = Tracker(hold=app.hold_frames)
    tuio = TuioSender(app.tuio_host, app.tuio_port)
    headless = args.headless or args.once
    win = "Ritualvision"
    if not headless:
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    # display mode as in reacTIVision.xml <image display>: src | dest | none
    display = "dest" if args.mask else (app.display if app.display in ("src", "dest", "none") else "src")
    show_help, adjust, fullscreen = False, False, False
    overlay = True                      # m: ID / hexagon drawings over the image
    msg, msg_until = "", 0.0
    pending = None                      # key pressed while waiting for a camera frame
    pipe = Pipeline(src, rec, tracker, tuio, cam_cfg, bool(args.source),
                    headless and not args.once, log)
    if not headless:
        pipe.start()                    # recognition on its own thread, the window here
    shown = 0                           # pipe.count of the frame on screen

    try:
        while True:
            if headless:
                if not pipe.step():
                    time.sleep(0.001)
                    continue
                markers = pipe.markers
                if args.json or args.once:
                    print(json.dumps([{"id": m.fid, "code": "".join(map(str, m.code)), "x": round(m.x, 1),
                                       "y": round(m.y, 1), "angle": round(m.angle, 2), "size": round(m.size, 1)}
                                      for m in markers]), flush=True)
                if args.once:
                    break
                if args.source:
                    time.sleep(1 / 60)
                continue

            res = pipe.latest()
            if res is None or res[0] == shown:
                # no new frame yet: keep the key for the next frame instead of dropping it
                k = key_action(cv2.waitKeyEx(1))
                if k == "esc":
                    break
                pending = k or pending
                continue
            shown, frame, mask, markers = res
            fps, now = pipe.fps, time.perf_counter()
            if now > msg_until:
                msg = ""
            lines = ()
            if adjust:                                   # g: threshold settings
                lines = ("threshold settings  (g to close)",
                         f"offset {rec.offset:.0f}   up / down",
                         f"window {rec.block}   left / right")
            if display == "none":
                img = no_image(fps, markers, show_help, msg, lines)
            else:
                img = draw(frame, mask, markers, display == "dest", fps, rec.offset, show_help, msg,
                           rec.block, size_status, rec.last_noise, lines, overlay)
            cv2.imshow(win, img)
            act = key_action(cv2.waitKeyEx(1)) or pending
            pending = None
            if act == "esc" or cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                break
            if act in ("source", "target", "none"):
                display = {"source": "src", "target": "dest", "none": "none"}[act]
            elif act == "gradient":
                adjust = not adjust
            elif adjust and act in ("up", "down"):
                rec.offset = min(80.0, max(1.0, rec.offset + (1 if act == "up" else -1)))
            elif adjust and act in ("left", "right"):
                rec.block = min(101, max(3, (rec.block or 15) + (2 if act == "right" else -2)))
            elif act == "options" and hasattr(src, "show_driver_dialog"):
                if not src.show_driver_dialog():
                    msg, msg_until = "no camera options for this camera", now + 2.0
            elif act == "equalize":
                rec.equalize = not rec.equalize
                msg, msg_until = f"equalize {'on' if rec.equalize else 'off'}", now + 1.5
            elif act == "pause":
                pipe.paused = not pipe.paused
            elif act == "verbose":
                pipe.verbose = not pipe.verbose
                msg, msg_until = f"verbose {'on' if pipe.verbose else 'off'}", now + 1.5
            elif act == "fullscreen":
                fullscreen = not fullscreen
                cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN,
                                      cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
            elif act == "help":
                show_help = not show_help
            elif act == "overlay":
                overlay = not overlay
                msg, msg_until = f"marker overlay {'on' if overlay else 'off'}", now + 1.5
    except KeyboardInterrupt:
        pass
    finally:
        pipe.stop()
        tuio.send([])           # empty alive: receivers drop every object
        src.stop()
        tuio.close()
        if not headless:
            cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
