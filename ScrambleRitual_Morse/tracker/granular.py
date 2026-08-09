"""
내장 그래뉼러 신스 — Max/MSP 패치가 준비되기 전까지의 사운드 모니터.

SoundMapper 가 내는 파라미터 dict 를 그대로 받아 실제 소리로 바꾼다:
    grain_ms       입자 길이 (319=길고 안정 -> 135=짧고 불안)
    density_hz     초당 입자 수
    pitch_scatter  입자별 피치 산포 (0~1 -> 최대 ±7반음)
    spray_ms       소스 재생 위치 산포
    tone           스펙트럼 틸트 (-1 저역 ~ +1 고역)
    scramble       소스 진행 방식: 0=순차 재생(intact), 1=무작위 점프(scramble)

이벤트: push_event("touch", ...) -> 접점 클릭 트랜지언트.

구현 노트
- 오디오 콜백(별도 스레드)에서 그레인을 샘플 단위로 스케줄·합성.
  파라미터는 dict 참조 교체(원자적)로 전달 — 락 불필요.
- 소스는 기본 합성 금속성 텍스처(비조화 배음) 또는 WAV 파일.
- offline_render() 로 오디오 장치 없이 테스트 가능.
"""

import math

import numpy as np

SR = 48000
MAX_GRAINS = 64


def structured_source(sr=SR, seed=4):
    """시간 구조가 있는 기본 소스 — 구간마다 소리가 달라야 '위치 스크램블'이 들린다.

    [저역 드론 스웰] -> [금속 벨 타격] -> [노이즈 스크레이프] -> [틱 패턴]
    순차 재생(intact)이면 일관된 4박 루프, 무작위 점프(scramble)면 파편 콜라주.
    """
    rng = np.random.default_rng(seed)
    segs = []

    n = int(sr * 1.2)
    t = np.arange(n) / sr
    swell = np.sin(np.pi * t / t[-1]) ** 0.7
    drone = sum(a * np.sin(2 * math.pi * f * t)
                for f, a in [(70, 1.0), (140, 0.4), (212, 0.18)])
    segs.append(drone * swell)

    bell = np.zeros(n)
    for st in (0.05, 0.62):
        m = t >= st
        tt = t[m] - st
        for r, a in [(1.0, 1.0), (2.76, 0.6), (5.40, 0.35), (8.93, 0.18)]:
            bell[m] += a * np.sin(2 * math.pi * 520.0 * r * tt) * np.exp(-tt * 7.0)
    segs.append(bell)

    scrape = rng.standard_normal(n) * (0.25 + 0.75 * np.abs(np.sin(2 * math.pi * 13 * t)))
    scrape *= np.sin(np.pi * t / t[-1]) ** 0.5
    segs.append(scrape)

    ticks = np.zeros(n)
    tick_len = int(sr * 0.008)
    burst = (rng.standard_normal(tick_len)
             * np.hanning(tick_len) * np.sin(2 * math.pi * 2400 * np.arange(tick_len) / sr))
    for k in range(8):
        s = int(k * n / 8)
        ticks[s:s + tick_len] += burst
    segs.append(ticks)

    xf = int(sr * 0.02)                                   # 구간 경계 클릭 방지
    ramp = np.linspace(0, 1, xf)
    out = []
    for s in segs:
        s = s / (np.max(np.abs(s)) or 1.0)
        s[:xf] *= ramp
        s[-xf:] *= ramp[::-1]
        out.append(s)
    out = np.concatenate(out)
    return (out / np.max(np.abs(out))).astype(np.float32)


def metallic_source(sr=SR, dur=3.0, f0=180.0, seed=3):
    """비조화 배음(타격된 금속 막대 비율)의 지속 텍스처. 루프 가능하게 끝을 크로스페이드."""
    rng = np.random.default_rng(seed)
    n = int(sr * dur)
    t = np.arange(n) / sr
    out = np.zeros(n)
    for ratio, amp in [(1.0, 1.0), (2.76, 0.55), (5.40, 0.32), (8.93, 0.18), (13.34, 0.09)]:
        f = f0 * ratio
        # 느린 무작위 AM/FM 으로 '살아있는' 질감
        am = 1.0 + 0.25 * np.sin(2 * math.pi * rng.uniform(0.05, 0.4) * t + rng.uniform(0, 6.28))
        fm = 1.0 + 0.002 * np.sin(2 * math.pi * rng.uniform(0.2, 1.2) * t)
        out += amp * am * np.sin(2 * math.pi * f * fm * t)
    out += 0.04 * rng.standard_normal(n)                  # 미세한 공기 노이즈
    xf = int(sr * 0.05)                                   # 루프 경계 크로스페이드
    ramp = np.linspace(0, 1, xf)
    out[:xf] = out[:xf] * ramp + out[-xf:] * (1 - ramp)
    out = out[: n - xf]
    return (out / np.max(np.abs(out))).astype(np.float32)


def load_wav(path, sr=SR):
    """WAV 로드(모노 변환·리샘플). 실패 시 ValueError."""
    import wave

    with wave.open(path, "rb") as w:
        raw = w.readframes(w.getnframes())
        data = np.frombuffer(raw, dtype={1: np.int8, 2: np.int16, 4: np.int32}[w.getsampwidth()])
        data = data.reshape(-1, w.getnchannels()).mean(axis=1)
        data = data / (np.max(np.abs(data)) or 1.0)
        if w.getframerate() != sr:
            n_out = int(len(data) * sr / w.getframerate())
            data = np.interp(np.linspace(0, len(data) - 1, n_out),
                             np.arange(len(data)), data)
    return data.astype(np.float32)


DEFAULT_PARAMS = {
    "scramble": 0.0, "grain_ms": 319.0, "density_hz": 8.0,
    "pitch_scatter": 0.0, "spray_ms": 0.0, "tone": 0.0, "n": 0.0,
}


class FragmentBank:
    """사운드 N개를 각각 pieces 조각으로 잘라 라벨된 프래그먼트 뱅크를 만든다.

    기본: 합성 사운드 4개 x 4조각 = 16개 (A0..D3).
    각 조각은 경계 클릭 방지용 짧은 페이드를 갖는다.
    """

    NAMES = "ABCDEFGH"

    def __init__(self, sounds, pieces=4, sr=SR, fade_ms=8.0):
        self.sr = sr
        self.pieces = pieces
        self.frags = []
        self.labels = []
        nf = max(int(sr * fade_ms / 1000.0), 2)
        ramp = np.linspace(0.0, 1.0, nf, dtype=np.float32)
        for si, s in enumerate(sounds):
            s = np.asarray(s, dtype=np.float32)
            s = s / (np.max(np.abs(s)) or 1.0)
            seg = len(s) // pieces
            for pi in range(pieces):
                f = s[pi * seg:(pi + 1) * seg].copy()
                f[:nf] *= ramp
                f[-nf:] *= ramp[::-1]
                self.frags.append(f)
                self.labels.append(f"{self.NAMES[si % len(self.NAMES)]}{pi}")
        # 스프레드 배정 순서: A0,B0,C0,D0,A1,... — 오브제가 적어도 사운드가 다양하게
        ns = len(sounds)
        self.spread_order = [si * pieces + pi
                             for pi in range(pieces) for si in range(ns)]

    @staticmethod
    def synth_sounds(sr=SR, dur=2.4, seed=4):
        """합성 기본 사운드 4종 — 각 사운드의 4분할 조각들이 서로 구별되게 설계."""
        rng = np.random.default_rng(seed)
        n = int(sr * dur)
        t = np.arange(n) / sr
        sounds = []

        a = np.zeros(n)                       # A: 드론 — 배음 강조가 시간을 따라 이동
        for k in range(1, 7):
            amp = np.exp(-0.5 * (t / dur * 6.0 - k) ** 2)
            a += amp * np.sin(2 * math.pi * 65.0 * k * t) / k ** 0.5
        sounds.append(a)

        b = np.zeros(n)                       # B: 벨 4타 — 조각마다 음고·타점·감쇠가 다름
        # 타점 오프셋과 감쇠를 다르게 해 포락선(=오브제 형상)도 조각마다 구별되게
        strikes = zip((0.02, 0.72, 1.38, 2.06),          # 조각 내 위치 이동
                      (392.0, 466.0, 523.0, 622.0),
                      (10.0, 7.0, 4.5, 3.0))             # 짧은 핑 -> 긴 울림
        for st, f, decay in strikes:
            m = t >= st
            tt = t[m] - st
            for r, amp in [(1.0, 1.0), (2.76, 0.55), (5.40, 0.30)]:
                b[m] += amp * np.sin(2 * math.pi * f * r * tt) * np.exp(-tt * decay)
        sounds.append(b)

        am = 2 * math.pi * (6.0 * t + (22.0 - 6.0) / (2 * dur) * t ** 2)
        c = rng.standard_normal(n) * (0.25 + 0.75 * np.abs(np.sin(am)))
        c += 0.3 * np.sin(2 * math.pi * (900.0 * t + (2800.0 - 900.0) / (2 * dur) * t ** 2))
        sounds.append(c)                      # C: 스크레이프 — AM 가속 + 상승 처프

        d = np.zeros(n)                       # D: 틱 — 점점 빨라지고 높아짐
        tlen = int(sr * 0.009)
        env = np.hanning(tlen)
        pos, k = 0.02, 0
        while pos < dur - 0.05 and k < 24:
            f = 1800.0 + k * 180.0
            s0 = int(pos * sr)
            d[s0:s0 + tlen] += (env * np.sin(2 * math.pi * f * np.arange(tlen) / sr)
                                + 0.4 * env * rng.standard_normal(tlen))
            pos += max(0.06, 0.30 * 0.85 ** k)
            k += 1
        sounds.append(d)
        return sounds

    @classmethod
    def synth(cls, sr=SR):
        return cls(cls.synth_sounds(sr), pieces=4, sr=sr)

    @classmethod
    def from_wavs(cls, paths, sr=SR):
        return cls([load_wav(p, sr) for p in paths[:8]], pieces=4, sr=sr)


class Arranger:
    """오브제 id <-> 프래그먼트 배정. 보이는 오브제의 왼->오 순서 = 재생 순서.

    새 오브제가 나타나면 스프레드 순서(A0,B0,C0,D0,A1,...)로 조각을 배정하고,
    트랙이 사라지면 조각을 회수한다. 겹침으로 가려진 오브제의 조각은
    순서에서 빠진다 (포개면 그 목소리가 사라진다).
    """

    def __init__(self, bank: FragmentBank):
        self.bank = bank
        self._rank = {f: i for i, f in enumerate(bank.spread_order)}
        self._free = list(bank.spread_order)
        self._of = {}                          # logical id -> frag index

    def update(self, state, width):
        for lid, _ in state.objects:
            if lid not in self._of and self._free:
                self._of[lid] = self._free.pop(0)
        tracks = getattr(state, "tracks", None)
        if tracks is not None:
            for lid in [i for i in self._of if i not in tracks]:
                self._free.append(self._of.pop(lid))
            self._free.sort(key=self._rank.get)
        vis = sorted(state.objects, key=lambda o: o[1].cx)
        order = [self._of[lid] for lid, _ in vis if lid in self._of]
        pans = [min(max(d.cx / width, 0.05), 0.95)
                for lid, d in vis if lid in self._of]
        return order, pans

    def label(self, lid):
        fi = self._of.get(lid)
        return self.bank.labels[fi] if fi is not None else ""


class GranularEngine:
    def __init__(self, source=None, sr=SR, gain=0.5, seed=11, bank=None):
        self.sr = sr
        self.gain = gain
        if bank is None:
            # 단일 소스가 주어지면 그것을 4조각 뱅크로, 아니면 합성 4사운드 x 4조각
            bank = (FragmentBank([source], pieces=4, sr=sr)
                    if source is not None else FragmentBank.synth(sr))
        self.bank = bank
        self.rng = np.random.default_rng(seed)
        self._params = dict(DEFAULT_PARAMS)
        self._events = []                  # 콜백에서 pop (GIL 원자적 append/clear)
        self.grains = []                   # [(buf, pos)] buf=(n,2) float32
        self.playhead = 0.0                # 테이프 진행 위치 (intact 모드)
        self._next_spawn = 0.0             # 다음 그레인까지 남은 샘플
        self._lp = [0.0, 0.0]              # 톤 틸트 필터 상태 (L, R)
        self.spawned = 0                   # 통계 (테스트용)
        self._order = None
        self._pans = []
        self.set_arrangement(list(range(len(bank.frags))))

    # ── 메인 스레드 API ──────────────────────────────────────────
    def set_params(self, p: dict):
        self._params = {**self._params, **{k: float(v) for k, v in p.items()}}

    def set_arrangement(self, order, pans=None):
        """프래그먼트 재생 순서(와 조각별 팬)를 교체한다 — '테이프'를 다시 잇는다.

        order: 프래그먼트 인덱스 리스트 (보이는 오브제의 왼->오 순서).
        빈 리스트면 전체 캐노니컬 순서로 폴백 (무음 방지).
        """
        pairs = [(f, (pans[i] if pans and i < len(pans) else None))
                 for i, f in enumerate(order) if 0 <= f < len(self.bank.frags)]
        if not pairs:
            pairs = [(f, None) for f in range(len(self.bank.frags))]
        new_order = [f for f, _ in pairs]
        if new_order != self._order:
            frags = [self.bank.frags[f] for f in new_order]
            src = np.concatenate(frags)
            bounds = np.cumsum([len(f) for f in frags])
            self.src = src                 # 참조 교체 — 콜백 스레드와 GIL 원자적
            self._bounds = bounds
            self._order = new_order
            self.playhead = self.playhead % len(src)
        self._pans = [p for _, p in pairs]

    def push_event(self, ev: str, pan: float = 0.5):
        if len(self._events) < 32:
            self._events.append((ev, pan))

    # ── 그레인 생성 ──────────────────────────────────────────────
    def _spawn(self, p):
        if len(self.grains) >= MAX_GRAINS:
            return
        x = p["scramble"]
        glen = max(int(self.sr * p["grain_ms"] / 1000.0), 64)
        semi = self.rng.uniform(-1, 1) * p["pitch_scatter"] * 7.0   # 기울기 -> 피치 산포
        scrambled = self.rng.random() < x
        if scrambled:
            # 파편: 무작위 위치 + 추가 디튠 + 악센트 + 날카로운 어택-감쇠 창
            start = self.rng.uniform(0, len(self.src))
            semi += self.rng.uniform(-1, 1) * 3.0 * x
            amp = self.rng.uniform(0.5, 1.2)
            env = np.exp(-np.linspace(0.0, 5.0, glen))
            atk = max(int(self.sr * 0.002), 2)
            env[:atk] *= np.linspace(0.0, 1.0, atk)
        else:
            # 온전: 순차 재생(+spray 지터) + 고른 음량 + 부드러운 창 -> 이어 들으면 원곡
            spray = p["spray_ms"] / 1000.0 * self.sr
            start = self.playhead + self.rng.uniform(-spray, spray)
            amp = 0.8
            env = np.hanning(glen)
        rate = 2.0 ** (semi / 12.0)
        src, bounds, pans = self.src, self._bounds, self._pans
        idx = (start + np.arange(glen) * rate) % len(src)
        g = src[idx.astype(np.intp)] * (env * amp).astype(np.float32)
        # 팬: 이 그레인이 속한 프래그먼트의 오브제 위치 — 조각 소리가 오브제 쪽에서 남
        fi = int(np.searchsorted(bounds, start % len(src), side="right"))
        base = pans[fi] if fi < len(pans) and pans[fi] is not None \
            else self.rng.uniform(0.15, 0.85)
        pan = min(max(base + self.rng.uniform(-0.1, 0.1), 0.08), 0.92)
        buf = np.stack([g * math.cos(pan * math.pi / 2),
                        g * math.sin(pan * math.pi / 2)], axis=1)
        self.grains.append([buf, 0])
        self.spawned += 1

    def _spawn_click(self, pan):
        n = int(self.sr * 0.006)
        g = (self.rng.standard_normal(n) * np.hanning(n) * 0.55).astype(np.float32)
        buf = np.stack([g * math.cos(pan * math.pi / 2),
                        g * math.sin(pan * math.pi / 2)], axis=1)
        self.grains.append([buf, 0])

    # ── 오디오 콜백 ──────────────────────────────────────────────
    def render_block(self, frames: int) -> np.ndarray:
        p = self._params
        out = np.zeros((frames, 2), np.float32)

        while self._events:
            ev, pan = self._events.pop()
            if ev in ("touch", "merge"):
                self._spawn_click(pan)

        # 밀도 기반 스폰 — 정렬: 규칙적 간격 / 흐트러짐: 불규칙 + 클러스터 버스트
        x = p["scramble"]
        interval = self.sr / max(p["density_hz"], 0.1)
        jit = 0.08 + 0.75 * x
        remaining = frames
        while self._next_spawn < remaining:
            self._spawn(p)
            if self.rng.random() < 0.3 * x:               # 파편 뭉침 (스터터)
                self._spawn(p)
            self._next_spawn += interval * self.rng.uniform(1 - jit, 1 + jit)
        self._next_spawn -= frames
        self.playhead = (self.playhead + frames) % len(self.src)

        # 활성 그레인 합성
        alive = []
        for g in self.grains:
            buf, pos = g
            take = min(frames, len(buf) - pos)
            out[:take] += buf[pos:pos + take]
            g[1] += take
            if g[1] < len(buf):
                alive.append(g)
        self.grains = alive

        # 톤 틸트: 원포올 LP, tone -1(어두움)~+1(밝음=원음에 가깝게)
        # 콜백 스레드 성능을 위해 numpy 스칼라가 아닌 순수 float 로 IIR 처리
        cutoff = 900.0 * (2.0 ** (p["tone"] * 2.0 + 1.0))
        a = math.exp(-2 * math.pi * cutoff / self.sr)
        if a > 0.02:
            k = 1.0 - a
            l, r = self._lp
            cl, cr = out[:, 0].tolist(), out[:, 1].tolist()
            for i in range(frames):
                l += (cl[i] - l) * k
                r += (cr[i] - r) * k
                cl[i] = l
                cr[i] = r
            out[:, 0], out[:, 1] = cl, cr
            self._lp = [l, r]

        # 소프트 리미터
        np.tanh(out * self.gain * 2.0, out=out)
        return out * 0.85

    # ── 실시간 스트림 / 오프라인 렌더 ────────────────────────────
    def start_stream(self, blocksize=512, device=None):
        import sounddevice as sd

        def cb(outdata, frames, time_info, status):
            outdata[:] = self.render_block(frames)

        self.stream = sd.OutputStream(samplerate=self.sr, channels=2,
                                      blocksize=blocksize, callback=cb,
                                      device=device)
        self.stream.start()
        return self.stream

    def output_name(self):
        """현재 스트림이 향하는 출력 장치 이름 (진단용)."""
        import sounddevice as sd
        dev = self.stream.device if getattr(self, "stream", None) else None
        if isinstance(dev, (list, tuple)):
            dev = dev[1]
        if dev is None:
            dev = sd.default.device[1]
        try:
            return sd.query_devices(dev)["name"]
        except Exception:
            return str(dev)

    def stop_stream(self):
        if getattr(self, "stream", None):
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def offline_render(self, seconds: float, blocksize=512) -> np.ndarray:
        blocks = [self.render_block(blocksize)
                  for _ in range(int(seconds * self.sr / blocksize))]
        return np.concatenate(blocks)
