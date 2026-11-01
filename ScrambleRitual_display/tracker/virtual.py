"""
Virtual objects for the TEST server (scripts/test_start.bat).

reacTIVision is replaced by a table of hand-placed hexagon objects. The
`VirtualSource` mimics TuioReceiver's interface (start/stop/age/fps/
on_frame/snapshot/...) and emits one "frame" per tick at the camera fps, so
everything after it — ROI normalize, tension graph, OSC format and targets —
is the exact exhibition pipeline.

Pure stdlib: no cv2 / Flask, cheap to import in tests.
"""

import json
import os
import threading
import time
from dataclasses import replace

from tracker.reactivision import ExhibitObject

ID_MIN, ID_MAX = 0, 24          # 25 objects, IDs 0..24
LIMIT = ID_MAX - ID_MIN + 1


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _wrap_deg(a):
    return ((float(a) + 180.0) % 360.0) - 180.0


class VirtualTable:
    """Thread-safe list of virtual objects in camera-frame coordinates
    (nx, ny 0..1, the same space as TUIO x/y). IDs are unique, 0..24."""

    def __init__(self, path=None):
        self._lock = threading.Lock()
        self._objs = []           # dicts: track, id, x, y, tilt
        self._next_track = 1
        self.path = path
        self._dirty = False
        if path:
            self._load()

    # -- persistence ------------------------------------------------------

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        seen = set()
        for o in data.get("objects", []):
            try:
                oid = int(o["id"])
                if not ID_MIN <= oid <= ID_MAX or oid in seen:
                    continue
                seen.add(oid)
                self._objs.append({"track": self._next_track, "id": oid,
                                   "x": _clamp(float(o["x"]), 0.0, 1.0),
                                   "y": _clamp(float(o["y"]), 0.0, 1.0),
                                   "tilt": _wrap_deg(o.get("tilt", 0.0))})
                self._next_track += 1
            except Exception:
                continue

    def save_if_dirty(self):
        if not self.path:
            return
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            data = {"objects": [{k: o[k] for k in ("id", "x", "y", "tilt")}
                                for o in self._objs]}
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except Exception:
            pass

    # -- queries ----------------------------------------------------------

    def list(self):
        with self._lock:
            return [dict(o) for o in self._objs]

    def snapshot(self):
        """-> [ExhibitObject] (canvas cx/cy are filled by the pipeline)."""
        with self._lock:
            return [ExhibitObject(track_id=o["track"], code_id=o["id"],
                                  nx=o["x"], ny=o["y"], angle=o["tilt"],
                                  status="virtual")
                    for o in self._objs]

    # -- edits (all return (ok, error)) ------------------------------------

    @staticmethod
    def _bounds(roi):
        """ROI [x, y, w, h] (normalized) -> (x1, y1, x2, y2); whole frame if unset."""
        if roi and len(roi) == 4 and roi[2] > 0 and roi[3] > 0:
            x, y, w, h = (float(v) for v in roi)
            return x, y, x + w, y + h
        return 0.0, 0.0, 1.0, 1.0

    def _free_spot(self, roi):
        """First cell of a 5x5 grid inside the table with no object nearby;
        else the cell farthest from every object."""
        x1, y1, x2, y2 = self._bounds(roi)
        n = 5
        cells = [(x1 + (x2 - x1) * (i + 0.5) / n, y1 + (y2 - y1) * (j + 0.5) / n)
                 for j in range(n) for i in range(n)]
        gap = min(x2 - x1, y2 - y1) / n * 0.5

        def nearest(c):
            return min((((c[0] - o["x"]) ** 2 + (c[1] - o["y"]) ** 2) ** 0.5
                        for o in self._objs), default=1e9)
        for c in cells:
            if nearest(c) > gap:
                return c
        return max(cells, key=nearest)

    def add(self, roi=None, x=None, y=None):
        with self._lock:
            used = {o["id"] for o in self._objs}
            free = [i for i in range(ID_MIN, ID_MAX + 1) if i not in used]
            if not free:
                return False, f"오브제 {LIMIT}개가 모두 올라가 있습니다"
            if x is None or y is None:
                x, y = self._free_spot(roi)
            bx1, by1, bx2, by2 = self._bounds(roi)
            self._objs.append({"track": self._next_track, "id": free[0],
                               "x": _clamp(float(x), bx1, bx2),
                               "y": _clamp(float(y), by1, by2), "tilt": 0.0})
            self._next_track += 1
            self._dirty = True
            return True, None

    def remove(self, index):
        with self._lock:
            if not 0 <= index < len(self._objs):
                return False, "없는 오브제"
            self._objs.pop(index)
            self._dirty = True
            return True, None

    def clear(self):
        with self._lock:
            self._objs = []
            self._dirty = True
            return True, None

    def move(self, index, x, y, roi=None):
        with self._lock:
            if not 0 <= index < len(self._objs):
                return False, "없는 오브제"
            bx1, by1, bx2, by2 = self._bounds(roi)
            o = self._objs[index]
            o["x"] = round(_clamp(float(x), bx1, bx2), 4)
            o["y"] = round(_clamp(float(y), by1, by2), 4)
            self._dirty = True
            return True, None

    def update(self, index, code_id=None, tilt=None):
        with self._lock:
            if not 0 <= index < len(self._objs):
                return False, "없는 오브제"
            o = self._objs[index]
            if code_id is not None:
                try:
                    cid = int(code_id)
                except (TypeError, ValueError):
                    return False, "ID는 숫자여야 합니다"
                if not ID_MIN <= cid <= ID_MAX:
                    return False, f"ID 범위는 {ID_MIN}~{ID_MAX}"
                if any(p["id"] == cid and p is not o for p in self._objs):
                    return False, f"ID {cid}는 이미 테이블 위에 있습니다"
                if cid != o["id"]:
                    # a different marker on the table = a new TUIO session
                    o["id"] = cid
                    o["track"] = self._next_track
                    self._next_track += 1
            if tilt is not None:
                o["tilt"] = round(_wrap_deg(tilt), 1)
            self._dirty = True
            return True, None


class VirtualSource:
    """Drop-in for TuioReceiver: emits the VirtualTable as one frame per tick
    at `fps` and calls on_frame(objects, fseq) on its own thread, exactly as
    the receiver does after a committed TUIO bundle."""

    def __init__(self, table, fps=120.0):
        self.table = table
        self.fps_target = _clamp(float(fps or 120.0), 1.0, 240.0)
        self.port = "virtual"
        self.error = None
        self.on_frame = None
        self.on_frame_error = None
        self.frame_no = 0
        self.last_fseq = -1
        self.last_rx = 0.0
        self.fps = 0.0
        self._last_t = None
        self._objects = []
        self._run = False
        self._thread = None

    def start(self):
        self._run = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="virtual")
        self._thread.start()
        return self

    def stop(self):
        self._run = False
        if self._thread is not None:
            self._thread.join(1.0)
            self._thread = None

    def _loop(self):
        period = 1.0 / self.fps_target
        nxt = time.perf_counter()
        while self._run:
            objs = self.table.snapshot()
            now = time.perf_counter()
            self._objects = objs
            self.frame_no += 1
            self.last_fseq = self.frame_no
            if self._last_t is not None:
                dt = now - self._last_t
                if dt > 0:
                    self.fps += 0.1 * (1.0 / dt - self.fps)
            self._last_t = now
            self.last_rx = now
            cb = self.on_frame
            if cb is not None:
                try:
                    cb([replace(o) for o in objs], self.last_fseq)
                except Exception as exc:
                    self.on_frame_error = str(exc)
            nxt += period
            wait = nxt - time.perf_counter()
            if wait > 0:
                time.sleep(wait)
            else:                       # fell behind: don't burst to catch up
                nxt = time.perf_counter()

    def snapshot(self):
        return [replace(o) for o in self._objects]

    def age(self):
        if not self.last_rx:
            return float("inf")
        return time.perf_counter() - self.last_rx
