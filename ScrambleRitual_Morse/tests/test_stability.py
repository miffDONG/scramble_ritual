import unittest
from types import SimpleNamespace

from tracker.morse import generate_codebook
from tracker.stability import CardStabilizer


def fake_card(bits, cx=100.0, cy=100.0, angle=0.0):
    return SimpleNamespace(cx=cx, cy=cy, angle=angle, bits=bits)


FRAME = (900, 520)


class TestCardStabilizer(unittest.TestCase):
    def setUp(self):
        self.book = generate_codebook(count=20, bits=8, min_distance=3)

    def test_needs_min_votes_before_reporting(self):
        st = CardStabilizer(codebook=self.book)
        out = st.update([fake_card(self.book[1])], FRAME)
        self.assertEqual(out, [])
        st.update([fake_card(self.book[1])], FRAME)
        out = st.update([fake_card(self.book[1])], FRAME)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].code_id, 1)
        self.assertEqual(out[0].status, "exact")

    def test_single_frame_glitch_is_outvoted(self):
        st = CardStabilizer(codebook=self.book)
        good = self.book[1]
        # flip 3 bits at once: unaided decode of the glitch frame fails
        bad = "".join("1" if b == "0" else "0" for b in good[:3]) + good[3:]
        for _ in range(5):
            st.update([fake_card(good)], FRAME)
        out = st.update([fake_card(bad)], FRAME)
        self.assertEqual(out[0].code_id, 1)
        self.assertEqual(out[0].bits, good)

    def test_track_identity_survives_motion(self):
        st = CardStabilizer(codebook=self.book)
        for i in range(8):
            out = st.update([fake_card(self.book[2], cx=100 + 6 * i,
                                       cy=100 + 3 * i)], FRAME)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].track_id, 1)
        self.assertEqual(out[0].code_id, 2)

    def test_two_cards_get_separate_tracks(self):
        st = CardStabilizer(codebook=self.book)
        for _ in range(4):
            out = st.update([fake_card(self.book[1], cx=100),
                             fake_card(self.book[2], cx=700)], FRAME)
        ids = sorted((s.code_id, s.track_id) for s in out)
        self.assertEqual(len(out), 2)
        self.assertEqual([cid for cid, _ in ids], [1, 2])
        self.assertNotEqual(ids[0][1], ids[1][1])

    def test_track_survives_short_dropout_then_dies(self):
        st = CardStabilizer(cfg={"max_missed": 3}, codebook=self.book)
        for _ in range(4):
            st.update([fake_card(self.book[1])], FRAME)
        out = st.update([], FRAME)
        self.assertEqual(len(out), 1)  # brief dropout: still reported
        for _ in range(3):
            out = st.update([], FRAME)
        self.assertEqual(out, [])  # exceeded max_missed: track dropped

    def test_id_changes_only_after_new_card_wins_vote(self):
        st = CardStabilizer(cfg={"window": 5}, codebook=self.book)
        for _ in range(5):
            st.update([fake_card(self.book[1])], FRAME)
        # card swapped in place: old bits keep winning until the window turns
        seen = []
        for _ in range(5):
            out = st.update([fake_card(self.book[3])], FRAME)
            seen.append(out[0].code_id)
        self.assertEqual(seen[0], 1)
        self.assertEqual(seen[-1], 3)


if __name__ == "__main__":
    unittest.main()
