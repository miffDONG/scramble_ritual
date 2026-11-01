"""
Statistical marker-ID correction (optional: rt["idfix_enabled"]).

reacTIVision sometimes reads a marker as the wrong symbol for a few frames
(0 -> 30, 0 -> 95, or worse, 0 -> 9 which is still inside 0..24). Sound and
TD then flip between identities. A physical object's marker never changes
while it lies on the table, so identity is decided per *physical track*, not
per frame:

  1. tracks     — observations are linked to tracks by position only (gate =
                  idfix_gate x d_near), ignoring the reported id.
  2. evidence   — each real TUIO frame adds log P(raw | true=k) to the
                  track's score S[k], k = 0..24 (weighted majority vote).
                  P is a flat prior (a reading of k supports k) mixed with
                  learned counts, so an unlearned reading >= 25 carries no
                  evidence. Symbol-tree similarity (default.trees) only
                  breaks ties: live data showed real misreads (0 -> 30) are
                  not the nearest tree neighbours.
     memory     — a new track where an id was just seen (and that id is
                  free now) starts with a bonus for that id.
     withhold   — a reading the model cannot explain (raw != id, not a
                  learned misread) is sent as the track's id only when the
                  track is nearly still AND either was read correctly
                  before (flicker on the table) or has been still for
                  STILL_S (a marker misread all the time while lying on the
                  glass). A lifted marker is often still decoded from below,
                  misread (16 -> 38): sending that as 16 would make the
                  lifted id come back, while with idfix off it goes out as
                  38, which the sound ignores.
  3. assignment — all tracks get distinct ids 0..24 at once; stronger
                  evidence wins a contested id, the loser takes its next
                  best *free* id (TD reacts to one marker per id).
  4. stability  — tentative -> confirmed -> locked; the output id changes only
                  after a sustained margin (more frames when locked). A new
                  track is held back up to idfix_first_delay_s so a misread
                  first frame does not reach the sound.
  5. learning   — raw readings of pinned tracks (calibration: the operator
                  names the true id) and, with idfix_learn, of locked tracks
                  are counted into configs/exhibit/id_confusion.json and
                  feed step 2 — e.g. after pinning a "46" marker as 14, later
                  46 readings are evidence for 14.

Input/output are ExhibitObject lists; output objects carry the corrected id
in `code_id` and the reacTIVision reading in `src_id`.
"""

import collections
import json
import math
import os
import threading
from dataclasses import dataclass, field, replace

import numpy as np

from tracker.motion import window_speed

N_IDS = 25          # identities sent downstream: 0..24
N_RAW = 216         # amoeba default.trees symbols: 0..215

TENTATIVE, CONFIRMED, LOCKED = 0, 1, 2
STATE_NAMES = ("tentative", "confirmed", "locked")


# ------------------------------------------------------------ symbol prior

def parse_trees(path):
    """default.trees line -> (root colour, sorted dot counts per white region).
    Line n is symbol n; lines 108.. are the colour-inverted copies."""
    shapes = []
    with open(path, encoding="ascii") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            regions = []
            for ch in s[2:]:              # s[0] colour, s[1] '0' = root depth
                if ch == "1":
                    regions.append(0)
                elif regions:
                    regions[-1] += 1
            shapes.append((s[0], tuple(sorted(regions, reverse=True))))
    return shapes


def _misreads(regions):
    """One-step misreads of a symbol: a dot lost / gained, a hole filled or
    added, two regions merged, a region split by a dot."""
    r = list(regions)
    out = []
    for i, n in enumerate(r):
        rest = r[:i] + r[i + 1:]
        out.append(r[:i] + [n + 1] + r[i + 1:])
        if n > 0:
            out.append(r[:i] + [n - 1] + r[i + 1:])
            out.extend(rest + [k, n - 1 - k] for k in range(n))
        else:
            out.append(rest)
        for j in range(i + 1, len(r)):
            out.append([x for k, x in enumerate(r) if k not in (i, j)] + [n + r[j]])
    out.append(r + [0])
    return {tuple(sorted(x, reverse=True)) for x in out}


def tree_steps(shapes, n_ids=N_IDS, max_steps=3):
    """steps[k, r] = misread steps from symbol k to symbol r (max_steps + 1 =
    farther, or a different root colour)."""
    index = collections.defaultdict(list)
    for r, shape in enumerate(shapes):
        index[shape].append(r)
    steps = np.full((n_ids, len(shapes)), max_steps + 1, dtype=np.int16)
    for k in range(min(n_ids, len(shapes))):
        colour, start = shapes[k]
        seen = {start: 0}
        queue = collections.deque([start])
        while queue:
            u = queue.popleft()
            if seen[u] >= max_steps:
                continue
            for v in _misreads(u):
                if v not in seen:
                    seen[v] = seen[u] + 1
                    queue.append(v)
        for shape, d in seen.items():
            for r in index.get((colour, shape), ()):
                steps[k, r] = d
    return steps


def default_trees_path(install_dir):
    return os.path.join(install_dir, "symbols", "amoeba", "default.trees")


# ------------------------------------------------------------ evidence model

class IdModel:
    """log P(raw | true) for true 0..24, raw 0..N_RAW-1:
    (learned counts + ALPHA * prior) / (row total + ALPHA). The prior is
    flat over misreads; tree similarity is kept apart as a tie-breaker."""

    P_SELF = 0.9        # prior P(k | k)
    ALPHA = 50.0        # prior strength, in pseudo-observations per true id
    RELOG_EVERY = 200   # learned observations between log-table rebuilds

    def __init__(self, steps=None, counts_path=None):
        self._lock = threading.Lock()
        self.counts_path = counts_path
        self.counts = np.zeros((N_IDS, N_RAW))
        self.prior = self._prior()
        self.near = None                       # [raw, true] tree closeness 0..1
        if steps is not None and steps.shape == (N_IDS, N_RAW):
            self.near = np.exp(-steps.astype(float)).T.copy()
        self.dirty = False
        self._pending = 0
        self.load()
        self._rebuild()

    def _prior(self):
        w = np.full((N_IDS, N_RAW), (1.0 - self.P_SELF) / (N_RAW - 1))
        for k in range(N_IDS):
            w[k, k] = self.P_SELF
        return w

    def _rebuild(self):
        with self._lock:
            c = self.counts.copy()
        p = (c + self.ALPHA * self.prior) / (c.sum(axis=1, keepdims=True) + self.ALPHA)
        self.logp = np.log(np.maximum(p, 1e-9)).T.copy()   # [raw, true]
        self._pending = 0

    def loglik(self, raw):
        """Score increment for one reading; unknown raw ids carry no information."""
        if 0 <= raw < N_RAW:
            return self.logp[raw]
        return None

    def explains(self, raw, true_id, p_min):
        """True when learning says `true_id` is often read as `raw`."""
        if not (0 <= raw < N_RAW and 0 <= true_id < N_IDS):
            return False
        return self.logp[raw, true_id] >= math.log(p_min)

    def closeness(self, raw):
        """Tree closeness of a reading to each true id (tie-break only)."""
        if self.near is not None and 0 <= raw < N_RAW:
            return self.near[raw]
        return None

    def learn(self, true_id, raw):
        if not (0 <= true_id < N_IDS and 0 <= raw < N_RAW):
            return
        with self._lock:
            self.counts[true_id, raw] += 1
            self.dirty = True
        self._pending += 1
        if self._pending >= self.RELOG_EVERY:
            self._rebuild()

    def confusions(self, top=20):
        """Learned misreads (true != raw), most frequent first."""
        with self._lock:
            c = self.counts.copy()
        out = [(int(c[k, r]), k, r) for k in range(N_IDS) for r in range(N_RAW)
               if r != k and c[k, r] > 0]
        out.sort(reverse=True)
        return [{"true": k, "raw": r, "n": n} for n, k, r in out[:top]]

    def load(self):
        if not self.counts_path or not os.path.exists(self.counts_path):
            return
        try:
            with open(self.counts_path, encoding="utf-8") as f:
                data = json.load(f).get("counts", {})
            for k, row in data.items():
                for r, n in row.items():
                    k_, r_ = int(k), int(r)
                    if 0 <= k_ < N_IDS and 0 <= r_ < N_RAW:
                        self.counts[k_, r_] = float(n)
        except Exception:
            pass

    def save_if_dirty(self):
        if not self.dirty or not self.counts_path:
            return
        with self._lock:
            c = self.counts.copy()
            self.dirty = False
        data = {"counts": {str(k): {str(r): int(c[k, r]) for r in range(N_RAW) if c[k, r]}
                           for k in range(N_IDS) if c[k].any()}}
        os.makedirs(os.path.dirname(os.path.abspath(self.counts_path)), exist_ok=True)
        tmp = self.counts_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, self.counts_path)

    def reset(self):
        with self._lock:
            self.counts[:] = 0
            self.dirty = True
        self._rebuild()


# ------------------------------------------------------------ tracker

@dataclass
class Track:
    tid: int
    x: float
    y: float
    t_born: float
    t_seen: float
    S: np.ndarray = field(default_factory=lambda: np.zeros(N_IDS))
    n: int = 0                 # readings with information
    state: int = TENTATIVE
    out: int = -1              # id sent downstream (-1 = none yet)
    cand: int = -1             # id challenging `out`
    cand_n: int = 0
    raw: int = -1              # last reacTIVision reading
    obs: object = None         # last ExhibitObject
    emitted: bool = False
    vx: float = 0.0
    vy: float = 0.0
    post: np.ndarray = None
    want: int = -1             # id this track asks for this frame
    pin: int = -1              # operator-set true id (calibration), -1 = none
    tie_sum: np.ndarray = None # summed tree closeness of the readings
    tie_n: int = 0
    seen_raw: set = field(default_factory=set)   # every reading of this track
    still_since: float = None  # nearly still since (None = moving)
    hist: object = None        # recent (t, x, y) for the speed window
    speed: float = 0.0         # px / s over motion.WINDOW_S


class IdFixer:
    """See the module docstring. `update` runs once per real TUIO frame."""

    CAP = 40.0             # max log-score gap kept (~5 clean frames): bounded memory
    MARGIN = 0.5           # posterior lead a challenger needs
    CONFIRM_N, CONFIRM_P = 10, 0.9
    LOCK_N, LOCK_P = 60, 0.99
    LOCK_SWITCH_MULT = 4   # locked tracks need this x idfix_switch_frames
    EMIT_N = 3             # readings before an early (pre-delay) first send
    GAP_S = 0.15           # unseen this long, then back: possibly another object
    GAP_KEEP = 0.3         # score kept after such a gap
    LEARN_SKIP_S = 0.5     # learn only from tracks this old (placement noise)
    MAX_LOST_S = 0.5       # a lifted marker is forgotten within this, whatever the setting
    BRIDGE_MAX_SPEED = 0.12  # table long sides / s: faster = no misread bridging
    STILL_S = 1.0          # an never-confirmed misread is sent after lying still this long
    EXPLAINED_P = 0.05     # learned P(raw | id) at or above this = an expected misread
    TIE_W = 1.0            # weight of tree closeness (< one clean reading)
    MEM_BONUS = 12.0       # score for the id last seen at a new track's spot
    MEM_GATE = 1.5         # memory radius, x gate_px

    def __init__(self, model=None, gate_px=30.0, lost_s=0.5, first_delay_s=0.1,
                 switch_frames=15, learn=False, memory_s=10.0):
        self.model = model or IdModel()
        self.gate_px = float(gate_px)
        self.lost_s = min(self.MAX_LOST_S, float(lost_s))
        self.first_delay_s = float(first_delay_s)
        self.switch_frames = int(switch_frames)
        self.learn = bool(learn)
        self.memory_s = float(memory_s)
        self.memory = {}         # id -> (x, y, t) where it was last sent
        self.table_px = 1000.0   # table long side in canvas px (for speeds)
        self.withheld = 0        # frames an unexplained misread was not sent
        self.tracks = []
        self._next_tid = 1
        self.frames = 0
        self.corrected = 0       # output objects whose id differs from the reading
        self.switches = 0        # emitted id changes
        self.events = collections.deque(maxlen=40)

    def configure(self, gate_px, lost_s, first_delay_s, switch_frames, learn,
                  memory_s=None, table_px=None):
        self.gate_px = max(1.0, float(gate_px))
        self.lost_s = min(self.MAX_LOST_S, max(0.05, float(lost_s)))
        self.first_delay_s = max(0.0, float(first_delay_s))
        self.switch_frames = max(1, int(switch_frames))
        self.learn = bool(learn)
        if memory_s is not None:
            self.memory_s = max(0.0, float(memory_s))
        if table_px is not None:
            self.table_px = max(1.0, float(table_px))

    def reset(self):
        self.tracks = []
        self.memory = {}

    def pin(self, tid, true_id):
        """Calibration: the operator says track `tid` is `true_id` (-1 =
        release). The pin wins every contest and its readings are learned."""
        for t in self.tracks:
            if t.tid == int(tid):
                t.pin = int(true_id) if 0 <= int(true_id) < N_IDS else -1
                return True
        return False

    # -- per frame ----------------------------------------------------------

    def update(self, objs, now):
        self.frames += 1
        self._associate(objs, now)
        self.tracks = [t for t in self.tracks if now - t.t_seen <= self.lost_s]
        for t in self.tracks:
            S = t.S - t.S.max()
            np.maximum(S, -self.CAP, out=S)
            t.S = S
            if t.tie_n:
                S = S + self.TIE_W * t.tie_sum / t.tie_n
            e = np.exp(S - S.max())
            t.post = e / e.sum()
        self._decide()
        self._assign(now)
        out = []
        for t in self.tracks:
            if t.t_seen != now or not t.emitted or t.out < 0:
                continue
            if self._withhold(t, now):
                self.withheld += 1
                continue
            if t.raw != t.out:
                self.corrected += 1
            out.append(replace(t.obs, code_id=t.out, src_id=t.raw, fix_track=t.tid))
            self.memory[t.out] = (t.x, t.y, now)
            if t.pin >= 0 and t.raw >= 0:
                self.model.learn(t.pin, t.raw)
            elif (self.learn and t.state == LOCKED and t.raw >= 0
                    and now - t.t_born >= self.LEARN_SKIP_S):
                self.model.learn(t.out, t.raw)
        return out

    def _explained(self, raw, k):
        return raw == k or self.model.explains(raw, k, self.EXPLAINED_P)

    def _withhold(self, t, now):
        """See `withhold` in the module docstring."""
        if t.pin >= 0 or self._explained(t.raw, t.out):
            return False
        if t.still_since is None:                       # moving misread
            return True
        if any(self._explained(r, t.out) for r in t.seen_raw):
            return False                                # flicker of a confirmed marker
        return now - t.still_since < self.STILL_S

    def _associate(self, objs, now):
        pairs = []
        for oi, o in enumerate(objs):
            for ti, t in enumerate(self.tracks):
                dt = min(max(now - t.t_seen, 0.0), 0.1)
                d = math.hypot(o.cx - (t.x + t.vx * dt), o.cy - (t.y + t.vy * dt))
                if d <= self.gate_px:
                    pairs.append((d, ti, oi))
        pairs.sort()
        used_t, used_o = set(), set()
        for _, ti, oi in pairs:
            if ti in used_t or oi in used_o:
                continue
            used_t.add(ti)
            used_o.add(oi)
            self._observe(self.tracks[ti], objs[oi], now)
        for oi, o in enumerate(objs):
            if oi not in used_o:
                t = Track(tid=self._next_tid, x=o.cx, y=o.cy, t_born=now, t_seen=now)
                self._next_tid += 1
                self._remember(t, now)
                self.tracks.append(t)
                self._observe(t, o, now, new=True)

    def _remember(self, t, now):
        """Bonus for the free id last sent nearest to a new track's spot:
        a marker that comes back misread (12 -> 90) keeps its id."""
        taken = {u.out for u in self.tracks if u.out >= 0}
        best, best_d = -1, self.gate_px * self.MEM_GATE
        for k, (x, y, ts) in list(self.memory.items()):
            if now - ts > self.memory_s:
                del self.memory[k]
                continue
            d = math.hypot(t.x - x, t.y - y)
            if k not in taken and d <= best_d:
                best, best_d = k, d
        if best >= 0:
            t.S = t.S.copy()
            t.S[best] += self.MEM_BONUS

    def _observe(self, t, o, now, new=False):
        if not new:
            dt = now - t.t_seen
            if dt > self.GAP_S:                 # back after a gap: maybe a swapped object
                t.S = t.S * self.GAP_KEEP
                t.state = min(t.state, CONFIRMED)
            if 0 < dt <= 0.1:
                a = 0.5
                t.vx += a * ((o.cx - t.x) / dt - t.vx)
                t.vy += a * ((o.cy - t.y) / dt - t.vy)
            else:
                t.vx = t.vy = 0.0
        if t.hist is None:
            t.hist = collections.deque()
        t.speed = window_speed(t.hist, now, o.cx, o.cy)
        if t.speed < self.BRIDGE_MAX_SPEED * self.table_px:
            if t.still_since is None:
                t.still_since = now
        else:
            t.still_since = None
        t.x, t.y, t.t_seen, t.obs = o.cx, o.cy, now, o
        t.raw = int(o.code_id)
        if len(t.seen_raw) < 64:
            t.seen_raw.add(t.raw)
        ll = self.model.loglik(t.raw)
        if ll is not None:
            t.S = t.S + ll
            t.n += 1
        near = self.model.closeness(t.raw) if t.raw >= N_IDS else None
        if near is not None:
            t.tie_sum = near.copy() if t.tie_sum is None else t.tie_sum + near
            t.tie_n += 1

    def _decide(self):
        """Desired id per track, with hysteresis against the current output."""
        for t in self.tracks:
            if t.pin >= 0:
                t.want = t.pin
                continue
            best = int(np.argmax(t.post))
            if t.out < 0:
                t.want = best
                continue
            if best != t.out and t.post[best] - t.post[t.out] >= self.MARGIN:
                t.cand_n = t.cand_n + 1 if t.cand == best else 1
                t.cand = best
            else:
                t.cand, t.cand_n = -1, 0
            need = (1, self.switch_frames,
                    self.switch_frames * self.LOCK_SWITCH_MULT)[t.state]
            t.want = t.cand if t.cand >= 0 and t.cand_n >= need else t.out

    def _assign(self, now):
        def strength(t):
            # evidence for the wanted id decides a contested id; an id a
            # track only got as a fallback (low posterior) yields to a
            # track that actually reads it
            return (t.pin >= 0, t.state, float(t.post[t.want]), t.n)
        used = set()
        for t in sorted(self.tracks, key=strength, reverse=True):
            if t.want not in used:
                new = t.want
            else:
                free = [k for k in range(N_IDS) if k not in used]
                if not free:
                    new = -1
                elif t.out in free:
                    new = t.out
                else:
                    new = max(free, key=lambda k: (t.post[k], -k))
            if new >= 0:
                used.add(new)
            if new != t.out:
                if t.emitted:
                    self.switches += 1
                    self.events.append({"track": t.tid, "from": t.out, "to": new,
                                        "raw": t.raw, "state": STATE_NAMES[t.state]})
                t.out = new
                t.state = TENTATIVE
                t.cand, t.cand_n = -1, 0
            if t.out < 0:
                continue
            if t.pin >= 0 and t.out == t.pin:
                t.state, t.emitted = LOCKED, True
                continue
            p = float(t.post[t.out])
            if t.state < LOCKED and t.n >= self.LOCK_N and p >= self.LOCK_P:
                t.state = LOCKED
            elif t.state < CONFIRMED and t.n >= self.CONFIRM_N and p >= self.CONFIRM_P:
                t.state = CONFIRMED
            if not t.emitted:
                t.emitted = ((t.n >= self.EMIT_N and p >= self.CONFIRM_P)
                             or now - t.t_born >= self.first_delay_s)

    # -- monitor --------------------------------------------------------------

    def stats(self):
        return {
            "tracks": [{"track": t.tid, "id": t.out, "raw": t.raw,
                        "p": round(float(t.post[t.out]), 3) if t.out >= 0 and t.post is not None else 0.0,
                        "state": STATE_NAMES[t.state], "emitted": t.emitted,
                        "pin": t.pin, "x": round(t.x, 1), "y": round(t.y, 1),
                        "speed": round(t.speed / self.table_px, 3)}
                       for t in self.tracks],
            "frames": self.frames, "corrected": self.corrected,
            "switches": self.switches, "events": list(self.events)[-10:],
            "withheld": self.withheld,
            "learned": self.model.confusions(10),
        }
