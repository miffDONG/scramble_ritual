"""Session ids and velocities, the way reacTIVision reports them over TUIO.

A marker keeps its session id while its fiducial id keeps appearing; it survives up to
`hold` missed frames (hand passing over, a dropped frame) before it is removed.
"""

import math
import time


class TrackedObject:
    __slots__ = ("sid", "fid", "x", "y", "a", "X", "Y", "A", "m", "r", "t", "missed", "speed",
                 "size")

    def __init__(self, sid, fid, x, y, a, t, size=0.0):
        self.sid, self.fid = sid, fid
        self.x, self.y, self.a = x, y, a
        self.X = self.Y = self.A = self.m = self.r = 0.0
        self.speed = 0.0
        self.t = t
        self.missed = 0
        self.size = size            # fold arm length rc (px); kept while the marker is held


class Tracker:
    def __init__(self, hold=3):
        self.hold = hold
        self.next_sid = 0
        self.objects = {}           # fid -> TrackedObject

    def update(self, detections, now=None):
        """detections: list of (fid, x_norm, y_norm, angle_rad[, size_px]). Returns live
        objects."""
        now = time.perf_counter() if now is None else now
        seen = set()
        for det in detections:
            fid, x, y, a = det[:4]
            size = float(det[4]) if len(det) > 4 else 0.0
            if fid < 0 or fid in seen:
                continue
            seen.add(fid)
            o = self.objects.get(fid)
            if o is None:
                self.objects[fid] = TrackedObject(self.next_sid, fid, x, y, a, now, size)
                self.next_sid += 1
                continue
            dt = max(now - o.t, 1e-3)
            X, Y = (x - o.x) / dt, (y - o.y) / dt
            da = (a - o.a + math.pi) % (2 * math.pi) - math.pi
            A = da / dt / (2 * math.pi)                     # rotations per second, as TUIO
            speed = math.hypot(X, Y)
            o.m = (speed - o.speed) / dt
            o.r = (A - o.A) / dt
            o.X, o.Y, o.A, o.speed = X, Y, A, speed
            o.x, o.y, o.a, o.t, o.missed = x, y, a, now, 0
            if size > 0:
                o.size = size
        for fid in list(self.objects):
            if fid not in seen:
                o = self.objects[fid]
                o.missed += 1
                if o.missed > self.hold:
                    del self.objects[fid]
        return list(self.objects.values())
