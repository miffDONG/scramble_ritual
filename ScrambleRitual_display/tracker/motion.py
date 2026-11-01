"""
Object speed and the optional lift cut (rt["lift_cut_enabled"]).

The camera sits under the table: a marker lifted off the glass is often
still decoded for a second or more while it is carried, so reacTIVision
keeps reporting it (and OSC keeps sending it). TUIO 1.1 /tuio/2Dobj has no
height or size, so a lift can only be told apart by motion: an object that
moves faster than `lift_cut_speed` (table long sides per second) for
`lift_cut_hold_s` is withheld until it has been slow again for
`release_s`. Fast slides on the glass are withheld too — that is the trade.

Speed is measured over a short window (WINDOW_S) so the 1-2 px jitter of a
marker at rest reads as ~0 instead of a per-frame spike.
"""

import collections
import math

WINDOW_S = 0.2


def window_speed(hist, now, x, y, window=WINDOW_S):
    """Append (now, x, y) to `hist` (a deque) and return the speed in px/s
    over the last `window` seconds (0 until 50 ms of history exist)."""
    hist.append((now, x, y))
    while len(hist) > 2 and now - hist[0][0] > window:
        hist.popleft()
    t0, x0, y0 = hist[0]
    dt = now - t0
    return math.hypot(x - x0, y - y0) / dt if dt >= 0.05 else 0.0


class MotionGate:
    FORGET_S = 1.0           # drop the state of an object unseen this long
    RELEASE_FRACTION = 0.5   # "slow again" = below this x the cut speed

    def __init__(self):
        self.state = {}      # key -> {"hist", "fast_since", "slow_since", "cut"}
        self.cut_count = 0   # objects withheld (cumulative frames)

    @staticmethod
    def key(o):
        fix = getattr(o, "fix_track", -1)
        return ("fix", fix) if fix >= 0 else ("tuio", o.track_id)

    def update(self, objs, now, long_px, enabled=False, cut_speed=0.3,
               hold_s=0.1, release_s=0.3):
        """Sets o.speed (table long sides / s) on every object; returns the
        objects to send (all of them when the cut is off)."""
        long_px = max(float(long_px), 1.0)
        out, seen = [], set()
        for o in objs:
            k = self.key(o)
            seen.add(k)
            st = self.state.get(k)
            if st is None:
                st = self.state[k] = {"hist": collections.deque(), "fast_since": None,
                                      "slow_since": None, "cut": False, "t": now}
            st["t"] = now
            o.speed = round(window_speed(st["hist"], now, o.cx, o.cy) / long_px, 3)
            if not enabled:
                st["cut"] = False
                st["fast_since"] = st["slow_since"] = None
                out.append(o)
                continue
            if o.speed >= cut_speed:
                st["slow_since"] = None
                if st["fast_since"] is None:
                    st["fast_since"] = now
                if now - st["fast_since"] >= hold_s:
                    st["cut"] = True
            else:
                st["fast_since"] = None
                if st["cut"]:
                    if o.speed < cut_speed * self.RELEASE_FRACTION:
                        if st["slow_since"] is None:
                            st["slow_since"] = now
                        if now - st["slow_since"] >= release_s:
                            st["cut"] = False
                    else:
                        st["slow_since"] = None
            o.lifted = st["cut"]
            if st["cut"]:
                self.cut_count += 1
            else:
                out.append(o)
        for k in [k for k, st in self.state.items()
                  if k not in seen and now - st["t"] > self.FORGET_S]:
            del self.state[k]
        return out
