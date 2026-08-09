"""
Scramble Ritual — 음악 반응 웨이브 필드 (12x12)

music.AudioAnalyzer 가 뽑은 특징(대역 에너지·온셋·RMS)을 받아 전자석 매트릭스로
페로플루이드에 '보이는' 웨이브 패턴을 만든다. 표준 라이브러리만 사용 — numpy 불필요.

레이어 (mode 가중치로 혼합):
    ripple   : 온셋(타격) -> 무작위 지점에서 원형 파문이 퍼져나감
    sweep    : 저역 에너지 -> 그리드를 가로지르는 진행파. 강한 온셋에 방향 회전
    spectrum : 12열 = 12대역, 열 높이(아래->위) = 대역 레벨

파이프라인:
    WaveField.update(feat, dt) -> float 필드(0..1, 144칸)
    select_target(field, energy, max_on) -> 에너지 비례 개수의 bool 타깃 프레임
    FrameGuard.commit(target, t) -> 하드웨어 보호를 강제한 래치 프레임

FrameGuard 는 engine.Engine 의 커밋 보호(릴레이 쿨다운·플립 예산·동시 ON 상한)에
전자석 코일 열 보호를 더한 것:
    max_hold_s : 셀 연속 ON 상한. 초과 시 강제 OFF (플립 예산·쿨다운 무시 — 안전 우선)
    rest_s     : 강제 OFF 후 재점등 금지 휴지 시간
"""

import math
import random

from .protocol import GRID, N_CELLS

MODES = {
    "ripple":   {"ripple": 1.0, "sweep": 0.0, "spectrum": 0.0},
    "sweep":    {"ripple": 0.0, "sweep": 1.0, "spectrum": 0.0},
    "spectrum": {"ripple": 0.0, "sweep": 0.0, "spectrum": 1.0},
    "mix":      {"ripple": 1.0, "sweep": 0.7, "spectrum": 0.35},
}

WAVE_DEFAULTS = {
    "seed": 319,
    "ripple_speed": 7.0,        # 파문 확산 속도 (칸/초)
    "ripple_width": 1.4,        # 파문 링 두께 (가우시안 시그마, 칸)
    "ripple_decay_s": 0.9,      # 파문 진폭 감쇠 시간상수
    "max_ripples": 6,           # 동시 파문 수 상한 (오래된 것부터 폐기)
    "sweep_wavelen": 6.0,       # 진행파 파장 (칸)
    "sweep_omega": 4.5,         # 진행파 위상 속도 상한 (rad/s, rms로 가감)
    "spectrum_smooth_s": 0.12,  # 스펙트럼 열 높이 스무딩
    "turn_strength": 0.75,      # 이 이상의 온셋 강도에서 sweep 방향 회전
}


class WaveField:
    def __init__(self, cfg=None, mode="mix"):
        self.cfg = dict(WAVE_DEFAULTS)
        if cfg:
            self.cfg.update(cfg)
        if mode not in MODES:
            raise ValueError(f"mode must be one of {sorted(MODES)}, got {mode!r}")
        self.w = MODES[mode]
        self.mode = mode
        self.rng = random.Random(self.cfg["seed"])
        self.ripples = []                     # [x, y, radius, amp]
        self.phase = 0.0                      # sweep 진행 위상
        self.theta = 0.0                      # sweep 진행 방향 (rad)
        self.levels = [0.0] * GRID            # spectrum 열 높이 (스무딩된 대역 레벨)

    def update(self, feat, dt):
        """feat: music.AudioAnalyzer.feed() 결과 dict. 144칸 float 필드 반환."""
        c = self.cfg
        bands = list(feat.get("bands") or [0.0] * GRID)
        if len(bands) != GRID:                # 대역 수가 달라도 죽지 않게 최근접 리샘플
            bands = [bands[min(int(i * len(bands) / GRID), len(bands) - 1)]
                     for i in range(GRID)]

        # ── 상태 진행 ────────────────────────────────────────────
        if feat.get("onset"):
            s = feat.get("onset_strength", 0.5)
            self.ripples.append([self.rng.uniform(1.5, GRID - 2.5),
                                 self.rng.uniform(1.5, GRID - 2.5),
                                 0.0, 0.4 + 0.6 * s])
            if len(self.ripples) > c["max_ripples"]:
                self.ripples.pop(0)
            if s >= c["turn_strength"]:
                self.theta += self.rng.uniform(0.6, 1.6)

        decay = math.exp(-dt / max(c["ripple_decay_s"], 1e-4))
        for rp in self.ripples:
            rp[2] += c["ripple_speed"] * dt
            rp[3] *= decay
        self.ripples = [rp for rp in self.ripples
                        if rp[3] > 0.05 and rp[2] < GRID * 1.6]

        rms = feat.get("rms", 0.0)
        bass = feat.get("bass", 0.0)
        self.phase += c["sweep_omega"] * (0.25 + 0.75 * rms) * dt

        k_smooth = 1.0 - math.exp(-dt / max(c["spectrum_smooth_s"], 1e-4))
        for i in range(GRID):
            self.levels[i] += (bands[i] - self.levels[i]) * k_smooth

        # ── 필드 합성 ────────────────────────────────────────────
        wr, ws, wp = self.w["ripple"], self.w["sweep"], self.w["spectrum"]
        two_w2 = 2.0 * c["ripple_width"] ** 2
        k_space = 2.0 * math.pi / max(c["sweep_wavelen"], 1e-4)
        cos_t, sin_t = math.cos(self.theta), math.sin(self.theta)

        field = [0.0] * N_CELLS
        for r in range(GRID):
            for col in range(GRID):
                v = 0.0
                if wr and self.ripples:
                    for x, y, rad, amp in self.ripples:
                        d = math.hypot(col - x, r - y)
                        v += wr * amp * math.exp(-((d - rad) ** 2) / two_w2)
                if ws:
                    u = col * cos_t + r * sin_t
                    v += ws * bass * (0.5 + 0.5 * math.sin(k_space * u - self.phase))
                if wp:
                    # r=0 이 바닥. 열 높이 h 아래는 1, 위로 갈수록 0 (경계 1칸 소프트)
                    h = self.levels[col] * GRID
                    v += wp * max(0.0, min(1.0, h - r))
                field[r * GRID + col] = min(1.0, v)
        return field


def select_target(field, energy, max_on, floor=0.0, gain=1.0, min_level=0.15):
    """필드에서 에너지 비례 개수만큼 밝은 셀을 골라 bool 타깃 프레임을 만든다.

    ON 개수 = max_on * clamp(floor + gain*energy) — 곡의 셈여림이 그대로
    패턴 밀도가 된다. min_level 미만 셀은 개수가 남아도 켜지 않는다.
    """
    density = max(0.0, min(1.0, floor + gain * energy))
    n = int(round(max_on * density))
    target = [False] * N_CELLS
    if n <= 0:
        return target
    ranked = sorted(range(N_CELLS), key=lambda i: (-field[i], i))
    for i in ranked[:n]:
        if field[i] < min_level:
            break
        target[i] = True
    return target


class FrameGuard:
    """타깃 프레임 -> 하드웨어 보호를 강제한 래치 프레임 (engine._commit 확장판)."""

    DEFAULTS = {
        "relay_cooldown_s": 0.3,     # 같은 릴레이 최소 재토글 간격
        "max_flips_per_frame": 28,   # 프레임당 상태 변경 상한 (돌입전류 캡)
        "max_on": 60,                # 동시 ON 상한 (전류·발열 캡)
        "max_hold_s": 4.0,           # 전자석 연속 ON 상한 (코일 열 보호)
        "rest_s": 1.0,               # 강제 OFF 후 재점등 금지 시간
        "seed": 1135,
    }

    def __init__(self, cfg=None):
        self.cfg = dict(self.DEFAULTS)
        if cfg:
            self.cfg.update(cfg)
        self.rng = random.Random(self.cfg["seed"])
        self.frame = [False] * N_CELLS
        self._last_toggle = [-1e9] * N_CELLS
        self._on_since = [0.0] * N_CELLS
        self._rest_until = [-1e9] * N_CELLS
        self.flips_total = 0
        self.forced_off_total = 0

    def commit(self, target, t):
        target = list(target)
        max_hold = self.cfg["max_hold_s"]
        rest = self.cfg["rest_s"]

        # 1) 열 보호 강제 OFF — 예산·쿨다운 무시 (안전 우선)
        for i in range(N_CELLS):
            if self.frame[i] and (t - self._on_since[i]) >= max_hold:
                self.frame[i] = False
                self._last_toggle[i] = t
                self._rest_until[i] = t + rest
                self.flips_total += 1
                self.forced_off_total += 1

        # 2) 휴지 중인 셀은 켜지 않는다
        for i in range(N_CELLS):
            if target[i] and t < self._rest_until[i]:
                target[i] = False

        # 3) engine 과 동일한 보호 커밋 — 상한은 래치 프레임에 강제
        cooldown = self.cfg["relay_cooldown_s"]
        budget = self.cfg["max_flips_per_frame"]
        max_on = self.cfg["max_on"]
        pending = [i for i in range(N_CELLS)
                   if target[i] != self.frame[i]
                   and (t - self._last_toggle[i]) >= cooldown]
        self.rng.shuffle(pending)             # 마모를 전 셀에 균등 분산
        n_on = sum(self.frame)
        applied = 0
        for i in pending:
            if applied >= budget:
                break
            if target[i] and n_on >= max_on:
                continue                      # ON 추가는 상한 도달 시 보류
            self.frame[i] = target[i]
            self._last_toggle[i] = t
            if target[i]:
                self._on_since[i] = t
                n_on += 1
            else:
                n_on -= 1
            applied += 1
            self.flips_total += 1
        return self.frame

    def on_count(self):
        return sum(self.frame)

    def all_off(self):
        self.frame = [False] * N_CELLS
        return self.frame
