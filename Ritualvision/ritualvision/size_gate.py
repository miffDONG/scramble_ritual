"""Minimum object size (r key): anything smaller than the registered smallest object is noise.

States
  off          no size filter
  registering  the filter is paused; place the smallest object, press r again to register
               (Esc cancels). The size is the median, over the last frames, of the smallest
               recognised marker in each frame.
  on           folds whose arm length rc < MARGIN * registered size are dropped as noise

The state is saved next to the exe (ritualvision_state.json), so it also applies after a
restart and in headless (-n) mode.
"""

import json
import os
from collections import deque

MARGIN = 0.8            # accept down to 80% of the registered size (tilt, lens edges, blur)
SAMPLE_FRAMES = 30


class SizeGate:
    def __init__(self, path):
        self.path = path
        self.state = "off"
        self.size = 0.0                 # registered rc of the smallest object (px)
        self.samples = deque(maxlen=SAMPLE_FRAMES)
        self._load()

    # -------------------------------------------------------------- persistence
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            self.size = float(d.get("min_size_rc", 0.0))
            self.state = "on" if d.get("min_size_on") and self.size > 0 else "off"
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"min_size_on": self.state == "on", "min_size_rc": round(self.size, 2)}, f, indent=1)
        except OSError:
            pass

    # -------------------------------------------------------------- recognizer side
    @property
    def min_rc(self):
        """Threshold to give the recognizer (0 = no filtering)."""
        return MARGIN * self.size if self.state == "on" else 0.0

    def observe(self, markers):
        if self.state == "registering":
            sizes = [m.size for m in markers if m.fid >= 0]
            if sizes:
                self.samples.append(min(sizes))

    # -------------------------------------------------------------- key r / Esc
    def press_r(self):
        """Returns a short message for the preview window."""
        if self.state == "off":
            self.state = "registering"
            self.samples.clear()
            return "min size: registering"
        if self.state == "registering":
            if len(self.samples) < 5:
                return "no marker recognised yet: place the smallest object in view"
            s = sorted(self.samples)
            self.size = s[len(s) // 2]
            self.state = "on"
            self._save()
            return f"min size ON: objects below {self.min_rc:.0f}px (fold arm) are noise"
        self.state = "off"
        self._save()
        return "min size OFF"

    def cancel(self):
        if self.state == "registering":
            self.state = "on" if self.size > 0 and self._was_on() else "off"
            return "registration cancelled"
        return ""

    def _was_on(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return bool(json.load(f).get("min_size_on"))
        except (OSError, ValueError):
            return False

    # -------------------------------------------------------------- display
    def status(self):
        if self.state == "on":
            return f"min size ON >= {self.min_rc:.0f}px"
        if self.state == "registering":
            return "min size: REGISTERING"
        return "min size off"

    def prompt(self, markers):
        if self.state != "registering":
            return ()
        seen = [m.size for m in markers if m.fid >= 0]
        now = f"smallest now {min(seen):.0f}px" if seen else "no marker recognised"
        return ("REGISTER MIN SIZE: place the smallest object, press r",
                f"{now}  ({len(self.samples)}/{SAMPLE_FRAMES} frames)   Esc = cancel")
