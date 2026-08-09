"""
Scramble Ritual — 패턴 엔진

입력  : scramble in [0,1]  (관객 슬라이드의 흐트러짐 정도. 0=정렬, 1=흐트러짐)
출력  : 144셀(12x12) 릴레이 프레임

동작 원리
- 내부 상태 x 는 입력을 비대칭 시간상수로 따라간다.
    흐트러짐 방향(attack)  : tau_attack  (기본 0.135s — 빠르게 무너짐)
    정렬 방향(release)     : tau_release (기본 0.319s — 천천히 회복)
- 각 열 c 의 강도 I[c] 는 두 프로파일을 x 로 보간한 값.
    x=0 : intact 프로파일  -> 바닥에서 I[c]*12 높이의 정적 스파이크
    x=1 : scramble 프로파일 -> 확률 I[c] 의 무작위 점멸
  x 자체를 혼합 온도로 써서 두 패턴을 셀 단위로 섞는다.

하드웨어 보호 (모두 이 레이어에서 1차 강제, 펌웨어가 2차 방어)
- max_on              : 동시 ON 셀 수 상한 (전류·발열 캡)
- relay_cooldown_s    : 같은 릴레이의 최소 재토글 간격 (기계 수명)
- max_flips_per_frame : 프레임당 상태 변경 셀 수 상한 (돌입전류·소음 밀도 캡)
표준 라이브러리만 사용 — 하드웨어 없이 테스트/시뮬레이션 가능.
"""

import math
import random

from .protocol import GRID, N_CELLS

DEFAULTS = {
    "tau_attack": 0.135,
    "tau_release": 0.319,
    "relay_cooldown_s": 0.4,
    "max_flips_per_frame": 24,
    "max_on": 60,
    "seed": 1135,
    # 열별 기본 프로파일 (12개). 실측 데이터로 교체 가능 (config.json).
    "profile_intact":   [0.75, 0.42, 0.58, 0.83, 0.33, 0.67,
                         0.50, 0.92, 0.25, 0.58, 0.42, 0.67],
    "profile_scramble": [0.45, 0.30, 0.55, 0.40, 0.60, 0.35,
                         0.50, 0.45, 0.30, 0.55, 0.40, 0.50],
}


class Engine:
    def __init__(self, cfg=None):
        self.cfg = dict(DEFAULTS)
        if cfg:
            self.cfg.update(cfg)
        self.rng = random.Random(self.cfg["seed"])
        self.x = 0.0                          # 현재 상태 (0=정렬, 1=흐트러짐)
        self.frame = [False] * N_CELLS        # 래치된 릴레이 상태
        self._last_toggle = [-1e9] * N_CELLS
        self.flips_total = 0

    # ── 상태 적분 ────────────────────────────────────────────────
    def update(self, scramble: float, dt: float) -> float:
        scramble = max(0.0, min(1.0, scramble))
        tau = self.cfg["tau_attack"] if scramble > self.x else self.cfg["tau_release"]
        self.x += (scramble - self.x) * (1.0 - math.exp(-dt / max(tau, 1e-4)))
        return self.x

    # ── 열 강도 ──────────────────────────────────────────────────
    def intensities(self):
        a = self.cfg["profile_intact"]
        b = self.cfg["profile_scramble"]
        return [a[c] + (b[c] - a[c]) * self.x for c in range(GRID)]

    # ── 프레임 생성 + 보호 커밋 ──────────────────────────────────
    def generate(self, t: float):
        I = self.intensities()
        temp = self.x
        target = [False] * N_CELLS
        for c in range(GRID):
            spike_h = I[c] * GRID
            for r in range(GRID):
                ordered = r < spike_h                      # 하단 정렬 스파이크
                chaotic = self.rng.random() < I[c]         # 무작위 점멸
                on = chaotic if (self.rng.random() < temp) else ordered
                target[r * GRID + c] = on
        self._cap_on_count(target)
        self._commit(target, t)
        return self.frame

    def _cap_on_count(self, target):
        max_on = self.cfg["max_on"]
        ons = [i for i, v in enumerate(target) if v]
        if len(ons) > max_on:
            for i in self.rng.sample(ons, len(ons) - max_on):
                target[i] = False

    def _commit(self, target, t):
        # 주의: 토글 예산 때문에 커밋은 '부분 적용'이다. 상한은 타깃이 아니라
        # 실제 래치 프레임에 강제해야 한다 — ON 추가는 max_on 미만일 때만 허용.
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
            n_on += 1 if target[i] else -1
            applied += 1
            self.flips_total += 1

    def on_count(self) -> int:
        return sum(self.frame)

    def all_off(self):
        self.frame = [False] * N_CELLS
        return self.frame
