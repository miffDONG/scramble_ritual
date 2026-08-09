import math
import unittest

from tracker.scene import SceneTracker, features
from tracker.sound import SoundMapper
from tracker.synthetic import render
from tracker.vision import detect, find_contacts


def ang_diff(a, b):
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def step(tracker, objs):
    frame = render(objs)
    mask, dets = detect(frame)
    state = tracker.update(dets, find_contacts(dets))
    return state, dets, frame


class TestVision(unittest.TestCase):
    def test_position_and_angle_accuracy(self):
        for gt in [(200, 150, 0.0), (320, 240, 35.0), (450, 300, -60.0), (320, 240, 88.0)]:
            frame = render([gt])
            _, dets = detect(frame)
            self.assertEqual(len(dets), 1, f"gt={gt}")
            d = dets[0]
            self.assertLess(math.hypot(d.cx - gt[0], d.cy - gt[1]), 3.0, f"gt={gt}")
            self.assertLess(ang_diff(d.angle, gt[2]), 4.0,
                            f"gt={gt} est={d.angle:.1f}")

    def test_two_objects_separated_no_contact(self):
        frame = render([(160, 240, 0.0), (480, 240, 0.0)])
        _, dets = detect(frame)
        self.assertEqual(len(dets), 2)
        self.assertEqual(find_contacts(dets), [])

    def test_touching_pair_contact_detected(self):
        # 끝과 끝이 4px 간격 — contact_gap_px(6) 이내
        frame = render([(255, 240, 0.0), (255 + 124, 240, 0.0)])
        _, dets = detect(frame)
        self.assertEqual(len(dets), 2)
        contacts = find_contacts(dets)
        self.assertEqual(len(contacts), 1)
        i, j, (px, py) = contacts[0]
        self.assertAlmostEqual(px, 255 + 62, delta=12)   # 접점은 두 오브제 사이
        self.assertAlmostEqual(py, 240, delta=14)


class TestSceneTracker(unittest.TestCase):
    def test_id_persistence_under_motion(self):
        tr = SceneTracker()
        state, _, _ = step(tr, [(160, 240, 0.0), (480, 240, 0.0)])
        ids0 = sorted(lid for lid, _ in state.objects)
        for k in range(1, 11):                       # 프레임당 8px 접근 (겹치진 않게)
            state, _, _ = step(tr, [(160 + 8 * k, 240, 0.0), (480 - 8 * k, 240, 0.0)])
        ids1 = sorted(lid for lid, _ in state.objects)
        self.assertEqual(ids0, ids1)

    def test_merge_then_split(self):
        tr = SceneTracker()
        # 떨어진 상태에서 두 트랙 생성
        step(tr, [(240, 240, 20.0), (400, 240, -20.0)])
        merged_seen = False
        # 점점 접근해 완전히 포개기
        for k in range(12):
            d = 80 * (1 - k / 11.0)
            state, _, _ = step(tr, [(320 - d, 240, 20.0), (320 + d, 240, -20.0)])
            if state.overlaps:
                merged_seen = True
        self.assertTrue(merged_seen, "겹침 그룹이 감지돼야 함")
        self.assertEqual(sorted(state.overlaps[0]), [1, 2])
        ev_types = [e for e, _ in state.events] if state.events else []
        # 다시 분리
        split_seen = False
        for k in range(12):
            d = 80 * (k / 11.0) + 10
            state, _, _ = step(tr, [(320 - d, 240, 20.0), (320 + d, 240, -20.0)])
            split_seen = split_seen or any(e == "split" for e, _ in state.events)
        self.assertTrue(split_seen, "분리(split) 이벤트가 나와야 함")
        self.assertEqual(state.overlaps, [])

    def test_touch_event_fires_once(self):
        tr = SceneTracker()
        step(tr, [(220, 240, 0.0), (520, 240, 0.0)])
        touches = []
        for k in range(15):
            gap = max(4, 170 - 14 * k)
            state, _, _ = step(tr, [(255, 240, 0.0), (255 + 120 + gap, 240, 0.0)])
            touches += [d for e, d in state.events if e == "touch"]
        self.assertEqual(len(touches), 1)
        self.assertEqual(touches[0]["ids"], [1, 2])


class TestFeaturesAndSound(unittest.TestCase):
    def test_disorder_aligned_vs_rotated(self):
        tr1 = SceneTracker()
        aligned, _, _ = step(tr1, [(140 + i * 120, 240, 0.0) for i in range(4)])
        f_aligned = features(aligned, (640, 480))
        tr2 = SceneTracker()
        rotated, _, _ = step(tr2, [(140, 120, 0.0), (260, 360, 45.0),
                                   (420, 120, 88.0), (540, 360, -45.0)])
        f_rot = features(rotated, (640, 480))
        self.assertLess(f_aligned["disorder"], 0.1)
        self.assertGreater(f_rot["disorder"], 0.5)
        self.assertGreater(f_rot["scatter"], f_aligned["scatter"])

    def test_chain_with_contacts(self):
        tr = SceneTracker()
        # 3개를 일렬 접촉 체인으로 (간격 4px)
        objs = [(150 + i * 124, 240, 0.0) for i in range(3)]
        state, _, _ = step(tr, objs)
        f = features(state, (640, 480))
        self.assertEqual(f["n"], 3)
        self.assertAlmostEqual(f["chain"], 1.0)
        self.assertGreater(f["contact_ratio"], 0.9)

    def test_sound_param_ranges(self):
        m = SoundMapper({"smooth_alpha": 1.0})
        lo = m.map({"n": 4, "disorder": 0.0, "scatter": 0.0,
                    "contact_ratio": 0.0, "chain": 0.0, "overlap_ratio": 0.0})
        m2 = SoundMapper({"smooth_alpha": 1.0})
        hi = m2.map({"n": 4, "disorder": 1.0, "scatter": 1.0,
                     "contact_ratio": 1.0, "chain": 0.0, "overlap_ratio": 1.0})
        self.assertAlmostEqual(lo["scramble"], 0.0)
        self.assertAlmostEqual(hi["scramble"], 1.0)
        self.assertAlmostEqual(lo["grain_ms"], 319.0)
        self.assertAlmostEqual(hi["grain_ms"], 135.0)
        self.assertLessEqual(hi["density_hz"], 80.0)

    def test_empty_scene_safe(self):
        tr = SceneTracker()
        state, _, _ = step(tr, [])
        f = features(state, (640, 480))
        self.assertEqual(f["n"], 0)
        p = SoundMapper().map(f)
        self.assertAlmostEqual(p["scramble"], 0.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
