"""
Temporal stabilization for Morse card reads.

A single frame can misread one slot (motion blur, a hand crossing the
plate, backlight flicker). The stabilizer tracks each card across frames
by center distance and takes a per-slot majority vote over a sliding
window, so a stable ID survives occasional one-frame glitches and an ID
only changes after the new reading has actually won the vote.
"""

from collections import Counter, deque
from dataclasses import dataclass, field
import math

from .morse import decode_bits


STABILITY_DEFAULTS = {
    "window": 12,          # frames in the voting window
    "min_votes": 3,        # frames seen before a track reports bits
    "match_dist_frac": 0.08,  # match radius as a fraction of frame diagonal
    "max_missed": 10,      # frames a track survives without a detection
    "pos_alpha": 0.5,      # EMA for center smoothing
    "angle_alpha": 0.4,    # EMA for axis smoothing (vector-averaged)
}


@dataclass
class Track:
    track_id: int
    cx: float
    cy: float
    angle: float
    history: deque = field(repr=False, default_factory=deque)
    age: int = 0
    missed: int = 0
    last_bits: str = ""

    def vote_bits(self):
        """Per-slot majority over the window. A slot with a tie keeps the
        most recent value (Counter.most_common is insertion-ordered, so we
        feed newest-first)."""
        if not self.history:
            return ""
        slots = len(self.history[-1])
        out = []
        for i in range(slots):
            votes = Counter(bits[i] for bits in reversed(self.history)
                            if len(bits) == slots)
            out.append(votes.most_common(1)[0][0] if votes else "?")
        return "".join(out)


@dataclass
class StableCard:
    track_id: int
    cx: float
    cy: float
    angle: float
    bits: str
    code_id: int | None
    raw_id: int | None
    status: str
    age: int
    missed: int
    votes: int

    @property
    def label(self):
        if self.code_id is None:
            return f"? {self.bits}"
        return f"ID{self.code_id:02d}"


class CardStabilizer:
    def __init__(self, cfg=None, codebook=None, correction_distance=1):
        self.cfg = dict(STABILITY_DEFAULTS)
        if cfg:
            self.cfg.update(cfg)
        self.codebook = codebook
        self.correction_distance = correction_distance
        self.tracks = []
        self._next_id = 1

    def configure(self, cfg):
        self.cfg.update(cfg)

    def reset(self):
        self.tracks = []
        self._next_id = 1

    def update(self, cards, frame_size):
        """Feed one frame's detections; returns the list of StableCard."""
        c = self.cfg
        w, h = frame_size
        radius = c["match_dist_frac"] * math.hypot(w, h)

        # greedy nearest matching, closest pairs first
        pairs = []
        for ti, tr in enumerate(self.tracks):
            for ci, card in enumerate(cards):
                d = math.hypot(card.cx - tr.cx, card.cy - tr.cy)
                if d <= radius:
                    pairs.append((d, ti, ci))
        pairs.sort()
        used_t, used_c = set(), set()
        for d, ti, ci in pairs:
            if ti in used_t or ci in used_c:
                continue
            used_t.add(ti)
            used_c.add(ci)
            self._absorb(self.tracks[ti], cards[ci])

        for ti, tr in enumerate(self.tracks):
            if ti not in used_t:
                tr.missed += 1
        self.tracks = [t for t in self.tracks if t.missed <= c["max_missed"]]

        for ci, card in enumerate(cards):
            if ci not in used_c:
                tr = Track(self._next_id, card.cx, card.cy, card.angle,
                           deque(maxlen=int(c["window"])))
                self._next_id += 1
                self._absorb(tr, card)
                self.tracks.append(tr)

        return self.snapshot()

    def _absorb(self, tr, card):
        a = self.cfg["pos_alpha"]
        if tr.age == 0:
            tr.cx, tr.cy, tr.angle = card.cx, card.cy, card.angle
        else:
            tr.cx += a * (card.cx - tr.cx)
            tr.cy += a * (card.cy - tr.cy)
            aa = self.cfg["angle_alpha"]
            x = ((1 - aa) * math.cos(math.radians(tr.angle))
                 + aa * math.cos(math.radians(card.angle)))
            y = ((1 - aa) * math.sin(math.radians(tr.angle))
                 + aa * math.sin(math.radians(card.angle)))
            tr.angle = math.degrees(math.atan2(y, x))
        tr.history.append(card.bits)
        tr.last_bits = card.bits
        tr.age += 1
        tr.missed = 0

    def snapshot(self):
        out = []
        for tr in self.tracks:
            if tr.age < self.cfg["min_votes"]:
                continue
            bits = tr.vote_bits()
            code_id, raw_id, status = decode_bits(
                bits, self.codebook, self.correction_distance)
            out.append(StableCard(
                track_id=tr.track_id, cx=tr.cx, cy=tr.cy, angle=tr.angle,
                bits=bits, code_id=code_id, raw_id=raw_id, status=status,
                age=tr.age, missed=tr.missed, votes=len(tr.history)))
        out.sort(key=lambda s: s.track_id)
        return out
