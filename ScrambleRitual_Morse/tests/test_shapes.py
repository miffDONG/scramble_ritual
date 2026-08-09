import math
import unittest

import numpy as np

from tracker.granular import FragmentBank
from tracker.synthetic import bank_shapes, render, waveform_poly_from_audio
from tracker.vision import detect


def ang_diff(a, b):
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


class TestWaveformShapes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bank = FragmentBank.synth()
        cls.shapes = bank_shapes(cls.bank)

    def test_every_fragment_shape_detectable(self):
        # 핵심 회귀: 무음 구간이 많은 조각(틱 등)도 한 덩어리로 검출돼야 함
        for i, poly in enumerate(self.shapes):
            for ang in (0.0, 30.0):
                frame = render([(320, 240, ang)], polys=[poly])
                _, dets = detect(frame)
                self.assertEqual(len(dets), 1,
                                 f"{self.bank.labels[i]} ang={ang}: {len(dets)}개로 검출")
                d = dets[0]
                self.assertLess(math.hypot(d.cx - 320, d.cy - 240), 6.0,
                                self.bank.labels[i])
                # 쐐기형(가늘어지는) 실루엣은 minAreaRect 가 수 도(deg) 기우는
                # 고유 편향이 있음 — 편향은 회전을 따라다니므로 상대 변화는 정확
                self.assertLess(ang_diff(d.angle, ang), 8.0,
                                f"{self.bank.labels[i]} est={d.angle:.1f}")

    def test_shapes_are_distinct(self):
        # 모양이 곧 정체성 — 모든 조각 쌍의 포락선이 달라야 함
        envs = []
        for poly in self.shapes:
            half = -poly[:len(poly) // 2, 1]            # top edge -> 두께 프로파일
            envs.append(half / (half.max() or 1.0))
        for i in range(len(envs)):
            for j in range(i + 1, len(envs)):
                diff = float(np.mean(np.abs(envs[i] - envs[j])))
                self.assertGreater(
                    diff, 0.02,
                    f"{self.bank.labels[i]} vs {self.bank.labels[j]} 너무 비슷 ({diff:.4f})")

    def test_min_thickness_enforced(self):
        silent = np.zeros(48000, dtype=np.float32)      # 완전 무음이어도
        poly = waveform_poly_from_audio(silent)
        half = -poly[:len(poly) // 2, 1]
        self.assertGreaterEqual(float(half.min()), 4.0)  # 최소 두께 유지
        frame = render([(320, 240, 0.0)], polys=[poly])
        _, dets = detect(frame)
        self.assertEqual(len(dets), 1)                   # 끊기지 않음


if __name__ == "__main__":
    unittest.main()
