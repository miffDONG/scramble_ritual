"""Tracker engines (docs/tracker-switch-design.md steps 1-5): config files per engine,
Ritualvision size messages, contact measurement -> size reference, restart rules."""

import math
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from tracker.engines import TRACKER_DEFAULTS, get_engine
from tracker.engines import ritualvision as ERV
from tracker.tuio import ExhibitObject, TuioReceiver
from webui import exhibit_server as X


class RitualvisionConfigTest(unittest.TestCase):
    def test_writes_size_range_into_ritualvision_xml(self):
        cfg = {**TRACKER_DEFAULTS, "tracker": "ritualvision", "rtv_width": 1280, "rtv_height": 720,
               "rv_size_ref_px": 57.4, "rv_size_lo_pct": 10, "rv_size_hi_pct": 8}
        with tempfile.TemporaryDirectory() as d:
            paths = get_engine(cfg).write_configs(cfg, d)
            self.assertEqual(set(paths), {"camera", "rtv", "ritualvision"})
            back = get_engine(cfg).import_configs(d)
        self.assertEqual(back["rv_size_ref_px"], 57.4)
        self.assertEqual((back["rv_size_lo_pct"], back["rv_size_hi_pct"]), (10.0, 8.0))
        self.assertTrue(back["rv_size_on"])
        self.assertEqual(back["rtv_width"], 1280)

    def test_size_off_without_reference(self):
        cfg = {**TRACKER_DEFAULTS, "rv_size_on": True, "rv_size_ref_px": 0.0}
        self.assertIsNone(ERV.size_range(cfg))
        self.assertIn('size_on="false"', ERV.build_ritualvision_xml(cfg))

    def test_size_range(self):
        lo, hi = ERV.size_range({"rv_size_on": True, "rv_size_ref_px": 50.0,
                                 "rv_size_lo_pct": 10, "rv_size_hi_pct": 10})
        self.assertAlmostEqual(lo, 45.0)
        self.assertAlmostEqual(hi, 55.0)
        self.assertIsNone(ERV.size_range({"rv_size_on": False, "rv_size_ref_px": 50.0}))

    def test_contact_ratio_geometry(self):
        # two tiles edge to edge: centre distance sqrt(3) R, fold arm 0.76 R / cos 30
        self.assertAlmostEqual(ERV.CONTACT_RC_RATIO, 1.9737, places=3)


class SizeMessageTest(unittest.TestCase):
    def test_size_attached_to_object(self):
        rx = TuioReceiver(port=0)
        rx._on_obj("/tuio/2Dobj", "alive", 3)
        rx._on_obj("/tuio/2Dobj", "set", 3, 7, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        rx._on_size("/ritualvision/size", 3, 57.25, 1280, 720)
        rx._on_obj("/tuio/2Dobj", "fseq", 1)
        o = rx.snapshot()[0]
        self.assertEqual((o.size_px, o.frame_w, o.frame_h), (57.25, 1280, 720))

    def test_size_dropped_with_session(self):
        rx = TuioReceiver(port=0)
        rx._on_obj("/tuio/2Dobj", "alive", 3)
        rx._on_obj("/tuio/2Dobj", "set", 3, 7, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        rx._on_size("/ritualvision/size", 3, 57.0, 1280, 720)
        rx._on_obj("/tuio/2Dobj", "fseq", 1)
        rx._on_obj("/tuio/2Dobj", "alive")
        rx._on_obj("/tuio/2Dobj", "fseq", 2)
        self.assertEqual(rx.snapshot(), [])
        self.assertEqual(rx._sizes, {})


class _PipeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(X, "CFG_PATH", os.path.join(self.tmp.name, "c.json")),
                        mock.patch.object(X, "CONFIGS_DIR", os.path.join(self.tmp.name, "p")),
                        mock.patch.object(X, "find_exe", lambda *a, **k: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()


class ContactSizeTestMode(_PipeCase):
    """Test mode: the virtual hexagons are drawn with radius _hex_r."""

    def _pipe(self, gap_factor):
        p = X.ExhibitPipeline(autostart=False, test_mode=True)
        p._size = (1000, 800)
        p._hex_r = 50.0
        d = math.sqrt(3) * 50.0 * gap_factor
        p._last_objs = [ExhibitObject(1, 4, 0.3, 0.5, 0.0, cx=300.0, cy=400.0),
                        ExhibitObject(2, 9, 0.3, 0.5, 0.0, cx=300.0 + d, cy=400.0)]
        return p

    def test_touching_sets_reference(self):
        r = self._pipe(1.0).measure_contact()
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["size_ref_px"], ERV.RC_PER_R * 50.0, places=1)
        self.assertAlmostEqual(r["shape_ratio"], ERV.CONTACT_RC_RATIO, places=2)
        self.assertFalse(r["restart"])                    # test mode never runs a tracker

    def test_not_touching_is_rejected(self):
        p = self._pipe(1.25)
        before = p.rt["tension_contact"]
        r = p.measure_contact()
        self.assertFalse(r["ok"])
        self.assertIn("맞닿지", r["error"])
        self.assertEqual(p.rt["tension_contact"], before)   # nothing saved


class ContactSizeRitualvision(_PipeCase):
    """Real path: sizes come from Ritualvision's /ritualvision/size messages."""

    def test_reference_from_tuio_and_restart(self):
        p = X.ExhibitPipeline(autostart=False, tracker="ritualvision")
        p.rt["rtv_autostart"] = True
        p._size = (1280, 720)
        rx = TuioReceiver(port=0)
        p.tuio = rx
        W, H, rc = 1280, 720, 57.0
        dx = ERV.CONTACT_RC_RATIO * rc / W                 # touching, in TUIO units
        a = ExhibitObject(1, 4, 0.4, 0.5, 0.0, cx=0.4 * 1280, cy=360.0)
        b = ExhibitObject(2, 9, 0.4 + dx, 0.5, 0.0, cx=(0.4 + dx) * 1280, cy=360.0)
        p._last_objs = [a, b]
        stop = threading.Event()

        def feed():
            n = 0
            while not stop.is_set():
                n += 1
                rx._on_obj("/tuio/2Dobj", "alive", 1, 2)
                for s, x in ((1, 0.4), (2, 0.4 + dx)):
                    rx._on_obj("/tuio/2Dobj", "set", s, 4 if s == 1 else 9, x, 0.5,
                               0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
                    rx._on_size("/ritualvision/size", s, rc + (0.3 if n % 2 else -0.3), W, H)
                rx._on_obj("/tuio/2Dobj", "fseq", n)
                time.sleep(0.02)

        th = threading.Thread(target=feed, daemon=True)
        th.start()
        try:
            r = p.measure_contact()
        finally:
            stop.set()
            th.join(1.0)
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["size_ref_px"], rc, delta=0.5)
        self.assertTrue(r["restart"])
        self.assertEqual(p.rt["rv_size_src"], "measured")
        self.assertTrue(p._rtv_dirty)
        self.assertEqual(p._restart_status()["phase"], "restarting")


class RestartRulesTest(_PipeCase):
    def test_rv_keys_restart_only_ritualvision(self):
        p = X.ExhibitPipeline(autostart=False)
        p._rtv_dirty = False
        p.update({"rv_size_lo_pct": 12})
        self.assertFalse(p._rtv_dirty)                    # reacTIVision does not read rv_*
        p.update({"tracker": "ritualvision"})
        self.assertTrue(p._rtv_dirty)
        p._rtv_dirty = False
        p.update({"rv_size_lo_pct": 15})
        self.assertTrue(p._rtv_dirty)
        p._rtv_dirty = False
        p.update({"rv_size_shape": 1.97})                 # measurement info only
        self.assertFalse(p._rtv_dirty)

    def test_restart_becomes_playing_on_first_frame(self):
        p = X.ExhibitPipeline(autostart=False)
        rx = TuioReceiver(port=0)
        p.tuio = rx
        with mock.patch.object(p.rtv, "is_running", return_value=True):
            p._restart = {"phase": "restarting", "t": time.perf_counter(), "rx": rx,
                          "base": rx.frame_no, "reason": ""}
            self.assertEqual(p._restart_status()["label"], "다시 시작하는 중")
            rx._on_obj("/tuio/2Dobj", "fseq", 1)
            self.assertEqual(p._restart_status()["label"], "재생중")

    def test_restart_fails_without_frames(self):
        p = X.ExhibitPipeline(autostart=False)
        rx = TuioReceiver(port=0)
        p.tuio = rx
        p._restart = {"phase": "restarting", "t": time.perf_counter() - p.RESTART_TIMEOUT_S - 1,
                      "rx": rx, "base": rx.frame_no, "reason": ""}
        st = p._restart_status()
        self.assertEqual((st["phase"], st["label"]), ("failed", "재시작 실패"))
        self.assertTrue(st["reason"])


if __name__ == "__main__":
    unittest.main()
