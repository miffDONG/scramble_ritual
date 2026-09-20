import unittest

from conductor.link import MockLink
from conductor.protocol import ERR_OVER_LIMIT, N_CELLS


class TestMockLink(unittest.TestCase):
    def test_frame_ack_and_latch(self):
        link = MockLink()
        cells = [i % 5 == 0 for i in range(N_CELLS)]
        a = link.send_frame(cells)
        self.assertTrue(a.ok)
        self.assertEqual(a.on_count, sum(cells))
        self.assertEqual(link.latched, cells)

    def test_over_limit_nacked_keeps_previous(self):
        link = MockLink(max_on=72)
        ok_cells = [i < 10 for i in range(N_CELLS)]
        link.send_frame(ok_cells)
        a = link.send_frame([True] * N_CELLS)       # 144 ON -> 거부
        self.assertFalse(a.ok)
        self.assertEqual(a.err, ERR_OVER_LIMIT)
        self.assertEqual(link.latched, ok_cells)    # 이전 상태 유지

    def test_all_off(self):
        link = MockLink()
        link.send_frame([True] * 20 + [False] * (N_CELLS - 20))
        a = link.all_off()
        self.assertTrue(a.ok)
        self.assertEqual(sum(link.latched), 0)

    def test_set_limit(self):
        link = MockLink(max_on=72)
        self.assertTrue(link.set_limit(100).ok)
        cells = [i < 90 for i in range(N_CELLS)]
        self.assertTrue(link.send_frame(cells).ok)

    def test_seq_increments(self):
        link = MockLink()
        a1 = link.ping()
        a2 = link.ping()
        self.assertEqual((a2.seq - a1.seq) & 0xFF, 1)


if __name__ == "__main__":
    unittest.main()
