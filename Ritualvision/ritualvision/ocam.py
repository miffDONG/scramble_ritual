"""oCam 연결부 (Withrobot oCam through its own SDK, libCamCap).

Mono oCams (oCam-1MGN-U ...) only stream 8-bit GREY (Y800), which neither OpenCV's DirectShow /
Media Foundation backends nor reacTIVision can open. Withrobot's libCamCap-x64.dll (shipped with
oCamViewer, header in github.com/withrobot/oCam) reads them directly. This module wraps it with
ctypes so CameraSource can fall back to it; other cameras keep the OpenCV path.

DLL search: next to Ritualvision.exe, inside the exe (PyInstaller), then vendor/ocam/.
"""

import ctypes as C
import math
import os
import threading
import time

import cv2
import numpy as np

from .config import base_dir, bundled

DLL_NAME = "libCamCap-x64.dll"

INFO_USB_TYPE, INFO_SERIAL_NUM, INFO_MODEL_NAME, INFO_DATE_TIME = 1, 2, 3, 4
CTRL_BRIGHTNESS, CTRL_CONTRAST, CTRL_HUE, CTRL_SATURATION, CTRL_EXPOSURE = 1, 2, 3, 4, 5
# camera.xml <settings> keys the oCam SDK can set
CTRLS = {"brightness": CTRL_BRIGHTNESS, "contrast": CTRL_CONTRAST,
         "exposure": CTRL_EXPOSURE, "shutter": CTRL_EXPOSURE}

_dll = None
_dll_error = None


def _candidates():
    yield os.path.join(base_dir(), DLL_NAME)
    yield bundled(DLL_NAME)
    yield os.path.join(base_dir(), "vendor", "ocam", DLL_NAME)


def load():
    """The libCamCap DLL, or None when it is missing / not loadable (error kept in dll_error())."""
    global _dll, _dll_error
    if _dll is not None or _dll_error is not None:
        return _dll
    for path in _candidates():
        if not os.path.exists(path):
            continue
        try:
            d = C.CDLL(path)
        except OSError as e:
            _dll_error = f"{path}: {e}"
            continue
        d.GetConnectedCamNumber.restype = C.c_int
        d.CamGetDeviceInfo.restype = C.c_char_p
        d.CamGetDeviceInfo.argtypes = [C.c_int, C.c_int]
        d.CamOpen.restype = C.c_void_p
        d.CamOpen.argtypes = [C.c_int, C.c_int, C.c_int, C.c_double, C.c_void_p, C.c_void_p]
        d.CamStart.argtypes = d.CamStop.argtypes = [C.c_void_p]
        d.CamClose.argtypes = [C.POINTER(C.c_void_p)]       # CamClose(CAMPTR&)
        d.CamGetImage.argtypes = [C.c_void_p, C.c_void_p]
        d.CamSetCtrl.argtypes = [C.c_void_p, C.c_int, C.c_long]
        d.CamGetCtrl.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_long)]
        d.CamGetCtrlRange.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_long), C.POINTER(C.c_long)]
        _dll, _dll_error = d, None
        return d
    if _dll_error is None:
        _dll_error = f"{DLL_NAME} not found"
    return None


def dll_error():
    return _dll_error


def count():
    d = load()
    if d is None:
        return 0
    try:
        return max(0, d.GetConnectedCamNumber())
    except OSError:
        return 0


def info(index, kind):
    d = load()
    s = d.CamGetDeviceInfo(index, kind) if d else None
    return s.decode(errors="replace") if s else ""


def list_ocams():
    """[(index, model, usb)] of the connected oCams."""
    return [(i, info(i, INFO_MODEL_NAME), info(i, INFO_USB_TYPE)) for i in range(count())]


def _is_mono(model):
    # Withrobot names mono models with M (oCam-1MGN-U, -1MGN-U-T2 ...); color ones stream YUYV
    m = model.upper()
    return "MGN" in m or m.endswith("-M")


class OCamSource:
    """Same interface as CameraSource: open(), start(), read(), stop(), actual_format()."""

    backend = "oCam SDK"

    def __init__(self, cfg, index):
        self.cfg = cfg
        self.index = index
        self.name = f"oCam {index}"
        self.model = ""
        self.h = None
        self._lock = threading.Lock()
        self._frame = None
        self._no = 0
        self._run = False
        self._thread = None
        self._t0, self._n0, self._fps = 0.0, 0, 0.0

    def open(self):
        d = load()
        if d is None:
            raise RuntimeError(f"oCam SDK unavailable ({dll_error()})")
        if self.index >= count():
            raise RuntimeError(f"oCam {self.index} not connected ({count()} found)")
        self.model = info(self.index, INFO_MODEL_NAME)
        self.mono = _is_mono(self.model)
        w, h = self.cfg.width, self.cfg.height
        h_ = d.CamOpen(self.index, w, h, float(self.cfg.fps), None, None)
        if not h_:
            raise RuntimeError(f"{self.model}: CamOpen {w}x{h} @ {self.cfg.fps} failed "
                               "(mode not supported? see oCamViewer for the list)")
        self.h = h_
        self.w, self.ht = w, h
        self._buf = np.zeros(w * h * (1 if self.mono else 2), np.uint8)
        self.name = f"{self.model} ({info(self.index, INFO_USB_TYPE)})"
        return self

    def start(self):
        if self.h is None:
            self.open()
        if not load().CamStart(self.h):
            raise RuntimeError(f"{self.model}: CamStart failed")
        self._run = True
        self._t0 = time.perf_counter()
        self._thread = threading.Thread(target=self._loop, name="ocam-grab", daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        d, buf = load(), self._buf
        while self._run:
            if not d.CamGetImage(self.h, buf.ctypes.data):
                time.sleep(0.001)
                continue
            if self.mono:
                f = cv2.cvtColor(buf.reshape(self.ht, self.w), cv2.COLOR_GRAY2BGR)
            else:
                f = cv2.cvtColor(buf.reshape(self.ht, self.w, 2), cv2.COLOR_YUV2BGR_YUYV)
            with self._lock:
                self._frame = f
                self._no += 1

    def read(self):
        with self._lock:
            return self._frame, self._no

    def actual_format(self):
        """Width, height and the frame rate measured since start (asked fps before that)."""
        dt = time.perf_counter() - self._t0
        fps = round(self._no / dt, 1) if self._run and dt > 0.5 else float(self.cfg.fps)
        return self.w, self.ht, fps

    def wait_first_frame(self, timeout=3.0):
        t = time.perf_counter()
        while time.perf_counter() - t < timeout:
            if self._no:
                return True
            time.sleep(0.01)
        return False

    def apply_settings(self, settings, log=print):
        """camera.xml <settings>: brightness, contrast, exposure/shutter (oCam's own ranges).
        "default" leaves the camera value, "min"/"max" or a number set it; others are logged."""
        d = load()
        applied = {}
        for key, raw in settings.items():
            val = str(raw).strip().lower()
            if val in ("", "default"):
                continue
            ctrl = CTRLS.get(key)
            if ctrl is None or val == "auto":
                log(f"[camera] {key}={raw} not available on {self.model}")
                continue
            lo, hi = C.c_long(), C.c_long()
            if not d.CamGetCtrlRange(self.h, ctrl, C.byref(lo), C.byref(hi)) or lo.value == hi.value:
                log(f"[camera] {key} not available on {self.model}")
                continue
            if val in ("min", "max"):
                num = lo.value if val == "min" else hi.value
            else:
                try:
                    num = int(round(float(val)))
                except ValueError:
                    log(f"[camera] ignoring {key}={raw!r}")
                    continue
                num = min(max(num, lo.value), hi.value)
            ok = bool(d.CamSetCtrl(self.h, ctrl, num))
            cur = C.c_long()
            d.CamGetCtrl(self.h, ctrl, C.byref(cur))
            applied[key] = (num, ok, cur.value, f"range {lo.value}..{hi.value}")
        for k, v in applied.items():
            log(f"[camera] {k}: {v}")
        self._limit_exposure(log)
        return applied

    def max_exposure(self):
        """Longest exposure (log2 s) that still allows the asked fps: 60 fps -> -6 (1/64 s)."""
        return -math.ceil(math.log2(max(1, self.cfg.fps)))

    def _limit_exposure(self, log=print):
        """The camera keeps its last exposure (also from oCamViewer or an earlier run); one
        longer than a frame period drops the frame rate (-3 = 1/8 s -> ~9 fps), so cap it."""
        d, cur = load(), C.c_long()
        if not d.CamGetCtrl(self.h, CTRL_EXPOSURE, C.byref(cur)):
            return
        top = self.max_exposure()
        if cur.value > top:
            d.CamSetCtrl(self.h, CTRL_EXPOSURE, top)
            log(f"[camera] exposure {cur.value} (1/{2 ** -cur.value:g} s) is too long for "
                f"{self.cfg.fps} fps -> {top} (1/{2 ** -top:g} s)")

    OPTIONS_WIN = "oCam options"

    def show_driver_dialog(self):
        """o key: the SDK has no property page, so open (or close) a slider window for the
        controls the camera reports a range for. Changes apply live; the values are logged so
        they can be written into camera.xml <settings>."""
        try:
            if cv2.getWindowProperty(self.OPTIONS_WIN, cv2.WND_PROP_VISIBLE) >= 1:
                cv2.destroyWindow(self.OPTIONS_WIN)
                return True
        except cv2.error:
            pass
        d = load()
        cv2.namedWindow(self.OPTIONS_WIN, cv2.WINDOW_AUTOSIZE)
        self._opt = {}
        for name, ctrl in (("brightness", CTRL_BRIGHTNESS), ("contrast", CTRL_CONTRAST),
                           ("exposure", CTRL_EXPOSURE)):
            lo, hi, cur = C.c_long(), C.c_long(), C.c_long()
            if not d.CamGetCtrlRange(self.h, ctrl, C.byref(lo), C.byref(hi)) or lo.value >= hi.value:
                continue
            d.CamGetCtrl(self.h, ctrl, C.byref(cur))
            top = min(hi.value, self.max_exposure()) if ctrl == CTRL_EXPOSURE else hi.value
            self._opt[name] = (ctrl, lo.value, min(cur.value, top), top)
            cv2.createTrackbar(name, self.OPTIONS_WIN, min(cur.value, top) - lo.value, top - lo.value,
                               lambda pos, n=name: self._on_slider(n, pos))
        self._draw_options()
        return True

    def _on_slider(self, name, pos):
        ctrl, lo, _, top = self._opt[name]
        val = min(max(lo + pos, lo), top)
        load().CamSetCtrl(self.h, ctrl, val)
        self._opt[name] = (ctrl, lo, val, top)
        self._draw_options()
        print("[camera] " + " ".join(f'{n}="{v[2]}"' for n, v in self._opt.items())
              + "  (camera.xml <settings>)", flush=True)

    def _draw_options(self):
        img = np.full((70, 420, 3), 32, np.uint8)
        if "exposure" in self._opt:
            e = self._opt["exposure"][2]
            # UVC exposure is log2 seconds: -8 = 1/256 s; frames can't come faster than that
            txt = f"exposure {e}: 1/{2 ** -e:g} s, max {min(2 ** -e, self.cfg.fps):g} fps"
            cv2.putText(img, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1, cv2.LINE_AA)
        cv2.putText(img, "o: close   values are printed in the console", (10, 56),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1, cv2.LINE_AA)
        cv2.imshow(self.OPTIONS_WIN, img)

    def stop(self):
        self._run = False
        if self._thread:
            self._thread.join(timeout=1)
        d = load()
        if self.h:
            d.CamStop(self.h)
            p = C.c_void_p(self.h)
            d.CamClose(C.byref(p))
            self.h = None
