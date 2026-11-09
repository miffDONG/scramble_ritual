"""music.py — 온셋 검출·대역 매핑·소스 검증 (numpy 필요, .venv 로 실행)."""

import unittest

import numpy as np

from conductor.music import AudioAnalyzer, SimSource

SR = 44100
BLOCK = 2048
HOP = 1.0 / 20.0                               # 20fps 프레임 간격


def run_analyzer(signal, cfg=None):
    """신호를 20fps 로 훑으며 feat 리스트를 돌려준다."""
    an = AudioAnalyzer(SR, cfg)
    feats = []
    t = 0.0
    while int(t * SR) + BLOCK <= len(signal):
        pos = int(t * SR)
        feats.append(an.feed(signal[pos:pos + BLOCK], t, HOP))
        t += HOP
    return feats


class TestOnsets(unittest.TestCase):
    def test_click_train_detected(self):
        # 0.5초 간격 클릭 8개 (4초) — 온셋 수가 근사해야 함
        sig = np.zeros(4 * SR, dtype=np.float64)
        for k in range(8):
            i = int(k * 0.5 * SR)
            sig[i:i + 256] = np.random.default_rng(k).standard_normal(256) * 0.8
        n_onsets = sum(f["onset"] for f in run_analyzer(sig))
        self.assertGreaterEqual(n_onsets, 5)   # 초반 이력 부족으로 1~2개 놓칠 수 있음
        self.assertLessEqual(n_onsets, 12)

    def test_silence_no_onsets(self):
        sig = np.zeros(2 * SR)
        self.assertEqual(sum(f["onset"] for f in run_analyzer(sig)), 0)
        self.assertEqual(max(f["rms"] for f in run_analyzer(sig)), 0.0)

    def test_steady_tone_no_onsets_after_warmup(self):
        t = np.arange(3 * SR) / SR
        sig = 0.5 * np.sin(2 * np.pi * 440 * t)
        feats = run_analyzer(sig)
        self.assertEqual(sum(f["onset"] for f in feats[20:]), 0)


class TestBands(unittest.TestCase):
    def test_tone_lands_in_right_band(self):
        # 1kHz 사인 — 40..8000Hz 로그 12대역에서 1kHz 를 포함한 대역이 최대
        t = np.arange(2 * SR) / SR
        sig = 0.5 * np.sin(2 * np.pi * 1000 * t)
        feats = run_analyzer(sig)
        bands = feats[-1]["bands"]
        edges = np.geomspace(40.0, 8000.0, 13)
        expect = int(np.searchsorted(edges, 1000.0) - 1)
        self.assertEqual(int(np.argmax(bands)), expect)

    def test_bass_tracks_low_end(self):
        t = np.arange(2 * SR) / SR
        low = 0.5 * np.sin(2 * np.pi * 60 * t)
        high = 0.5 * np.sin(2 * np.pi * 4000 * t)
        self.assertGreater(run_analyzer(low)[-1]["bass"], 0.5)
        self.assertLess(run_analyzer(high)[-1]["bass"], 0.2)


class TestSimSource(unittest.TestCase):
    def test_loop_reads_and_wraps(self):
        src = SimSource()
        a = src.read(0.0, BLOCK)
        b = src.read(4.0, BLOCK)               # 4초 루프 — 랩어라운드 지점
        self.assertEqual(len(a), BLOCK)
        self.assertEqual(len(b), BLOCK)
        np.testing.assert_allclose(a, b)       # 루프 주기와 일치
        self.assertLessEqual(float(np.abs(src.data).max()), 1.0)

    def test_sim_drives_onsets(self):
        src = SimSource()
        sig = src.data[: 4 * SR]
        n = sum(f["onset"] for f in run_analyzer(sig))
        self.assertGreaterEqual(n, 4)          # 킥 8 + 스네어 2 + 햇 — 상당수 검출


if __name__ == "__main__":
    unittest.main()
