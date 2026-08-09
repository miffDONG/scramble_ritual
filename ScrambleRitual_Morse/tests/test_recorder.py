import os
import tempfile
import unittest

from tracker.recorder import Recorder, load, replay, to_state
from tracker.scene import SceneTracker, features
from tracker.sound import SoundMapper
from tracker.synthetic import render, scenario
from tracker.vision import detect, find_contacts


def record_scenario(path, seconds=3.0, fps=15):
    tr = SceneTracker()
    mapper = SoundMapper()
    rec = Recorder(path)
    for i in range(int(seconds * fps)):
        t = i / fps
        frame = render(scenario(t))
        _, dets = detect(frame)
        state = tr.update(dets, find_contacts(dets))
        params = mapper.map(features(state, (640, 480)))
        rec.write(t, params, state)
    return rec.close()


class TestRecorder(unittest.TestCase):
    def test_record_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.jsonl")
            n = record_scenario(path)
            rows = load(path)
            self.assertEqual(len(rows), n)
            row = rows[0]
            self.assertIn("scramble", row["params"])
            self.assertEqual(len(row["objects"][0]), 4)      # id, cx, cy, angle
            state = to_state(row)
            self.assertTrue(hasattr(state.objects[0][1], "cx"))

    def test_replay_drives_engine(self):
        from tracker.granular import GranularEngine

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.jsonl")
            record_scenario(path, seconds=2.0)
            rows = load(path)
            eng = GranularEngine()
            applied = []
            replay(rows, engine=eng, speed=1e9,          # 대기 없이 즉시 적용
                   on_frame=lambda r: applied.append(r["t"]))
            self.assertEqual(len(applied), len(rows))
            # 마지막 프레임의 파라미터가 엔진에 반영됨
            self.assertAlmostEqual(eng._params["scramble"],
                                   rows[-1]["params"]["scramble"], places=4)
            out = eng.offline_render(0.5)
            self.assertGreater(float(abs(out).max()), 0.0)


if __name__ == "__main__":
    unittest.main()
