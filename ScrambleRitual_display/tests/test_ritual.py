import unittest
import socket
from unittest import mock

from tracker.ritual import RitualState
from tracker.reactivision import ExhibitObject
from webui import exhibit_server as X
from tests.test_exhibit_server import FakeClient, bare_pipeline


def obj(key, x, y=0):
    return ExhibitObject(track_id=key, code_id=key, nx=0, ny=0, cx=x, cy=y, angle=0)


class TestRitual(unittest.TestCase):
    def test_no_degree_cap(self):
        s = RitualState()
        s.update([obj(i, 0) for i in range(9)])
        s.edges = {(0, i) for i in range(1, 9)}
        edges, _ = s.graph(100, .1, .5)
        self.assertEqual(len(edges), 8)
        self.assertEqual(sum(0 in (i, j) for i, j, _ in edges), 8)

    def test_order_grace_and_reappearance(self):
        s = RitualState()
        s.update([obj(5, 20), obj(3, 10)])
        s.update([obj(3, 30), obj(5, 0)])
        self.assertEqual(list(s.objects), [5, 3])
        for _ in range(4):
            s.update([obj(5, 0)])
            self.assertEqual(list(s.objects), [5, 3])
        s.update([obj(5, 0)])
        self.assertEqual(list(s.objects), [5])
        s.update([obj(7, 40), obj(3, 30), obj(5, 0)])
        self.assertEqual(list(s.objects), [5, 7, 3])

    def test_roi_and_heartbeat(self):
        s = RitualState()
        s.update([obj(0, 10)], (0, 0, 100, 100))
        for _ in range(4):
            s.update([obj(0, 110)], (0, 0, 100, 100))
        self.assertIn(0, s.objects)
        s.update([obj(0, 110)], (0, 0, 100, 100))
        self.assertFalse(s.objects)
        for _ in range(95):
            s.update([])
        self.assertEqual(s.heartbeat, 100)
        s.update([])
        self.assertEqual(s.heartbeat, 0)

    def test_contact_edges_and_pairs_survive_jitter_then_release(self):
        s = RitualState()
        s.update([obj(0, 0), obj(1, 8), obj(2, 17)])
        s.graph(100, .2, .5)
        original = set(s.edges)
        self.assertEqual(s.pairs[1], 0)
        s.update([obj(0, 0), obj(1, 12), obj(2, 17)])
        s.graph(100, .2, .5)
        self.assertEqual(s.edges, original)
        self.assertEqual(s.pairs[1], 0)
        # 0-1 leaves the contact plateau; shorter 0-2 now replaces it.
        s.update([obj(0, 0), obj(1, 30), obj(2, 17)])
        s.graph(100, .2, .5)
        self.assertNotIn((0, 1), s.edges)
        self.assertIn((0, 2), s.edges)
        for _ in range(5):
            s.update([obj(0, 0), obj(2, 17)])
            s.graph(100, .2, .5)
        self.assertTrue(all(1 not in edge for edge in s.edges))
        self.assertNotIn(1, s.pairs.values())

    def test_continuous_margin_and_disconnected_pair(self):
        s = RitualState()
        s.update([obj(0, 0), obj(1, 13)])
        _, ts = s.graph(100, .1, .5, .02)
        self.assertAlmostEqual(ts[0], .9737)
        s.update([obj(0, 0), obj(1, 12)])
        self.assertEqual(s.graph(100, .1, .5, .02)[1], [1, 1])
        s.update([obj(0, 0), obj(1, 60)])
        self.assertEqual(s.graph(100, .1, .5)[1], [0, 0])
        self.assertEqual(s.pairs, {0: -1, 1: -1})

    def test_screenshot_spacing_has_contact_plateau_and_continuous_ramp(self):
        s = RitualState()
        # Screenshot test objects: (0.1,0.1), (0.1595,0.1594), 1280x800.
        s.update([obj(0, 128, 80), obj(1, .1595 * 1280, .1594 * 800)])
        margin = X.EXHIBIT_DEFAULTS["tension_margin"]
        self.assertEqual(s.graph(1280, .0622, .5, margin)[1], [1, 1])
        # A small additional centre jitter remains inside the plateau.
        s.update([obj(0, 128, 80), obj(1, .1595 * 1280 + 3, .1594 * 800)])
        self.assertEqual(s.graph(1280, .0622, .5, margin)[1], [1, 1])
        contact = .0622 + margin
        for desired in (.98, .99, .999):
            distance = .5 - desired * (.5 - contact)
            s.update([obj(0, 0), obj(1, distance * 1280)])
            self.assertAlmostEqual(s.graph(1280, .0622, .5, margin)[1][0], desired, places=4)

    def test_td_bundle_preserves_sound_values_and_types(self):
        FakeClient.sent = {}
        rt = {**X.EXHIBIT_DEFAULTS, "osc_enabled": True}
        p = bare_pipeline(rt)
        with mock.patch("pythonosc.udp_client.SimpleUDPClient", FakeClient):
            p._send_osc(rt, [obj(5, 10), obj(3, 20)], (1000, 500))
        sound = FakeClient.sent[("127.0.0.1", 57120)]
        td = FakeClient.sent[("127.0.0.1", 7000)]
        self.assertEqual(len(td), 1)
        bundle = td[0][1]
        messages = list(bundle)
        self.assertEqual(len(messages), 20)      # heartbeat + count + 2 objects x 9 fields
        for index, (_, args) in enumerate(sound):
            values = dict(zip(args[::2], args[1::2]))
            hb = next(m for m in messages if m.address == "/scramble/heartbeat")
            self.assertEqual(hb.params, [values.pop("heartbeat")])
            for field, value in values.items():
                suffix = "pairID" if field == "pairId" else field
                message = next(m for m in messages if m.address == f"/scramble/{suffix}{index}")
                if isinstance(value, float):
                    self.assertAlmostEqual(message.params[0], value, places=5)
                else:
                    self.assertEqual(message.params, [value])
        self.assertEqual(next(m for m in messages if m.address == "/scramble/bits0").params, ["00000101"])

    def test_real_udp_one_frame_one_bundle(self):
        from pythonosc.osc_packet import OscPacket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(("127.0.0.1", 0))
            receiver.settimeout(1)
            rt = {**X.EXHIBIT_DEFAULTS, "osc_enabled": True, "osc_targets": [
                {"name": "TD", "host": "127.0.0.1", "port": receiver.getsockname()[1],
                 "format": "td", "enabled": True}]}
            p = bare_pipeline(rt)
            try:
                p._send_osc(rt, [obj(5, 10), obj(3, 20)], (1000, 500))
                payload, _ = receiver.recvfrom(65535)
                self.assertTrue(payload.startswith(b"#bundle"))
                packet = OscPacket(payload)
                self.assertEqual(len(packet.messages), 20)
                self.assertEqual([m.message.params[0] for m in packet.messages
                                  if "heartbeat" in m.message.address], [0])
                receiver.settimeout(.05)
                with self.assertRaises(socket.timeout):
                    receiver.recvfrom(65535)
            finally:
                for client in p._osc_clients.values():
                    client._sock.close()


if __name__ == "__main__":
    unittest.main()
