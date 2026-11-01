"""TUIO 1.1 /tuio/2Dobj receiver, shared by both trackers (reacTIVision and Ritualvision).

Each tracker sends one bundle per camera frame: alive, set*, fseq. Ritualvision adds one
extra message per object in the same bundle (ignored by any other TUIO client):

  /ritualvision/size  s(session id)  rc(px, float)  W  H(frame size, int)

rc is the fold-line arm length in camera pixels: the marker's measured size.

Pure stdlib + python-osc: no cv2 / Flask here so tests can import it cheaply.
"""

import math
import threading
import time
from dataclasses import dataclass, replace


def tuio_angle_deg(rad, offset=0.0):
    """TUIO angle (radians, 0..2π, screen-clockwise positive) -> degrees
    wrapped to -180..180. Same convention as the tuner's `tilt` (image
    coordinates, +y down, clockwise positive), so no sign flip."""
    return ((math.degrees(float(rad)) + float(offset) + 180.0) % 360.0) - 180.0


@dataclass
class ExhibitObject:
    """One fiducial as the pipeline sees it. `code_id` is the tracker's
    symbol id (the object's identity); `track_id` the TUIO session id."""
    track_id: int
    code_id: int
    nx: float                 # TUIO x (0..1 of the camera frame)
    ny: float
    angle: float              # degrees, -180..180
    rad: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    va: float = 0.0
    motion_accel: float = 0.0
    rot_accel: float = 0.0
    cx: float = 0.0           # canvas pixels (filled by the pipeline)
    cy: float = 0.0
    flip: bool = False
    pitch: float | None = None
    status: str = "tuio"
    src_id: int = -1          # tracker reading before idfix (-1 = idfix off)
    fix_track: int = -1       # idfix track id (-1 = idfix off)
    speed: float = 0.0        # table long sides / s (tracker/motion.py)
    lifted: bool = False      # withheld by the lift cut
    size_px: float = 0.0      # Ritualvision: fold arm length rc in camera px (0 = unknown)
    frame_w: int = 0          # camera frame the size and nx/ny refer to
    frame_h: int = 0

    @property
    def bits(self):           # tuner-compat: identity as a string
        return str(self.code_id)

    @property
    def raw_id(self):
        return self.src_id if self.src_id >= 0 else self.code_id

    @property
    def label(self):
        return f"ID{self.code_id:02d}"


class TuioReceiver:
    """TUIO 1.1 /tuio/2Dobj listener. A blocking (single-thread) OSC server
    keeps the messages of a bundle in order; the frame is committed on `fseq`."""

    def __init__(self, host="127.0.0.1", port=3333, angle_offset=0.0):
        self.host = host
        self.port = int(port)
        self.angle_offset = float(angle_offset)
        self._cond = threading.Condition()
        self._objects = {}          # session id -> ExhibitObject (committed)
        self._pending = {}          # updates since the last fseq
        self._alive = None          # session ids in the current bundle
        self._sizes = {}            # session id -> (rc, W, H), Ritualvision only
        self.frame_no = 0
        self.last_fseq = -1
        self.last_rx = 0.0
        self.fps = 0.0
        self._last_t = None
        self.error = None
        self._server = None
        self._thread = None
        #: optional callback(objects, fseq) run on the receiver thread right
        #: after each frame is committed — the lowest-latency hook for OSC
        self.on_frame = None
        self.on_frame_error = None

    # -- lifecycle ------------------------------------------------------

    def start(self):
        try:
            from pythonosc.dispatcher import Dispatcher
            from pythonosc.osc_server import BlockingOSCUDPServer
            disp = Dispatcher()
            disp.map("/tuio/2Dobj", self._on_obj)
            disp.map("/ritualvision/size", self._on_size)
            self._server = BlockingOSCUDPServer((self.host, self.port), disp)
        except Exception as exc:
            self.error = f"TUIO 포트 {self.port} 열기 실패: {exc}"
            self._server = None
            return self
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True, name="tuio")
        self._thread.start()
        return self

    def stop(self):
        srv = self._server
        self._server = None
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(2.0)
            self._thread = None

    # -- OSC handlers (server thread) ------------------------------------

    def _on_obj(self, addr, *args):
        if not args:
            return
        kind = args[0]
        if kind == "set" and len(args) >= 11:
            s, i, x, y, a, X, Y, A, m, r = args[1:11]
            self._pending[int(s)] = ExhibitObject(
                track_id=int(s), code_id=int(i), nx=float(x), ny=float(y),
                angle=tuio_angle_deg(a, self.angle_offset), rad=float(a),
                vx=float(X), vy=float(Y), va=float(A),
                motion_accel=float(m), rot_accel=float(r))
        elif kind == "alive":
            self._alive = {int(v) for v in args[1:]}
        elif kind == "fseq":
            self._commit(int(args[1]) if len(args) > 1 else -1)

    def _on_size(self, addr, *args):
        if len(args) >= 4:
            self._sizes[int(args[0])] = (float(args[1]), int(args[2]), int(args[3]))

    def _commit(self, fseq):
        with self._cond:
            merged = dict(self._objects)
            merged.update(self._pending)
            if self._alive is not None:
                merged = {s: o for s, o in merged.items() if s in self._alive}
                self._sizes = {s: v for s, v in self._sizes.items() if s in self._alive}
            for s, o in merged.items():
                sz = self._sizes.get(s)
                if sz is not None:
                    o.size_px, o.frame_w, o.frame_h = sz
            self._objects = merged
            self._pending = {}
            self._alive = None
            self.last_fseq = fseq
            self.frame_no += 1
            # perf_counter, not monotonic: on Windows monotonic ticks every
            # 15.6 ms, which caps a per-frame fps estimate at ~64
            now = time.perf_counter()
            if self._last_t is not None:
                dt = now - self._last_t
                if dt > 0:
                    self.fps += 0.1 * (1.0 / dt - self.fps)
            self._last_t = now
            self.last_rx = now
            self._cond.notify_all()
            objs = [replace(o) for _, o in sorted(merged.items())]
        cb = self.on_frame
        if cb is not None:                  # outside the lock: sending must not block readers
            try:
                cb(objs, fseq)
            except Exception as exc:        # never let a send error kill the receiver
                self.on_frame_error = str(exc)

    # -- pipeline side ----------------------------------------------------

    def wait_frame(self, timeout=0.1, since=None):
        """Block until a TUIO frame newer than `since` (default: now) is
        committed. Returns True on a new frame, False on timeout."""
        with self._cond:
            seen = self.frame_no if since is None else since
            self._cond.wait_for(lambda: self.frame_no != seen, timeout)
            return self.frame_no != seen

    def snapshot(self):
        with self._cond:
            return [replace(o) for _, o in sorted(self._objects.items())]

    def age(self):
        """Seconds since the last committed frame (inf before the first)."""
        if not self.last_rx:
            return float("inf")
        return time.perf_counter() - self.last_rx
