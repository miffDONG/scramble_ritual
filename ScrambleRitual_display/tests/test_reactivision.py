import math
import os
import tempfile
import threading
import time
import unittest

from tracker import reactivision as R


SAMPLE_L = """reacTIVision 1.5.1 (May 18 2016)

1 videoInput camera found:
\t0: USB Camera
\t\tformat: MJPG
\t\t\t1280x800 120|60|30|15| fps
\t\t\t800x600 120|60|30|15| fps
\t\t\t640x400 120|60|30|15| fps
\t\t\t320x240 120|60|30|15| fps
\t\tformat: YUY2
\t\t\t1280x800 10| fps
\t\t\t800x600 10| fps
\t\t\t640x400 30|15| fps
\t\t\t320x200 59.9|29.9|14.9| fps
2 MIDI out devices found:
\t0: Microsoft MIDI Mapper
\t1: Microsoft GS Wavetable Synth
"""

CAMERA_XML = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<portvideo>
    <camera id="0">
        <capture width="800" height="600" fps="60" compress="true" />
        <settings brightness="default" contrast="default" gain="default" shutter="default" exposure="default" sharpness="default" gamma="default" focus="min" />
        <frame width="max" height="max" xoff="0" yoff="0" />
    </camera>
</portvideo>
"""

RTV_XML = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<reactivision>
    <!--
	<tuio host="127.0.0.1" port="3333" />
	<calibration file="default.grid" invert="xya" />
    -->
    <finger size="0" sensitivity="75" />
    <image display="src" equalize="false" fullscreen="false" />
    <threshold gradient="32" tile="10" threads="max" />
    <calibration file="default.grid" invert=" " />
</reactivision>
"""


class TestListParsing(unittest.TestCase):
    def test_parse_sample(self):
        cams = R.parse_list_output(SAMPLE_L)
        self.assertEqual(len(cams), 1)
        c = cams[0]
        self.assertEqual((c["index"], c["name"]), (0, "USB Camera"))
        self.assertEqual([f["codec"] for f in c["formats"]], ["MJPG", "YUY2"])
        mjpg = c["formats"][0]["modes"]
        self.assertEqual(len(mjpg), 4)
        self.assertEqual(mjpg[0], {"w": 1280, "h": 800, "fps": [120, 60, 30, 15]})
        yuy2 = c["formats"][1]["modes"]
        self.assertEqual(yuy2[-1]["fps"], [59.9, 29.9, 14.9])

    def test_midi_section_ignored(self):
        cams = R.parse_list_output(SAMPLE_L)
        self.assertFalse(any("MIDI" in c["name"] for c in cams))

    def test_default_mode_prefers_mjpg_120(self):
        cams = R.parse_list_output(SAMPLE_L)
        m = R.default_mode(cams[0])
        self.assertEqual((m["w"], m["h"], m["fps"], m["codec"], m["compress"]),
                         (1280, 800, 120, "MJPG", True))

    def test_default_mode_fallback_without_120(self):
        cam = {"index": 0, "name": "x", "formats": [
            {"codec": "YUY2", "modes": [{"w": 640, "h": 480, "fps": [30, 15]},
                                        {"w": 1280, "h": 720, "fps": [10]}]}]}
        m = R.default_mode(cam)
        self.assertEqual((m["w"], m["h"], m["fps"], m["compress"]), (1280, 720, 10, False))
        self.assertIsNone(R.default_mode({"index": 0, "name": "x", "formats": []}))

    def test_resolve_mode_auto_and_explicit(self):
        cams = R.parse_list_output(SAMPLE_L)
        auto = R.resolve_mode(dict(R.RTV_DEFAULTS), cams)
        self.assertEqual((auto["rtv_width"], auto["rtv_height"], auto["rtv_fps"]), (1280, 800, 120))
        explicit = R.resolve_mode({**R.RTV_DEFAULTS, "rtv_width": 800, "rtv_height": 600,
                                   "rtv_fps": 60}, cams)
        self.assertEqual((explicit["rtv_width"], explicit["rtv_fps"]), (800, 60))
        none = R.resolve_mode(dict(R.RTV_DEFAULTS), [])
        self.assertEqual((none["rtv_width"], none["rtv_height"]), (1280, 800))


class TestXml(unittest.TestCase):
    def test_roundtrip(self):
        cfg = {**R.RTV_DEFAULTS, "rtv_width": 800, "rtv_height": 600, "rtv_fps": 120,
               "rtv_exposure": -7, "rtv_gain": "max", "rtv_calib_invert": "",
               "rtv_gradient": 40, "rtv_display": "dest", "rtv_equalize": True,
               "rtv_tuio_port": 3334}
        with tempfile.TemporaryDirectory() as d:
            cam, rtv = R.write_configs(cfg, d)
            self.assertTrue(os.path.isabs(cam))
            with open(rtv, encoding="latin-1") as f:
                text = f.read()
            # reacTIVision 1.5.1 cannot open camera.xml when it is referenced
            # by an explicit <camera config> tag -> it must be absent
            self.assertNotIn("<camera config", text)
            self.assertIn('invert=" "', text)          # empty -> single space
            back = R.import_xml(cam, rtv)
        for k in ("rtv_width", "rtv_height", "rtv_fps", "rtv_exposure", "rtv_gain",
                  "rtv_gradient", "rtv_display", "rtv_equalize", "rtv_tuio_port",
                  "rtv_focus", "rtv_compress"):
            self.assertEqual(back[k], cfg[k], k)
        self.assertEqual(back["rtv_calib_invert"], "")
        self.assertIsInstance(back["rtv_exposure"], int)
        self.assertEqual(back["rtv_frame_width"], "max")

    def test_import_current_files(self):
        with tempfile.TemporaryDirectory() as d:
            cp, rp = os.path.join(d, "camera.xml"), os.path.join(d, "reacTIVision.xml")
            with open(cp, "w", encoding="latin-1") as f:
                f.write(CAMERA_XML)
            with open(rp, "w", encoding="latin-1") as f:
                f.write(RTV_XML)
            got = R.import_xml(cp, rp)
        self.assertEqual((got["rtv_width"], got["rtv_height"], got["rtv_fps"]), (800, 600, 60))
        self.assertTrue(got["rtv_compress"])
        self.assertEqual(got["rtv_focus"], "min")
        self.assertEqual((got["rtv_finger_size"], got["rtv_finger_sensitivity"]), (0, 75))
        self.assertEqual((got["rtv_gradient"], got["rtv_tile"]), (32, 10))
        self.assertEqual(got["rtv_calib_invert"], "")
        self.assertNotIn("rtv_tuio_port", got)        # only in the comment block

    def test_import_missing_files(self):
        self.assertEqual(R.import_xml("Z:/nope/camera.xml", "Z:/nope/r.xml"), {})

    def test_camera_xml_settings_text(self):
        x = R.build_camera_xml({"rtv_exposure": "-6", "rtv_gain": None, "rtv_focus": 12.0})
        self.assertIn('exposure="-6"', x)
        self.assertIn('gain="default"', x)
        self.assertIn('focus="12"', x)


class TestTreeAndBackup(unittest.TestCase):
    def test_tree_attr(self):
        self.assertIsNone(R.tree_attr(""))
        self.assertIsNone(R.tree_attr("default"))
        self.assertIsNone(R.tree_attr("default.trees"))
        self.assertEqual(R.tree_attr("small"), "symbols/amoeba/small.trees")
        self.assertEqual(R.tree_attr("legacy.trees"), "symbols/amoeba/legacy.trees")
        self.assertEqual(R.tree_attr("symbols/amoeba/x.trees"), "symbols/amoeba/x.trees")
        self.assertNotIn("tree=", R.build_reactivision_xml({"rtv_tree": ""}))
        self.assertIn('tree="symbols/amoeba/small.trees"',
                      R.build_reactivision_xml({"rtv_tree": "small"}))

    def test_write_configs_backs_up_originals_once(self):
        with tempfile.TemporaryDirectory() as d:
            cam = os.path.join(d, "camera.xml")
            with open(cam, "w", encoding="latin-1") as f:
                f.write(CAMERA_XML)
            R.write_configs({**R.RTV_DEFAULTS, "rtv_width": 640, "rtv_height": 400}, d)
            with open(cam + ".orig", encoding="latin-1") as f:
                self.assertEqual(f.read(), CAMERA_XML)
            R.write_configs({**R.RTV_DEFAULTS, "rtv_width": 800, "rtv_height": 600}, d)
            with open(cam + ".orig", encoding="latin-1") as f:
                self.assertEqual(f.read(), CAMERA_XML)          # not overwritten
            self.assertEqual(R.original_xml_paths(d)[0], cam + ".orig")
            self.assertEqual(R.import_xml(*R.original_xml_paths(d))["rtv_width"], 800)
            self.assertEqual(R.import_xml(cam, None)["rtv_width"], 800)

    def test_ensure_local_install_copies_once(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "dist")
            os.makedirs(os.path.join(src, "symbols"))
            with open(os.path.join(src, "reacTIVision.exe"), "wb") as f:
                f.write(b"x")
            local = os.path.join(d, "tools", "reactivision")
            exe = R.ensure_local_install(os.path.join(src, "reacTIVision.exe"), local)
            self.assertEqual(exe, os.path.join(local, "reacTIVision.exe"))
            self.assertTrue(os.path.isdir(os.path.join(local, "symbols")))
            self.assertEqual(R.ensure_local_install(exe, local), exe)


class TestStdoutParsing(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(R._parse_stdout_line("format: 800x600, 120fps"),
                         ("format", (800, 600, 120)))
        self.assertEqual(R._parse_stdout_line("camera: USB Camera"), ("camera", "USB Camera"))
        self.assertEqual(R._parse_stdout_line("no camera found")[0], "error")
        self.assertIsNone(R._parse_stdout_line("render: direct3d"))


class TestProcess(unittest.TestCase):
    def test_missing_exe_reports_error(self):
        p = R.ReactivisionProcess(exe="Z:/nope/reacTIVision.exe")
        cands = R.EXE_CANDIDATES[:]
        R.EXE_CANDIDATES[:] = ["Z:/nope2/reacTIVision.exe"]
        try:
            st = p.start({**R.RTV_DEFAULTS, "rtv_exe": "Z:/nope3/reacTIVision.exe"})
        finally:
            R.EXE_CANDIDATES[:] = cands
        self.assertFalse(st["running"])
        self.assertIn("reacTIVision.exe", st["error"])
        p.stop()                                      # no-op, must not raise

    def test_find_exe_override(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "reacTIVision.exe")
            with open(exe, "wb") as f:
                f.write(b"")
            self.assertEqual(R.find_exe(exe), os.path.abspath(exe))


class TestTuio(unittest.TestCase):
    def test_angle_wrap(self):
        self.assertAlmostEqual(R.tuio_angle_deg(0), 0.0)
        self.assertAlmostEqual(R.tuio_angle_deg(math.pi / 2), 90.0)
        self.assertAlmostEqual(abs(R.tuio_angle_deg(math.pi)), 180.0)
        self.assertAlmostEqual(R.tuio_angle_deg(3 * math.pi / 2), -90.0)
        self.assertAlmostEqual(R.tuio_angle_deg(2 * math.pi - 1e-9), 0.0, places=5)
        self.assertAlmostEqual(R.tuio_angle_deg(0, offset=-90), -90.0)

    def test_set_alive_fseq(self):
        t = R.TuioReceiver()
        t._on_obj("/tuio/2Dobj", "set", 5, 12, 0.25, 0.5, math.pi / 2, 0, 0, 0, 0, 0)
        self.assertEqual(t.snapshot(), [])            # not committed until fseq
        t._on_obj("/tuio/2Dobj", "alive", 5)
        t._on_obj("/tuio/2Dobj", "fseq", 10)
        objs = t.snapshot()
        self.assertEqual(len(objs), 1)
        o = objs[0]
        self.assertEqual((o.track_id, o.code_id), (5, 12))
        self.assertAlmostEqual(o.angle, 90.0)
        self.assertEqual(o.bits, "12")
        self.assertEqual(t.last_fseq, 10)
        # object removed: alive without it
        t._on_obj("/tuio/2Dobj", "alive")
        t._on_obj("/tuio/2Dobj", "fseq", 11)
        self.assertEqual(t.snapshot(), [])
        self.assertEqual(t.frame_no, 2)

    def test_on_frame_callback_per_commit(self):
        t = R.TuioReceiver()
        got = []
        t.on_frame = lambda objs, fseq: got.append((fseq, [o.code_id for o in objs]))
        t._on_obj("/tuio/2Dobj", "set", 1, 3, 0.1, 0.1, 0, 0, 0, 0, 0, 0)
        t._on_obj("/tuio/2Dobj", "alive", 1)
        t._on_obj("/tuio/2Dobj", "fseq", 7)
        t._on_obj("/tuio/2Dobj", "alive")
        t._on_obj("/tuio/2Dobj", "fseq", 8)
        self.assertEqual(got, [(7, [3]), (8, [])])
        # a failing callback is recorded, never raised into the receiver
        t.on_frame = lambda objs, fseq: 1 / 0
        t._on_obj("/tuio/2Dobj", "fseq", 9)
        self.assertIn("division", t.on_frame_error)
        self.assertEqual(t.frame_no, 3)

    def test_snapshot_is_a_copy(self):
        t = R.TuioReceiver()
        t._on_obj("/tuio/2Dobj", "set", 1, 3, 0.1, 0.1, 0, 0, 0, 0, 0, 0)
        t._on_obj("/tuio/2Dobj", "alive", 1)
        t._on_obj("/tuio/2Dobj", "fseq", 1)
        a = t.snapshot()[0]
        a.cx = 999
        self.assertEqual(t.snapshot()[0].cx, 0.0)

    def test_wait_frame(self):
        t = R.TuioReceiver()
        self.assertFalse(t.wait_frame(0.05))

        def later():
            time.sleep(0.05)
            t._on_obj("/tuio/2Dobj", "alive")
            t._on_obj("/tuio/2Dobj", "fseq", 1)
        threading.Thread(target=later).start()
        self.assertTrue(t.wait_frame(1.0))

    def test_udp_roundtrip(self):
        """Real UDP: a python-osc client sends a 2Dobj bundle to the receiver."""
        from pythonosc.osc_bundle_builder import IMMEDIATELY, OscBundleBuilder
        from pythonosc.osc_message_builder import OscMessageBuilder
        from pythonosc.udp_client import SimpleUDPClient
        t = R.TuioReceiver(port=33399).start()
        self.assertIsNone(t.error)
        try:
            n0 = t.frame_no
            b = OscBundleBuilder(IMMEDIATELY)
            for args in (["alive", 7], ["set", 7, 21, 0.5, 0.25, math.pi, 0.0, 0.0, 0.0, 0.0, 0.0],
                         ["fseq", 42]):
                m = OscMessageBuilder("/tuio/2Dobj")
                for a in args:
                    m.add_arg(a)
                b.add_content(m.build())
            SimpleUDPClient("127.0.0.1", 33399).send(b.build())
            self.assertTrue(t.wait_frame(2.0, since=n0))
            o = t.snapshot()[0]
            self.assertEqual((o.track_id, o.code_id, o.nx, o.ny), (7, 21, 0.5, 0.25))
            self.assertEqual(t.last_fseq, 42)
        finally:
            t.stop()


if __name__ == "__main__":
    unittest.main()
