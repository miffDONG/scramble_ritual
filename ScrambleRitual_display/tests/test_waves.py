"""waves.py — 웨이브 필드·타깃 선택·FrameGuard 보호 불변식 검증 (표준 lib만)."""

import unittest

from conductor.protocol import GRID, N_CELLS
from conductor.waves import FrameGuard, WaveField, select_target


def silent_feat():
    return {"rms": 0.0, "bass": 0.0, "bands": [0.0] * GRID,
            "onset": False, "onset_strength": 0.0}


def onset_feat(strength=1.0):
    f = silent_feat()
    f.update(onset=True, onset_strength=strength, rms=0.8, bass=0.8)
    return f


class TestWaveField(unittest.TestCase):
    def test_silence_is_dark(self):
        wf = WaveField(mode="mix")
        field = wf.update(silent_feat(), 0.05)
        self.assertAlmostEqual(max(field), 0.0, places=6)

    def test_onset_spawns_ripple(self):
        wf = WaveField(mode="ripple")
        wf.update(onset_feat(), 0.05)
        field = wf.update(silent_feat(), 0.05)
        self.assertGreater(max(field), 0.3)

    def test_ripple_decays_out(self):
        wf = WaveField(mode="ripple")
        wf.update(onset_feat(), 0.05)
        for _ in range(200):                    # 10초 — 감쇠·확산으로 소멸해야 함
            field = wf.update(silent_feat(), 0.05)
        self.assertEqual(len(wf.ripples), 0)
        self.assertAlmostEqual(max(field), 0.0, places=6)

    def test_spectrum_column_height(self):
        wf = WaveField(mode="spectrum")
        f = silent_feat()
        f["bands"][3] = 1.0
        for _ in range(40):                     # 스무딩 수렴
            field = wf.update(f, 0.05)
        col3 = [field[r * GRID + 3] for r in range(GRID)]
        col0 = [field[r * GRID + 0] for r in range(GRID)]
        self.assertGreater(col3[0], 0.9)        # 바닥은 켜짐
        self.assertGreater(sum(col3), GRID * 0.8)
        self.assertAlmostEqual(sum(col0), 0.0, places=6)

    def test_deterministic_with_seed(self):
        a, b = WaveField(mode="mix"), WaveField(mode="mix")
        seq = [onset_feat(0.9), silent_feat(), onset_feat(0.4), silent_feat()]
        for f in seq:
            fa, fb = a.update(f, 0.05), b.update(f, 0.05)
        self.assertEqual(fa, fb)

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            WaveField(mode="nope")


class TestSelectTarget(unittest.TestCase):
    def test_count_scales_with_energy(self):
        field = [1.0] * N_CELLS
        self.assertEqual(sum(select_target(field, 0.0, 60)), 0)
        self.assertEqual(sum(select_target(field, 0.5, 60)), 30)
        self.assertEqual(sum(select_target(field, 1.0, 60)), 60)

    def test_dim_cells_never_selected(self):
        field = [0.05] * N_CELLS               # 전부 min_level 미만
        self.assertEqual(sum(select_target(field, 1.0, 60)), 0)

    def test_brightest_first(self):
        field = [0.0] * N_CELLS
        field[7] = 1.0
        field[100] = 0.5
        target = select_target(field, 1.0, 60)
        self.assertTrue(target[7] and target[100])
        self.assertEqual(sum(target), 2)


class TestFrameGuard(unittest.TestCase):
    CFG = {"relay_cooldown_s": 0.3, "max_flips_per_frame": 28,
           "max_on": 60, "max_hold_s": 4.0, "rest_s": 1.0, "seed": 7}

    def test_max_on_never_exceeded(self):
        g = FrameGuard(self.CFG)
        all_on = [True] * N_CELLS
        for k in range(60):
            frame = g.commit(all_on, k * 0.05)
            self.assertLessEqual(sum(frame), 60)

    def test_flip_budget(self):
        g = FrameGuard(self.CFG)
        frame = g.commit([True] * N_CELLS, 0.0)
        self.assertLessEqual(g.flips_total, 28)
        self.assertLessEqual(sum(frame), 28)

    def test_cooldown_blocks_retoggle(self):
        g = FrameGuard(self.CFG)
        target = [False] * N_CELLS
        target[0] = True
        g.commit(target, 0.0)
        self.assertTrue(g.frame[0])
        g.commit([False] * N_CELLS, 0.1)       # 쿨다운 0.3s 안 — 못 끔
        self.assertTrue(g.frame[0])
        g.commit([False] * N_CELLS, 0.35)      # 쿨다운 지남 — 꺼짐
        self.assertFalse(g.frame[0])

    def test_hold_limit_forces_off_and_rests(self):
        g = FrameGuard(self.CFG)
        target = [False] * N_CELLS
        target[5] = True
        g.commit(target, 0.0)
        self.assertTrue(g.frame[5])
        g.commit(target, 4.2)                  # max_hold 4.0s 초과 -> 강제 OFF
        self.assertFalse(g.frame[5])
        self.assertEqual(g.forced_off_total, 1)
        g.commit(target, 4.8)                  # rest 1.0s 안 — 재점등 금지
        self.assertFalse(g.frame[5])
        g.commit(target, 5.5)                  # rest 지남 — 재점등 허용
        self.assertTrue(g.frame[5])

    def test_hold_forced_off_ignores_budget(self):
        cfg = dict(self.CFG, max_flips_per_frame=0)   # 예산 0이어도
        g = FrameGuard(cfg)
        g.frame[3] = True                              # 켜진 상태를 직접 심고
        g._on_since[3] = 0.0
        g.commit([True] * N_CELLS, 5.0)                # hold 초과 시점
        self.assertFalse(g.frame[3])                   # 안전 OFF 는 무조건 적용
        self.assertEqual(g.forced_off_total, 1)


if __name__ == "__main__":
    unittest.main()
