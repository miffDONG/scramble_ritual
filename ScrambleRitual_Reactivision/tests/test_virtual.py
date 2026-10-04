import json
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from tracker.virtual import ID_MAX, ID_MIN, LIMIT, VirtualSource, VirtualTable
from webui import exhibit_server as X


class TestVirtualTable(unittest.TestCase):
    def test_add_lowest_free_id_up_to_limit(self):
        t = VirtualTable()
        for _ in range(LIMIT):
            self.assertTrue(t.add()[0])
        ids = sorted(o["id"] for o in t.list())
        self.assertEqual(ids, list(range(ID_MIN, ID_MAX + 1)))
        ok, err = t.add()
        self.assertFalse(ok)
        self.assertIn("25", err)
        t.remove(3)                       # frees ID 3
        t.add()
        self.assertEqual(t.list()[-1]["id"], 3)

    def test_add_spreads_inside_roi(self):
        t = VirtualTable()
        roi = [0.2, 0.1, 0.5, 0.6]
        for _ in range(LIMIT):
            t.add(roi)
        pts = {(round(o["x"], 3), round(o["y"], 3)) for o in t.list()}
        self.assertEqual(len(pts), LIMIT)               # no two on the same spot
        for x, y in pts:
            self.assertTrue(0.2 <= x <= 0.7 and 0.1 <= y <= 0.7)

    def test_update_id_rules(self):
        t = VirtualTable()
        t.add(); t.add()
        self.assertFalse(t.update(0, code_id=1)[0])     # duplicate
        self.assertFalse(t.update(0, code_id=-1)[0])    # out of range
        self.assertFalse(t.update(0, code_id=25)[0])
        track = t.list()[0]["track"]
        self.assertTrue(t.update(0, code_id=24)[0])
        self.assertEqual(t.list()[0]["id"], 24)
        self.assertNotEqual(t.list()[0]["track"], track)   # new marker = new session
        self.assertTrue(t.update(1, tilt=190)[0])
        self.assertEqual(t.list()[1]["tilt"], -170.0)

    def test_move_clamped_to_roi(self):
        t = VirtualTable()
        t.add()
        t.move(0, 1.5, -1, roi=[0.1, 0.2, 0.5, 0.5])
        o = t.list()[0]
        self.assertEqual((o["x"], o["y"]), (0.6, 0.2))

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "objs.json")
            t = VirtualTable(path)
            t.add(); t.add(); t.update(1, tilt=30)
            t.save_if_dirty()
            t2 = VirtualTable(path)
            self.assertEqual([(o["id"], o["tilt"]) for o in t2.list()], [(0, 0.0), (1, 30.0)])

    def test_source_emits_frames(self):
        t = VirtualTable()
        t.add(); t.add()
        got = []
        s = VirtualSource(t, fps=100)
        s.on_frame = lambda objs, fseq: got.append((len(objs), fseq))
        s.start()
        time.sleep(0.3)
        s.stop()
        self.assertGreater(len(got), 15)
        self.assertTrue(all(n == 2 for n, _ in got))
        self.assertLess(s.age(), 1.0)


class TestTestModePipeline(unittest.TestCase):
    """--test end to end: virtual objects -> exhibition OSC path -> a real UDP
    socket, dict format, no reacTIVision started, no TUIO port bound."""

    def test_osc_dict_over_udp(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(2.0)
        port = rx.getsockname()[1]
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(X, "CFG_PATH", os.path.join(d, "cfg.json")):
            p = X.ExhibitPipeline(test_mode=True, test_objects_path=os.path.join(d, "o.json"))
            p.rtv = mock.Mock()
            p.rtv.format = None
            p.update({"osc_enabled": True, "rtv_fps": 60,
                      "osc_targets": [{"name": "sc", "host": "127.0.0.1", "port": port,
                                       "format": "dict", "enabled": True}]})
            p.object_action({"action": "add"})
            p.object_action({"action": "add"})
            p.object_action({"action": "update", "index": 1, "tilt": 30})
            p.start()
            try:
                from pythonosc.osc_message import OscMessage
                msgs = []
                t_end = time.time() + 2
                while len(msgs) < 6 and time.time() < t_end:
                    msgs.append(OscMessage(rx.recv(4096)))
            finally:
                p.shutdown()
                rx.close()
            p.rtv.start.assert_not_called()
            p.rtv.restart.assert_not_called()
        self.assertGreaterEqual(len(msgs), 6)
        m = msgs[0]
        self.assertEqual(m.address, "/scramble/obj")
        self.assertEqual(m.params[0::2], X.OSC_OBJ_FIELDS)     # "id","bits","x","y","tilt","tension"
        ids = {tuple(mm.params[1:4:2]) for mm in msgs}
        self.assertIn((0, "00000000"), ids)
        self.assertIn((1, "00000001"), ids)
        tilts = {mm.params[1]: mm.params[9] for mm in msgs}
        self.assertEqual(tilts[1], 30.0)


if __name__ == "__main__":
    unittest.main()
