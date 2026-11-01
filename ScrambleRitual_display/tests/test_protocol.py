import unittest

from conductor import protocol as P


class TestProtocol(unittest.TestCase):
    def test_pack_unpack_roundtrip(self):
        cells = [(i * 7) % 3 == 0 for i in range(P.N_CELLS)]
        self.assertEqual(P.unpack_bits(P.pack_bits(cells)), cells)

    def test_encode_decode_roundtrip(self):
        payload = bytes(range(P.N_BYTES))
        wire = P.encode(P.T_FRAME, payload, seq=42)
        parser = P.Parser(P.SYNC_H2D)
        pkts = parser.feed(wire)
        self.assertEqual(pkts, [(P.T_FRAME, 42, payload)])

    def test_incremental_feed(self):
        wire = P.encode(P.T_PING, seq=7)
        parser = P.Parser(P.SYNC_H2D)
        pkts = []
        for b in wire:                      # 1바이트씩 수신해도 복원
            pkts += parser.feed(bytes([b]))
        self.assertEqual(pkts, [(P.T_PING, 7, b"")])

    def test_corrupted_crc_rejected_then_resync(self):
        good = P.encode(P.T_PING, seq=1)
        bad = bytearray(P.encode(P.T_PING, seq=2))
        bad[-1] ^= 0xFF                     # CRC 파괴
        parser = P.Parser(P.SYNC_H2D)
        pkts = parser.feed(bytes(bad) + good)
        self.assertEqual(pkts, [(P.T_PING, 1, b"")])
        self.assertGreaterEqual(parser.crc_errors, 1)

    def test_garbage_prefix_resync(self):
        wire = b"\x00\xff\x13" + P.encode(P.T_ALL_OFF, seq=9)
        pkts = P.Parser(P.SYNC_H2D).feed(wire)
        self.assertEqual(pkts, [(P.T_ALL_OFF, 9, b"")])


if __name__ == "__main__":
    unittest.main()
