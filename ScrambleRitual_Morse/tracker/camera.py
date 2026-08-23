"""Webcam capture that survives Windows' Media Foundation backend.

Import this module BEFORE cv2 anywhere a camera is opened: it sets
``OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS``, which OpenCV reads once while the
videoio library loads, so setting it after ``import cv2`` has no effect.

Why any of this is needed: on Windows OpenCV defaults to MSMF, which happily
"opens" plenty of UVC devices it can then never grab a frame from -- every
read() fails with

    [ WARN] cap_msmf.cpp CvCapture_MSMF::grabFrame videoio(MSMF):
            can't grab frame. Error: -2147483638

(-2147483638 = 0x8000000A, "the data is not yet available"), while the vendor
app and the Windows Camera app show the same device working fine.  Elgato
capture devices and several laptop cams do this consistently.  isOpened() is
therefore NOT proof a device works, so open_camera() tries the backends in
order and keeps the first one that actually delivers a frame.
"""

import os
import sys
import time
from contextlib import contextmanager

# Disables the hardware MFT path that is the usual cause of the grabFrame
# failure above. setdefault, so an operator can still force it back on.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2  # noqa: E402  (must come after the env switch above)

#: Windows: DirectShow first — it is the backend that works with the devices
#: MSMF chokes on. MSMF stays as a fallback because a few UVC cams (and most
#: virtual cams) only expose themselves there.
WIN_BACKENDS = (("dshow", cv2.CAP_DSHOW), ("msmf", cv2.CAP_MSMF), ("any", cv2.CAP_ANY))
POSIX_BACKENDS = (("any", cv2.CAP_ANY),)

#: A camera that has just been opened needs a moment before the first frame
#: lands; a broken MSMF device never produces one, so this is also how long we
#: wait before writing a backend off.
WARMUP_S = 2.0


def backends():
    return WIN_BACKENDS if sys.platform == "win32" else POSIX_BACKENDS


@contextmanager
def _quiet_videoio():
    """Mute OpenCV's warning spam while we probe backends that may fail."""
    log = getattr(getattr(cv2, "utils", None), "logging", None)
    if log is None:
        yield
        return
    prev = log.getLogLevel()
    log.setLogLevel(log.LOG_LEVEL_ERROR)
    try:
        yield
    finally:
        log.setLogLevel(prev)


def grab_first_frame(cap, warmup_s=WARMUP_S):
    """True once the capture actually hands over a frame, False if it never
    does within warmup_s."""
    deadline = time.monotonic() + warmup_s
    while time.monotonic() < deadline:
        ok, frame = cap.read()
        if ok and frame is not None and frame.size:
            return True
        time.sleep(0.05)
    return False


def open_camera(index, size=None, warmup_s=WARMUP_S):
    """Open camera `index` and return (cap, backend_name).

    `size` is an optional (width, height) requested before the warm-up read, so
    the resolution change happens while the stream is still being negotiated.
    Raises RuntimeError naming what each backend did if none produce a frame.
    """
    index = int(index)
    tried = []
    for name, api in backends():
        with _quiet_videoio():
            cap = cv2.VideoCapture(index, api)
            if not cap.isOpened():
                cap.release()
                tried.append(f"{name}: 열기 실패")
                continue
            if size:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(size[0]))
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(size[1]))
            if grab_first_frame(cap, warmup_s):
                return cap, name
            cap.release()
            tried.append(f"{name}: 프레임 없음")
    raise RuntimeError(
        f"카메라 {index}를 열 수 없습니다 ({', '.join(tried)}). "
        "다른 앱(Elgato Camera Hub, 카메라 앱, 화상회의)이 장치를 잡고 있으면 "
        "닫은 뒤 다시 시도하고, 설정 > 개인 정보 및 보안 > 카메라에서 "
        "데스크톱 앱의 카메라 접근이 켜져 있는지 확인하세요."
    )


def probe_cameras(limit=6, warmup_s=0.6):
    """Indices that hand over a frame, as [{index, name, backend}]. Slow (it
    opens every device), so callers with a cheaper enumeration should use that
    and keep this as the fallback."""
    found = []
    for i in range(limit):
        try:
            cap, backend = open_camera(i, warmup_s=warmup_s)
        except RuntimeError:
            continue
        found.append({"index": i, "name": f"Camera {i}", "backend": backend})
        cap.release()
    return found
