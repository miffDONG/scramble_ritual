"""카메라 리소스 연결부 (camera resource).

Opens a camera (DirectShow on Windows, like reacTIVision) or an image / video file, and grabs
frames on a background thread so the recognizer always works on the newest frame.

camera.xml <camera driver="...">, or --driver:
  auto    (default, also when the attribute is missing) OpenCV first; when that camera gives no
          frame, a Withrobot oCam through its own SDK (ocam.py). Mono oCams need this.
  opencv  DirectShow / Media Foundation only
  ocam    oCam SDK only; <camera id> is then the oCam index
"""

import os
import sys
import threading
import time

import cv2

from . import camera_options, ocam


def _backends():
    # DirectShow first (what reacTIVision uses on Windows, exposes the driver settings page),
    # Media Foundation as fallback
    if sys.platform.startswith("win"):
        return (cv2.CAP_DSHOW, cv2.CAP_MSMF)
    return (cv2.CAP_ANY,)


def list_cameras(max_index=6):
    """Probe camera indexes; returns [(index, backend, width, height)] for OpenCV cameras that
    give a frame, then [(index, "oCam SDK", model, usb)] for the connected oCams."""
    found = []
    _quiet_opencv()
    for i in range(max_index):
        for api in _backends():
            cap = cv2.VideoCapture(i, api)
            if cap.isOpened():
                ok, f = cap.read()
                if ok:
                    found.append((i, cap.getBackendName(), f.shape[1], f.shape[0]))
                cap.release()
                break
            cap.release()
    found += [(i, "oCam SDK", model, usb) for i, model, usb in ocam.list_ocams()]
    return found


def _quiet_opencv():
    # Media Foundation logs a warning per failed grab while probing a GREY-only camera
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except AttributeError:
        pass


def open_camera(cfg, log=print):
    """A started-ready camera source for camera.xml (cfg.driver: auto | opencv | ocam)."""
    driver = (cfg.driver or "auto").lower()
    errors = []
    if driver in ("auto", "opencv"):
        src = CameraSource(cfg)
        try:
            src.open()
            if driver == "opencv" or src.probe_frame():
                return src
            errors.append(f"camera {cfg.camera_id} ({src.backend}) opened but gave no frame")
            src.stop()
        except RuntimeError as e:
            errors.append(str(e))
        if driver == "opencv":
            raise RuntimeError("; ".join(errors))
    n = ocam.count()
    if n == 0:
        why = ocam.dll_error() or "no oCam connected"
        raise RuntimeError("; ".join(errors + [f"oCam SDK: {why}"]))
    idx = cfg.camera_id if cfg.camera_id < n else 0
    if driver == "auto":
        log(f"  camera  {'; '.join(errors)} -> oCam SDK, oCam {idx}")
    elif idx != cfg.camera_id:
        log(f"  camera  oCam {cfg.camera_id} not connected ({n} found), using oCam {idx}")
    return ocam.OCamSource(cfg, idx).open()


class Source:
    """Common interface: start(), read() -> (frame_bgr or None, frame_no), stop()."""

    name = "source"

    def start(self):
        return self

    def read(self):
        raise NotImplementedError

    def stop(self):
        pass


class CameraSource(Source):
    def __init__(self, cfg):
        self.cfg = cfg
        self.name = f"camera {cfg.camera_id}"
        self.cap = None
        self._lock = threading.Lock()
        self._frame = None
        self._no = 0
        self._run = False
        self._thread = None

    def open(self):
        cap = None
        for api in _backends():
            cap = cv2.VideoCapture(self.cfg.camera_id, api)
            if cap.isOpened():
                self.backend = cap.getBackendName()
                break
            cap.release()
        if cap is None or not cap.isOpened():
            raise RuntimeError(f"camera {self.cfg.camera_id} could not be opened")
        # Order matters with DirectShow (tested on the exhibit camera, FIXLOG 11): the fps goes
        # first, the format last. Setting the size resets the format to YUY2 (uncompressed
        # 1280x800 is 10 fps on USB 2), and setting the fps after MJPG switches it back to YUY2
        # too; with the fps after the size but before MJPG, MJPG came up at its fastest mode
        # (120) whatever fps was asked.
        cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
        if self.cfg.compress:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap = cap
        return self

    def probe_frame(self, timeout=2.0):
        """True when the opened camera delivers a frame (GREY-only cameras open but never do)."""
        _quiet_opencv()
        t = time.perf_counter()
        while time.perf_counter() - t < timeout:
            ok, _ = self.cap.read()
            if ok:
                return True
            time.sleep(0.02)
        return False

    def apply_settings(self, settings, log=print):
        return camera_options.apply_settings(self.cap, settings, log)

    def actual_format(self):
        c = self.cap
        return (int(c.get(cv2.CAP_PROP_FRAME_WIDTH)), int(c.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                round(c.get(cv2.CAP_PROP_FPS), 1))

    def start(self):
        if self.cap is None:
            self.open()
        self._run = True
        self._thread = threading.Thread(target=self._loop, name="grab", daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        while self._run:
            ok, f = self.cap.read()
            if not ok:
                time.sleep(0.005)
                continue
            with self._lock:
                self._frame = f
                self._no += 1

    def read(self):
        with self._lock:
            return self._frame, self._no

    def show_driver_dialog(self):
        """DirectShow property page: exposure / gain / focus set by hand during a test."""
        if self.cap is not None:
            return self.cap.set(cv2.CAP_PROP_SETTINGS, 1)
        return False

    def stop(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=1)
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class FileSource(Source):
    """A still image (repeated) or a video file, for offline tests."""

    def __init__(self, path, loop=True):
        self.path = path
        self.name = os.path.basename(path)
        self.loop = loop
        self.img = None
        self.cap = None
        self._no = 0
        ext = os.path.splitext(path)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"):
            self.img = cv2.imread(path, cv2.IMREAD_COLOR)
            if self.img is None:
                raise RuntimeError(f"cannot read image {path}")
        else:
            self.cap = cv2.VideoCapture(path)
            if not self.cap.isOpened():
                raise RuntimeError(f"cannot open video {path}")

    def read(self):
        self._no += 1
        if self.img is not None:
            return self.img, self._no
        ok, f = self.cap.read()
        if not ok and self.loop:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, f = self.cap.read()
        return (f if ok else None), self._no

    def stop(self):
        if self.cap is not None:
            self.cap.release()
