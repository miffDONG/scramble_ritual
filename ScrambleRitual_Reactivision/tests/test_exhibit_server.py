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

    def send(self, bundle):
        FakeClient.sent[self.key].append(("#bundle", bundle))


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
    p._frame_lock = threading.Lock()
    p._last_frame_t = 0.0
    return p


def obj(sid, fid, cx, cy, angle=0.0):
    return ExhibitObject(track_id=sid, code_id=fid, nx=0, ny=0, angle=angle, cx=cx, cy=cy)


class TestOscFormatting(unittest.TestCase):
    def test_field_order(self):
        self.assertEqual(X.OSC_OBJ_FIELDS[0], "id")
        self.assertEqual(X.OSC_OBJ_FIELDS, ["id", "bits", "x", "y", "tilt", "tension", "pairId", "heartbeat"])

    def test_id_bits(self):
        self.assertEqual(X.id_bits(0), "00000000")
        self.assertEqual(X.id_bits(12), "00001100")
        self.assertEqual(X.id_bits(215), "11010111")
        self.assertEqual(int(X.id_bits(97), 2), 97)

    def test_three_formats(self):
        vals = [3, "00000011", 0.5, 0.25, 90.0, 0.4]
        self.assertEqual(X.format_osc_args("list", X.OSC_OBJ_FIELDS, vals), vals)
        d = X.format_osc_args("dict", X.OSC_OBJ_FIELDS, vals)
        self.assertEqual(d[:4], ["id", 3, "bits", "00000011"])
        j = X.format_osc_args("json", X.OSC_OBJ_FIELDS, vals)
        self.assertEqual(json.loads(j[0])["id"], 3)
        # the monitor shows the arguments exactly as sent
        self.assertEqual(json.loads(X.osc_args_text("dict", d)), d)
        self.assertEqual(json.loads(X.osc_args_text("list", vals)), vals)
        self.assertEqual(X.osc_args_text("json", j), j[0])

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
                         ("127.0.0.1", "dict", False))     # unknown format -> default dict
        self.assertEqual([t["format"] for t in X.DEFAULT_OSC_TARGETS], ["dict", "td"])


class TestSendOsc(unittest.TestCase):
    def setUp(self):
        FakeClient.sent = {}
        self.rt = {**X.EXHIBIT_DEFAULTS, "osc_enabled": True, "osc_targets": [
            {"name": "SC", "host": "127.0.0.1", "port": 57120, "format": "list", "enabled": True},
            {"name": "JSON", "host": "127.0.0.1", "port": 7000, "format": "json", "enabled": True},
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
        self.assertEqual(next(args[1] for _, args in sc if args[0] == 9), "00001001")
        td0 = json.loads(next(a[0] for _, a in td))
        self.assertEqual(td0["bits"], X.id_bits(td0["id"]))
        self.assertEqual(set(td0), set(X.OSC_OBJ_FIELDS))   # no flip / freq any more
        # same values on both targets
        first_sc = next(args for _, args in sc if args[0] == 9)
        first_td = json.loads(next(args[0] for _, args in td if json.loads(args[0])["id"] == 9))
        self.assertEqual(first_sc, [first_td[k] for k in X.OSC_OBJ_FIELDS])
        self.assertEqual(first_td["tilt"], 45.0)
        self.assertEqual(sorted({r[2] for r in log}), ["JSON", "SC"])
        self.assertEqual(len(log), 4)

    def test_roi_normalization(self):
        rt = {**self.rt, "roi": [0.5, 0.5, 0.5, 0.5]}
        p = bare_pipeline(rt)
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            p._send_osc(rt, [obj(1, 2, 960, 600)], (1280, 800), (640, 400, 1280, 800))
        _, args = FakeClient.sent[("127.0.0.1", 57120)][0]
        x_i, y_i = X.OSC_OBJ_FIELDS.index("x"), X.OSC_OBJ_FIELDS.index("y")
        self.assertAlmostEqual(args[x_i], 0.5)
        self.assertAlmostEqual(args[y_i], 0.5)

    def test_y_is_bottom_up(self):
        rt = {**self.rt, "roi": [0.5, 0.5, 0.5, 0.5]}
        y_i = X.OSC_OBJ_FIELDS.index("y")
        for cy, want in ((400, 1.0), (500, 0.75), (700, 0.25), (799, 0.0025)):
            FakeClient.sent = {}
            p = bare_pipeline(rt)                 # ROI top edge = 1, toward the bottom edge -> 0
            with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
                p._send_osc(rt, [obj(1, 2, 960, cy)], (1280, 800), (640, 400, 1280, 800))
            _, args = FakeClient.sent[("127.0.0.1", 57120)][0]
            self.assertAlmostEqual(args[y_i], want, msg=f"cy={cy}")

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

    def td_bundle(self, objs):
        from pythonosc.osc_bundle import OscBundle
        rt = {**self.rt, "osc_targets": [
            {"name": "TD", "host": "127.0.0.1", "port": 7000, "format": "td", "enabled": True}]}
        p = bare_pipeline(rt)
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            p._send_osc(rt, objs, (1280, 800), None)
        sent = FakeClient.sent[("127.0.0.1", 7000)]
        self.assertEqual(len(sent), 1)                  # one bundle per frame
        bundle = OscBundle(sent[0][1].dgram)
        return p, [(m.address, m.params) for m in bundle]

    def test_td_bundle_slots_from_zero_single_heartbeat(self):
        p, msgs = self.td_bundle([obj(1, 4, 100, 100), obj(2, 9, 700, 500)])
        addrs = [a for a, _ in msgs]
        self.assertEqual(msgs[0], ("/scramble/heartbeat", [p._ritual.heartbeat]))
        self.assertEqual(addrs.count("/scramble/heartbeat"), 1)
        self.assertFalse(any(a.startswith("/scramble/heartbeat") and a != "/scramble/heartbeat"
                             for a in addrs))
        self.assertEqual(msgs[1], ("/scramble/count", [2]))
        self.assertEqual(addrs.count("/scramble/count"), 1)
        self.assertEqual(addrs[2:11], [f"/scramble/{f}" for f in
                                       ("id0", "bits0", "x0", "y0", "tilt0", "tension0",
                                        "pairID0", "pairX0", "pairY0")])
        self.assertIn("/scramble/id1", addrs)
        self.assertNotIn("/scramble/id2", addrs)
        self.assertEqual(len(msgs), 2 + 2 * 9)

    def test_td_pair_position(self):
        _, msgs = self.td_bundle([obj(1, 4, 128, 80), obj(2, 9, 192, 80),
                                  obj(3, 7, 1000, 700)])
        m = dict((a.rsplit("/", 1)[1], v[0]) for a, v in msgs)
        slot = {m[f"id{i}"]: i for i in range(3)}
        for i in range(3):
            pair = m[f"pairID{i}"]
            if pair == -1:
                self.assertEqual((m[f"pairX{i}"], m[f"pairY{i}"]), (-1.0, -1.0))
            else:
                j = slot[pair]
                self.assertAlmostEqual(m[f"pairX{i}"], m[f"x{j}"], places=4)
                self.assertAlmostEqual(m[f"pairY{i}"], m[f"y{j}"], places=4)
        self.assertEqual(m[f"pairID{slot[4]}"], 9)      # the close pair points at each other
        self.assertAlmostEqual(m[f"pairX{slot[4]}"], 0.15, places=4)

    def test_pair_position_td_only(self):
        p = bare_pipeline(self.rt)                     # list + json targets
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            p._send_osc(self.rt, [obj(1, 4, 100, 100), obj(2, 9, 120, 100)], (1280, 800), None)
        _, args = FakeClient.sent[("127.0.0.1", 7000)][0]
        self.assertEqual(set(json.loads(args[0])), set(X.OSC_OBJ_FIELDS))
        _, args = FakeClient.sent[("127.0.0.1", 57120)][0]
        self.assertEqual(len(args), len(X.OSC_OBJ_FIELDS))

    def test_td_heartbeat_without_objects(self):
        p, msgs = self.td_bundle([])
        self.assertEqual(msgs, [("/scramble/heartbeat", [p._ritual.heartbeat]),
                                ("/scramble/count", [0])])

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


class TestFrameCallback(unittest.TestCase):
    def test_on_tuio_frame_sends_and_records(self):
        FakeClient.sent = {}
        rt = {**X.EXHIBIT_DEFAULTS, "osc_enabled": True, "roi": None}
        p = bare_pipeline(rt)
        p.rtv = mock.Mock(format=(1280, 800, 120))
        p._size = (0, 0)
        p.infer_on = True
        objs = [X.ExhibitObject(track_id=1, code_id=4, nx=0.25, ny=0.5, angle=0.0),
                X.ExhibitObject(track_id=2, code_id=9, nx=0.75, ny=0.5, angle=0.0)]
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            p._on_tuio_frame(objs, 42)
        self.assertEqual(p._size, (1280, 800))
        self.assertEqual((objs[0].cx, objs[0].cy), (320.0, 400.0))   # canvas px filled in
        self.assertEqual(len(FakeClient.sent[("127.0.0.1", 57120)]), 2)
        self.assertEqual(len(p._last_osc_log), 22)   # 2 SOUND messages + TD heartbeat, count + 2x9 fields
        self.assertEqual(p._last_objs, objs)
        # paused: nothing sent, graph still computed
        FakeClient.sent = {}
        p.infer_on = False
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            p._on_tuio_frame(objs, 43)
        self.assertEqual(FakeClient.sent, {})
        self.assertEqual(p._last_osc_log, [])
        self.assertIsNotNone(p._graph)


class TestSendClock(unittest.TestCase):
    """reacTIVision goes quiet (~1 bundle/s) with an empty table: the clock
    must keep heartbeat/count at the camera fps."""

    def make(self):
        FakeClient.sent = {}
        rt = {**X.EXHIBIT_DEFAULTS, "osc_enabled": True, "roi": None, "osc_targets": [
            {"name": "TD", "host": "127.0.0.1", "port": 7000, "format": "td", "enabled": True}]}
        p = bare_pipeline(rt)
        p.rtv = mock.Mock(format=(1280, 800, 120))
        p._size = (0, 0)
        p.infer_on = True
        p.tuio = X.TuioReceiver(port=0)          # never started; state driven by hand
        return p

    def counts(self):
        from pythonosc.osc_bundle import OscBundle
        out = []
        for _, b in FakeClient.sent[("127.0.0.1", 7000)]:
            m = {x.address: x.params[0] for x in OscBundle(b.dgram)}
            out.append(m["/scramble/count"])
        return out

    def test_count_drops_to_zero_in_five_clock_ticks(self):
        p = self.make()
        period = p._clock_period(p.rt)
        self.assertAlmostEqual(period, 1 / 120)
        t = p.tuio
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            t._on_obj("/tuio/2Dobj", "alive", 7)
            t._on_obj("/tuio/2Dobj", "set", 7, 4, .5, .5, 0., 0., 0., 0., 0., 0.)
            t._on_obj("/tuio/2Dobj", "fseq", 1)
            p._on_tuio_frame(t.snapshot(), 1)        # what on_frame does
            t._on_obj("/tuio/2Dobj", "alive")        # marker lifted, then reacTIVision idles
            t._on_obj("/tuio/2Dobj", "fseq", 2)
            p._on_tuio_frame(t.snapshot(), 2)
            sent = 0
            for _ in range(6):                       # no more TUIO: only the clock
                p._last_frame_t -= period
                sent += p._clock_tick(period)
        self.assertEqual(sent, 6)
        self.assertEqual(self.counts(), [1, 1, 1, 1, 1, 0, 0, 0])   # 5 missing frames -> 0

    def test_clock_does_not_double_send(self):
        p = self.make()
        p.tuio.last_rx = X.time.perf_counter()
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            p._on_tuio_frame([], 1)
            self.assertFalse(p._clock_tick(p._clock_period(p.rt)))   # real frame just sent
        self.assertEqual(len(FakeClient.sent[("127.0.0.1", 7000)]), 1)

    def test_clock_stops_when_reactivision_gone(self):
        p = self.make()
        p.tuio.last_rx = X.time.perf_counter() - p.CLOCK_ALIVE_S - 1
        self.assertFalse(p._clock_tick(p._clock_period(p.rt)))
        p.tuio = None
        self.assertFalse(p._clock_tick(1 / 120))


class TestContactDistance(unittest.TestCase):
    """The d_near basis is set by changing tension_contact only; the tension
    logic (tension_thresholds / tension_graph) is untouched."""

    def make(self, roi=None):
        p = bare_pipeline({**X.EXHIBIT_DEFAULTS, "roi": roi})
        p._size = (1280, 800)
        p._last_objs = []
        return p

    def test_measure_contact_uses_closest_pair_and_roi_long_side(self):
        p = self.make(roi=[0.0, 0.0, 0.5, 0.5])          # ROI 640x400 -> long side 640
        p._last_objs = [obj(1, 4, 100, 100), obj(2, 9, 164, 100), obj(3, 2, 600, 700)]
        r = p.measure_contact()
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["tension_contact"], 64 / 640, places=4)
        self.assertEqual(sorted(r["ids"]), [4, 9])
        self.assertEqual(p.rt["tension_contact"], r["tension_contact"])
        self.assertEqual(p.rt["object_side_px"], 0.0)
        self.assertEqual(p.rt["tension_contact_src"], "measured")
        # the stored value is exactly what tension_thresholds will use
        from tracker.tension import tension_thresholds
        d_near, d_far, src = tension_thresholds(p.rt, p.rt["object_side_px"], 640)
        self.assertAlmostEqual(d_near, 0.1, places=4)
        self.assertEqual(d_far, 0.5)

    def test_measure_contact_needs_two_objects(self):
        p = self.make()
        self.assertFalse(p.measure_contact()["ok"])
        p._last_objs = [obj(1, 4, 100, 100)]
        self.assertFalse(p.measure_contact()["ok"])
        p._size = (0, 0)
        self.assertFalse(p.measure_contact()["ok"])

    def test_physical_fallback(self):
        p = self.make()
        r = p.contact_from_physical(120, 1200)
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["tension_contact"], 120 / 3 ** 0.5 / 1200, places=4)
        self.assertEqual((p.rt["object_side_mm"], p.rt["table_long_mm"]), (120.0, 1200.0))
        self.assertEqual(p.rt["tension_contact_src"], "physical")
        self.assertFalse(p.contact_from_physical(0, 1200)["ok"])
        self.assertFalse(p.contact_from_physical(1300, 1200)["ok"])
        self.assertFalse(p.contact_from_physical("x", 1200)["ok"])


class TestOverlay(unittest.TestCase):
    def test_draws_without_error(self):
        rt = dict(X.EXHIBIT_DEFAULTS)
        objs = [obj(1, 2, 100, 100, 30.0), obj(2, 5, 300, 200)]
        graph = ([(100, 100), (300, 200)], [(0, 1, 0.5)], [0.5, 0.5])
        vis = X.draw_exhibit_overlay(640, 400, objs, graph,
                                     {"running": True, "pid": 1, "format": "640x400@120",
                                      "error": "boom"}, 118.0, (10, 10, 600, 380), rt, False)
        self.assertEqual(vis.shape, (400, 640, 3))

    def test_camera_background(self):
        rt = dict(X.EXHIBIT_DEFAULTS)
        cam = X.np.full((800, 1280, 3), 200, X.np.uint8)
        vis = X.draw_exhibit_overlay(640, 400, [], None, {}, 0.0, None, rt, camera=cam)
        self.assertEqual(vis.shape, (400, 640, 3))
        self.assertGreater(int(vis[200, 320, 0]), 150)


class FakeCap:
    def __init__(self, idx, api=None):
        self.idx, self.released = idx, False

    def isOpened(self):
        return True

    def set(self, *a):
        return True

    def read(self):
        return True, X.np.zeros((800, 1280, 3), X.np.uint8)

    def release(self):
        self.released = True


class TestCameraBorrow(unittest.TestCase):
    def make(self):
        p = bare_pipeline(dict(X.EXHIBIT_DEFAULTS))
        p.test_mode = False
        p.rtv = mock.Mock()
        p._rtv_dirty = False
        p._cam_want, p._cam, p._cam_frame, p._cam_error, p._cam_seen_t = False, None, None, None, 0.0
        return p

    @mock.patch.object(X.time, "sleep", lambda s: None)
    def test_borrow_and_return(self):
        p = self.make()
        with mock.patch.object(X.cv2, "VideoCapture", FakeCap):
            p.camera_borrow(True)
            p._ensure_camera(p.rt)
        p.rtv.stop.assert_called_once()
        self.assertIsNotNone(p._cam)
        self.assertEqual(p.camera_state()["size"], [1280, 800])
        cap = p._cam
        p.camera_borrow(False)
        p._ensure_camera(p.rt)
        self.assertTrue(cap.released)
        self.assertIsNone(p._cam)
        self.assertTrue(p._rtv_dirty)            # reacTIVision restarts afterwards

    @mock.patch.object(X.time, "sleep", lambda s: None)
    def test_idle_ui_returns_camera(self):
        p = self.make()
        with mock.patch.object(X.cv2, "VideoCapture", FakeCap):
            p.camera_borrow(True)
            p._ensure_camera(p.rt)
            p._cam_seen_t -= p.CAM_IDLE_S + 1
            p._ensure_camera(p.rt)
        self.assertIsNone(p._cam)
        self.assertFalse(p.camera_state()["borrowed"])

    def test_crop_like_rtv(self):
        f = X.np.zeros((800, 1280, 3), X.np.uint8)
        rt = {"rtv_frame_width": 640, "rtv_frame_height": "max", "rtv_xoff": 100, "rtv_yoff": 0}
        self.assertEqual(X.ExhibitPipeline._crop_like_rtv(f, rt).shape[:2], (800, 640))
        rt = {"rtv_frame_width": "max", "rtv_frame_height": "max"}
        self.assertEqual(X.ExhibitPipeline._crop_like_rtv(f, rt).shape[:2], (800, 1280))


if __name__ == "__main__":
    unittest.main()
