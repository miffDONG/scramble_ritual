import unittest

from tracker.motion import MotionGate
from tracker.reactivision import ExhibitObject

FPS = 60.0
LONG = 1000.0


def ob(sid, cx, cy=100.0):
    return ExhibitObject(track_id=sid, code_id=sid, nx=0, ny=0, angle=0, cx=cx, cy=cy)


class TestMotionGate(unittest.TestCase):
    def run_frames(self, g, frames, enabled=True, **kw):
        out = None
        for objs in frames:
            self.t += 1.0 / FPS
            out = g.update(objs, self.t, LONG, enabled, **kw)
        return out

    def setUp(self):
        self.t = 0.0

    def test_jitter_reads_as_still(self):
        g = MotionGate()
        frames = [[ob(1, 500 + (2 if i % 2 else -2))] for i in range(60)]
        out = self.run_frames(g, frames)
        self.assertEqual(len(out), 1)
        self.assertLess(out[0].speed, 0.05)

    def test_fast_mover_cut_within_hold_and_restored(self):
        g = MotionGate()
        self.run_frames(g, [[ob(1, 500)] for _ in range(30)])
        x, cut_at = 500.0, None
        for i in range(60):                      # 0.5 table/s
            x += 0.5 * LONG / FPS
            out = self.run_frames(g, [[ob(1, x)]], cut_speed=0.3, hold_s=0.1)
            if not out and cut_at is None:
                cut_at = i / FPS
        self.assertIsNotNone(cut_at)
        self.assertLess(cut_at, 0.5)
        out = self.run_frames(g, [[ob(1, x)] for _ in range(10)])
        self.assertEqual(out, [])                # still withheld right after stopping
        out = self.run_frames(g, [[ob(1, x)] for _ in range(60)])
        self.assertEqual(len(out), 1)            # slow again for > release_s

    def test_disabled_sends_everything(self):
        g = MotionGate()
        x = 0.0
        frames = []
        for _ in range(60):
            x += 2 * LONG / FPS
            frames.append([ob(1, x)])
        out = self.run_frames(g, frames, enabled=False)
        self.assertEqual(len(out), 1)
        self.assertGreater(out[0].speed, 1.0)


if __name__ == "__main__":
    unittest.main()
