import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from tracker.reactivision import ExhibitObject
from webui import exhibit_server as X


class FakeClient:
    sent = {}

    def __init__(self, host, port):
        self.key = (host, port)
        FakeClient.sent.setdefault(self.key, [])

    def send_message(self, addr, args):
        FakeClient.sent[self.key].append((addr, args))


def bare_pipeline(rt):
    p = X.ExhibitPipeline.__new__(X.ExhibitPipeline)
    p.lock = threading.Lock()
    p.rt = rt
    p._osc_clients = {}
    p._graph = None
    p._last_node_t = {}
    p.tension_basis = ""
    p.osc_fps = 0.0
    p._osc_last_t = None
    return p


def obj(sid, fid, cx, cy, angle=0.0):
    return ExhibitObject(track_id=sid, code_id=fid, nx=0, ny=0, angle=angle, cx=cx, cy=cy)


class TestOscFormatting(unittest.TestCase):
    def test_field_order(self):
        self.assertEqual(X.OSC_OBJ_FIELDS[0], "id")
        self.assertEqual(X.OSC_OBJ_FIELDS,
                         ["id", "x", "y", "tilt", "tension", "flip", "freq"])

    def test_three_formats(self):
        vals = [3, 0.5, 0.25, 90.0, 0.4, 0, -3.0]
        self.assertEqual(X.format_osc_args("list", X.OSC_OBJ_FIELDS, vals), vals)
        d = X.format_osc_args("dict", X.OSC_OBJ_FIELDS, vals)
        self.assertEqual(d[:4], ["id", 3, "x", 0.5])
        j = X.format_osc_args("json", X.OSC_OBJ_FIELDS, vals)
        self.assertEqual(json.loads(j[0])["id"], 3)
        self.assertIn("id: 3", X.osc_args_text("dict", d))
        self.assertEqual(X.osc_args_text("list", vals).split(", ")[0], "3")

    def test_targets_normalized(self):
        rt = {"osc_targets": [
            {"name": "a", "host": "127.0.0.1", "port": "57120", "format": "list"},
            {"name": "b", "host": "", "port": 7000, "format": "weird", "enabled": False},
            {"bad": 1}, "nope"]}
        t = X.osc_targets(rt)
        self.assertEqual(len(t), 2)
        self.assertEqual(t[0]["port"], 57120)
        self.assertTrue(t[0]["enabled"])
        self.assertEqual((t[1]["host"], t[1]["format"], t[1]["enabled"]),
                         ("127.0.0.1", "list", False))

    def test_object_freq(self):
        self.assertEqual(X.object_freq(7), ((7 * 5) % 12) - 6)
        self.assertEqual(X.object_freq(0), -6.0)


class TestSendOsc(unittest.TestCase):
    def setUp(self):
        FakeClient.sent = {}
        self.rt = {**X.EXHIBIT_DEFAULTS, "osc_enabled": True, "osc_targets": [
            {"name": "SC", "host": "127.0.0.1", "port": 57120, "format": "list", "enabled": True},
            {"name": "TD", "host": "127.0.0.1", "port": 7000, "format": "json", "enabled": True},
            {"name": "off", "host": "127.0.0.1", "port": 9999, "format": "dict", "enabled": False},
        ]}

    def test_multi_target_same_payload(self):
        p = bare_pipeline(self.rt)
        objs = [obj(1, 4, 100, 100), obj(2, 9, 700, 500, 45.0)]
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            log = p._send_osc(self.rt, objs, (1280, 800), None)
        sc = FakeClient.sent[("127.0.0.1", 57120)]
        td = FakeClient.sent[("127.0.0.1", 7000)]
        self.assertEqual(len(sc), 2)
        self.assertEqual(len(td), 2)
        self.assertNotIn(("127.0.0.1", 9999), FakeClient.sent)   # disabled: no client
        self.assertTrue(all(a == "/scramble/obj" for a, _ in sc))
        ids_sc = sorted(args[0] for _, args in sc)
        ids_td = sorted(json.loads(args[0])["id"] for _, args in td)
        self.assertEqual(ids_sc, [4, 9])
        self.assertEqual(ids_td, [4, 9])
        self.assertIsInstance(sc[0][1][0], int)
        # same values on both targets
        first_sc = next(args for _, args in sc if args[0] == 9)
        first_td = json.loads(next(args[0] for _, args in td if json.loads(args[0])["id"] == 9))
        self.assertEqual(first_sc, [first_td[k] for k in X.OSC_OBJ_FIELDS])
        self.assertEqual(first_td["tilt"], 45.0)
        self.assertEqual(sorted({r[2] for r in log}), ["SC", "TD"])
        self.assertEqual(len(log), 4)

    def test_roi_normalization(self):
        rt = {**self.rt, "roi": [0.5, 0.5, 0.5, 0.5]}
        p = bare_pipeline(rt)
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            p._send_osc(rt, [obj(1, 2, 960, 600)], (1280, 800), (640, 400, 1280, 800))
        _, args = FakeClient.sent[("127.0.0.1", 57120)][0]
        self.assertAlmostEqual(args[1], 0.5)
        self.assertAlmostEqual(args[2], 0.5)

    def test_disabled_or_paused_sends_nothing_but_builds_graph(self):
        p = bare_pipeline(self.rt)
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            log = p._send_osc(self.rt, [obj(1, 2, 10, 10), obj(2, 3, 20, 20)],
                              (1280, 800), None, send=False)
        self.assertEqual(log, [])
        self.assertEqual(FakeClient.sent, {})
        self.assertIsNotNone(p._graph)
        self.assertEqual(set(p._last_node_t), {1, 2})
        rt = {**self.rt, "osc_enabled": False}
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            self.assertEqual(p._send_osc(rt, [obj(1, 2, 10, 10)], (1280, 800), None), [])

    def test_no_targets(self):
        rt = {**self.rt, "osc_targets": []}
        p = bare_pipeline(rt)
        log = p._send_osc(rt, [obj(1, 2, 10, 10)], (1280, 800), None)
        self.assertEqual(log[0][0], "osc")


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = os.path.join(self.tmp.name, "exhibit_config.json")
        self.cfg_dir = os.path.join(self.tmp.name, "profiles")
        self.patches = [mock.patch.object(X, "CFG_PATH", self.cfg_path),
                        mock.patch.object(X, "CONFIGS_DIR", self.cfg_dir),
                        mock.patch.object(X, "find_exe", lambda *a, **k: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_first_run_writes_defaults(self):
        p = X.ExhibitPipeline(autostart=False)
        self.assertTrue(os.path.isfile(self.cfg_path))
        self.assertEqual(p.rt["rtv_fps"], 120)
        self.assertEqual(p.rt["rtv_width"], 0)
        self.assertEqual([t["port"] for t in p.rt["osc_targets"]], [57120, 7000])
        self.assertFalse(p.rt["rtv_autostart"])

    def test_update_whitelist_and_dirty(self):
        p = X.ExhibitPipeline(autostart=False)
        p._rtv_dirty = False
        applied = p.update({"tension_fold": "avg", "bogus": 1, "rtv_gradient": 40,
                            "osc_targets": [{"name": "x", "host": "h", "port": "1"}]})
        self.assertEqual(set(applied), {"tension_fold", "rtv_gradient", "osc_targets"})
        self.assertTrue(p._rtv_dirty)
        self.assertEqual(p.rt["osc_targets"][0]["port"], 1)
        self.assertNotIn("bogus", p.rt)

    def test_profile_roundtrip(self):
        p = X.ExhibitPipeline(autostart=False)
        p.update({"rtv_width": 800, "rtv_height": 600, "rtv_fps": 60, "osc_prefix": "/x"})
        p.set_roi([0.1, 0.2, 0.5, 0.5])
        name = p.save_cfg("현장 A")
        self.assertEqual(name, "현장 A")
        self.assertIn("현장 A", X.list_config_names())
        p.reset_cfg()
        self.assertEqual(p.rt["rtv_width"], 0)
        self.assertTrue(p.load_cfg("현장 A"))
        self.assertEqual((p.rt["rtv_width"], p.rt["rtv_fps"], p.rt["osc_prefix"]),
                         (800, 60, "/x"))
        self.assertEqual(p.rt["roi"], [0.1, 0.2, 0.5, 0.5])
        self.assertTrue(p._rtv_dirty)
        # the active config mirrors the loaded profile
        with open(self.cfg_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["rtv_width"], 800)

    def test_calibrate_side_needs_canvas(self):
        p = X.ExhibitPipeline(autostart=False)
        self.assertFalse(p.calibrate_side(0, 0, 0.5, 0)["ok"])
        p._size = (1000, 500)
        r = p.calibrate_side(0.1, 0.5, 0.3, 0.5)
        self.assertTrue(r["ok"])
        self.assertEqual(r["object_side_px"], 200.0)
        self.assertEqual(p.rt["object_side_px"], 200.0)


class TestTuioRebind(unittest.TestCase):
    def test_ensure_retries_after_port_busy(self):
        import socket
        port = 33397
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        blocker.bind(("127.0.0.1", port))
        p = bare_pipeline({**X.EXHIBIT_DEFAULTS, "rtv_tuio_port": port, "rtv_autostart": False})
        p.tuio = None
        p._tuio_dirty = True
        p._rtv_dirty = False
        p.rtv = mock.Mock()
        try:
            p._ensure(p.rt)
            self.assertIsNotNone(p.tuio.error)            # bind failed: port busy
            p._ensure(p.rt)                                # within the retry interval: same receiver
            first = p.tuio
            self.assertIs(p.tuio, first)
        finally:
            blocker.close()
        p._tuio_try_t = 0.0                                # interval elapsed
        p._ensure(p.rt)
        try:
            self.assertIsNot(p.tuio, first)
            self.assertIsNone(p.tuio.error)                # re-bound once the port was free
        finally:
            p.tuio.stop()


class TestOverlay(unittest.TestCase):
    def test_draws_without_error(self):
        rt = dict(X.EXHIBIT_DEFAULTS)
        objs = [obj(1, 2, 100, 100, 30.0), obj(2, 5, 300, 200)]
        graph = ([(100, 100), (300, 200)], [(0, 1, 0.5)], [0.5, 0.5])
        vis = X.draw_exhibit_overlay(640, 400, objs, graph,
                                     {"running": True, "pid": 1, "format": "640x400@120",
                                      "error": "boom"}, 118.0, (10, 10, 600, 380), rt, False)
        self.assertEqual(vis.shape, (400, 640, 3))


if __name__ == "__main__":
    unittest.main()
