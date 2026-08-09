import unittest
from types import SimpleNamespace

import numpy as np

from tracker.granular import (DEFAULT_PARAMS, Arranger, FragmentBank,
                              GranularEngine, metallic_source)


class TestGranular(unittest.TestCase):
    def test_offline_render_produces_audio(self):
        eng = GranularEngine(gain=0.5)
        eng.set_params({**DEFAULT_PARAMS, "density_hz": 20.0})
        out = eng.offline_render(2.0)
        self.assertEqual(out.shape[1], 2)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertLessEqual(float(np.max(np.abs(out))), 1.0)   # 리미터
        rms = float(np.sqrt(np.mean(out ** 2)))
        self.assertGreater(rms, 0.01)                            # 실제로 소리가 남

    def test_density_controls_grain_rate(self):
        lo = GranularEngine()
        lo.set_params({**DEFAULT_PARAMS, "density_hz": 8.0})
        lo.offline_render(2.0)
        hi = GranularEngine()
        hi.set_params({**DEFAULT_PARAMS, "density_hz": 60.0})
        hi.offline_render(2.0)
        self.assertGreater(hi.spawned, lo.spawned * 3)

    def test_event_click_adds_transient(self):
        eng = GranularEngine()
        eng.set_params({**DEFAULT_PARAMS, "density_hz": 0.1})
        eng.offline_render(1.0)              # 기동 직후 스폰된 첫 그레인 소진
        base = eng.offline_render(0.5)       # 다음 스폰까지 ~10초 — 무음 구간
        eng.push_event("touch", pan=0.5)
        clicked = eng.offline_render(0.5)
        self.assertGreater(float(np.max(np.abs(clicked))),
                           max(float(np.max(np.abs(base))) * 1.5, 0.05))

    def test_tone_darkens_spectrum(self):
        def centroid(tone):
            eng = GranularEngine(seed=5)
            eng.set_params({**DEFAULT_PARAMS, "density_hz": 30.0, "tone": tone})
            x = eng.offline_render(2.0)[:, 0]
            spec = np.abs(np.fft.rfft(x))
            freqs = np.fft.rfftfreq(len(x), 1 / eng.sr)
            return float((spec * freqs).sum() / spec.sum())
        self.assertLess(centroid(-1.0), centroid(+1.0) * 0.8)

    def test_intact_vs_scramble_audibly_differ(self):
        # 회귀: '사운드가 동일' 방지 — 정렬은 소스 타임라인을 재구성해야 하고
        # (높은 상관), 스크램블은 그것을 해체해야 한다 (무상관)
        def corr_with_source(x):
            eng = GranularEngine(seed=9)
            eng.set_params({**DEFAULT_PARAMS, "scramble": x,
                            "density_hz": 25.0, "grain_ms": 220.0})
            out = eng.offline_render(3.0)[:, 0]
            ref = eng.src[: len(out)].astype(np.float64)
            return float(np.corrcoef(out, ref)[0, 1])
        self.assertGreater(corr_with_source(0.0), 0.8)    # 실측 ~0.95
        self.assertLess(abs(corr_with_source(1.0)), 0.3)  # 실측 ~0.05

    def test_source_loopable_and_normalized(self):
        src = metallic_source()
        self.assertLessEqual(float(np.max(np.abs(src))), 1.0)
        self.assertGreater(len(src), 48000)


class TestFragments(unittest.TestCase):
    def test_bank_is_4x4(self):
        bank = FragmentBank.synth()
        self.assertEqual(len(bank.frags), 16)
        self.assertEqual(bank.labels[0], "A0")
        self.assertEqual(bank.labels[-1], "D3")
        self.assertEqual(bank.spread_order[:4], [0, 4, 8, 12])   # A0,B0,C0,D0
        for f in bank.frags:
            self.assertLessEqual(float(np.max(np.abs(f))), 1.0)
            self.assertAlmostEqual(float(f[0]), 0.0, places=2)   # 페이드 인

    def test_reorder_changes_output(self):
        # 같은 조각들이라도 순서가 바뀌면 (intact 상태에서) 다른 곡이 돼야 함
        def render(order):
            eng = GranularEngine(seed=9)
            eng.set_arrangement(order)
            eng.set_params({**DEFAULT_PARAMS, "scramble": 0.0,
                            "density_hz": 25.0, "grain_ms": 220.0})
            return eng.offline_render(3.0)[:, 0]
        a = render(list(range(16)))
        b = render(list(range(15, -1, -1)))
        corr = float(np.corrcoef(a, b)[0, 1])
        self.assertLess(abs(corr), 0.5)

    def test_empty_arrangement_falls_back(self):
        eng = GranularEngine()
        eng.set_arrangement([])
        self.assertEqual(len(eng.src),
                         sum(len(f) for f in eng.bank.frags))

    def test_pan_follows_fragment_position(self):
        eng = GranularEngine(seed=3)
        eng.set_arrangement([0, 1], pans=[0.1, 0.9])     # 조각0=왼쪽, 조각1=오른쪽
        eng.set_params({**DEFAULT_PARAMS, "scramble": 0.0,
                        "density_hz": 30.0, "grain_ms": 200.0})
        half = len(eng.bank.frags[0]) / eng.sr           # 조각0 재생 구간만 렌더
        out = eng.offline_render(half * 0.9)
        l = float(np.sqrt(np.mean(out[:, 0] ** 2)))
        r = float(np.sqrt(np.mean(out[:, 1] ** 2)))
        self.assertGreater(l, r * 1.5)                   # 왼쪽이 확실히 큼


class TestArranger(unittest.TestCase):
    def _state(self, objs, tracks=None):
        return SimpleNamespace(
            objects=[(lid, SimpleNamespace(cx=cx)) for lid, cx in objs],
            tracks={i: None for i in (tracks if tracks is not None
                                      else [lid for lid, _ in objs])})

    def test_left_to_right_order(self):
        arr = Arranger(FragmentBank.synth())
        order, pans = arr.update(self._state([(1, 100), (2, 300), (3, 500)]), 640)
        self.assertEqual(order, [0, 4, 8])               # A0, B0, C0
        self.assertEqual([round(p, 2) for p in pans], [0.16, 0.47, 0.78])

    def test_assignment_stable_under_reorder(self):
        arr = Arranger(FragmentBank.synth())
        arr.update(self._state([(1, 100), (2, 300)]), 640)
        order2, _ = arr.update(self._state([(1, 500), (2, 300)]), 640)
        self.assertEqual(order2, [4, 0])                 # id1=A0이 오른쪽으로 가도 A0 유지
        self.assertEqual(arr.label(1), "A0")
        self.assertEqual(arr.label(2), "B0")

    def test_fragment_recycled_after_leave(self):
        arr = Arranger(FragmentBank.synth())
        arr.update(self._state([(1, 100)]), 640)
        arr.update(self._state([], tracks=[]), 640)      # 트랙 소멸 -> 회수
        order, _ = arr.update(self._state([(9, 200)]), 640)
        self.assertEqual(order, [0])                     # 새 오브제가 A0을 재사용


if __name__ == "__main__":
    unittest.main()
