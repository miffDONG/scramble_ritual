import random
import unittest

import cv2
import numpy as np

from tracker.morse import (
    decode_bits,
    detect_cards,
    generate_codebook,
    hamming,
    render_scene,
)


class TestMorseCodebook(unittest.TestCase):
    def test_codebook_has_spacing(self):
        book = generate_codebook(count=20, bits=8, min_distance=3)
        self.assertEqual(len(book), 20)
        vals = list(book.values())
        self.assertEqual(len(set(vals)), 20)
        for i, a in enumerate(vals):
            for b in vals[i + 1:]:
                self.assertGreaterEqual(hamming(a, b), 3)

    def test_reference_cards_are_ids_1_and_2(self):
        # the two hand-drawn sample photos must stay decodable as ID 1 / 2
        book = generate_codebook(count=20, bits=8, min_distance=3)
        self.assertEqual(book[1], "00110110")  # IMG_0343
        self.assertEqual(book[2], "01001101")  # IMG_0344

    def test_one_bit_correction(self):
        book = generate_codebook(count=20, bits=8, min_distance=3)
        code = book[7]
        flipped = ("1" if code[0] == "0" else "0") + code[1:]
        cid, raw_id, status = decode_bits(flipped, book, correction_distance=1)
        self.assertEqual(cid, 7)
        self.assertEqual(status, "corrected")
        self.assertIsInstance(raw_id, int)


class TestMorseDetector(unittest.TestCase):
    def test_detect_single_rotated_card(self):
        book = generate_codebook(count=20, bits=8, min_distance=3)
        frame = render_scene([(book[5], 450, 260, 37.0)])
        _, cards, glyphs = detect_cards(frame, {"slots": 8}, book)
        self.assertGreaterEqual(len(glyphs), 9)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].code_id, 5)
        self.assertEqual(cards[0].status, "exact")

    def test_detect_reversed_card(self):
        # reading direction must come from the square, not screen order
        book = generate_codebook(count=20, bits=8, min_distance=3)
        frame = render_scene([(book[5], 450, 260, 180.0)])
        _, cards, _ = detect_cards(frame, {"slots": 8}, book)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].code_id, 5)

    def test_detect_multiple_cards_without_mixing(self):
        book = generate_codebook(count=20, bits=8, min_distance=3)
        frame = render_scene([
            (book[1], 280, 170, 0.0),
            (book[9], 610, 190, -28.0),
            (book[14], 450, 370, 18.0),
        ])
        _, cards, _ = detect_cards(frame, {"slots": 8}, book)
        self.assertEqual(sorted(c.code_id for c in cards), [1, 9, 14])

    def test_detect_hand_drawn_jitter(self):
        book = generate_codebook(count=20, bits=8, min_distance=3)
        rng = random.Random(7)
        frame = render_scene([(book[1], 450, 260, 12.0)], jitter=0.12, rng=rng)
        _, cards, _ = detect_cards(frame, {"slots": 8}, book)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].code_id, 1)

    def test_noise_specks_and_streaks_rejected(self):
        book = generate_codebook(count=20, bits=8, min_distance=3)
        frame = render_scene([(book[2], 300, 200, 5.0)])
        # dust specks
        for (x, y) in [(700, 80), (780, 420), (120, 450)]:
            cv2.circle(frame, (x, y), 4, 60, cv2.FILLED)
        # elongated shadow-like streak (mid-gray, off the card axis)
        cv2.line(frame, (600, 320), (840, 300), 150, 9)
        # dark streak far from the card
        cv2.line(frame, (640, 460), (860, 470), 40, 8)
        _, cards, _ = detect_cards(frame, {"slots": 8}, book)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].code_id, 2)

    def test_two_collinear_cards_not_merged(self):
        book = generate_codebook(count=20, bits=8, min_distance=3)
        # same y, same angle: gap gates must keep the chains apart
        frame = render_scene([
            (book[3], 220, 250, 0.0),
            (book[8], 640, 250, 0.0),
        ])
        _, cards, _ = detect_cards(frame, {"slots": 8}, book)
        self.assertEqual(sorted(c.code_id for c in cards), [3, 8])

    def test_small_square_rejected(self):
        # a square no bigger than the dots must not anchor a card
        from tracker.morse import render_card
        frame = np.full((520, 900, 3), 245, np.uint8)
        render_card(frame, "00110110", (450, 260), 0.0, marker=9, dot_r=6)
        _, cards, _ = detect_cards(frame, {"slots": 8}, None)
        self.assertEqual(len(cards), 0)

    def test_triangle_and_star_anchors(self):
        # 26.07.20 plate designs replace some square anchors with A/star
        from tracker.morse import render_card
        book = generate_codebook(count=20, bits=8, min_distance=3)
        # a star anchor is concave (solidity ~0.6), so the shape gate must be
        # lowered when a run uses star markers (the schema help says so)
        cfg = {"slots": 8, "square_max_vertices": 12,
               "square_min_extent": 0.3, "square_area_ratio": 1.4,
               "min_solidity": 0.5, "min_rectangularity": 0.35}
        for shape in ("triangle", "star"):
            frame = np.full((520, 900, 3), 245, np.uint8)
            render_card(frame, book[5], (450, 260), 15.0, marker=29,
                        marker_shape=shape)
            _, cards, _ = detect_cards(frame, cfg, book)
            self.assertEqual(len(cards), 1, f"{shape} anchor not detected")
            self.assertEqual(cards[0].code_id, 5, f"{shape} decode failed")

    def test_anchorless_reads_chain_without_start_marker(self):
        # test-stage goal: read the 8-blob chain even with no start anchor
        from tracker.morse import render_card
        book = generate_codebook(count=20, bits=8, min_distance=3)
        cfg = {"slots": 8, "require_start": False}
        for bits in ("00110110", "01001101", "10100101"):
            for ang in (0.0, 30.0, 90.0):
                frame = np.full((520, 900, 3), 245, np.uint8)
                render_card(frame, bits, (450, 260), ang, draw_start=False)
                _, cards, _ = detect_cards(frame, cfg, book)
                self.assertEqual(len(cards), 1, f"{bits}@{ang} not read")
                # orientation is ambiguous without an anchor: either reading ok
                self.assertIn(cards[0].bits, (bits, bits[::-1]),
                              f"{bits}@{ang} -> {cards[0].bits}")

    def test_require_start_true_ignores_anchorless_chain(self):
        # with the anchor required, a chain that has no start marker yields
        # no card (the default protocol behavior is unchanged)
        from tracker.morse import render_card
        frame = np.full((520, 900, 3), 245, np.uint8)
        render_card(frame, "00110110", (450, 260), 0.0, draw_start=False)
        _, cards, _ = detect_cards(frame, {"slots": 8, "require_start": True}, None)
        self.assertEqual(len(cards), 0)

    def test_shape_classifier_labels_five_shapes(self):
        # circle/line/square/triangle/star must each get their own label
        import math
        from tracker.morse import (cfg_with_defaults, prepare_gray,
                                    preprocess, find_glyphs)
        c = cfg_with_defaults({"shape_classify": True})

        def render(shape):
            f = np.full((200, 200, 3), 245, np.uint8)
            if shape == "square":
                cv2.rectangle(f, (84, 84), (116, 116), 0, -1)
            elif shape == "circle":
                cv2.circle(f, (100, 100), 17, 0, -1)
            elif shape == "line":
                cv2.line(f, (78, 100), (122, 100), 0, 8)
            elif shape == "triangle":
                cv2.fillPoly(f, [np.array([[100, 80], [82, 118], [118, 118]])], 0)
            elif shape == "star":
                pts = []
                for i in range(10):
                    r = 20 if i % 2 == 0 else 9
                    a = math.radians(-90 + i * 36)
                    pts.append([100 + r * math.cos(a), 100 + r * math.sin(a)])
                cv2.fillPoly(f, [np.array(pts, np.int32)], 0)
            return f

        for shape in ("square", "circle", "line", "triangle", "star"):
            frame = render(shape)
            gray = prepare_gray(frame, c)
            glyphs = find_glyphs(preprocess(frame, c, gray=gray), c, gray=gray)
            self.assertTrue(glyphs, f"{shape}: no glyph")
            g = max(glyphs, key=lambda z: z.area)
            self.assertEqual(g.shape, shape, f"{shape} -> {g.shape}")
            self.assertEqual(g.kind,
                             {"circle": "dot", "line": "dash"}.get(shape, "start"))

    def test_shape_classify_anchor_same_size_as_dots(self):
        # with shape_classify, a start marker no bigger than the dots is still
        # found (by shape), which the size-based anchor gate would have missed
        from tracker.morse import render_card
        book = generate_codebook(count=20, bits=8, min_distance=3)
        cfg = {"slots": 8, "shape_classify": True, "require_start": True,
               "min_solidity": 0.5, "min_rectangularity": 0.35}
        frame = np.full((520, 900, 3), 245, np.uint8)
        # marker (start square) drawn the SAME size as the dots
        render_card(frame, book[5], (450, 260), 10.0, marker=13, dot_r=6)
        _, cards, _ = detect_cards(frame, cfg, book)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].code_id, 5)
        self.assertEqual(cards[0].start.shape, "square")

    def test_template_matching_beats_brittle_threshold_tree(self):
        # nearest-template classification is robust even when dash_aspect_min
        # is set so low that the threshold tree would call star/triangle "line"
        import math
        from tracker.morse import (cfg_with_defaults, prepare_gray, preprocess,
                                    find_glyphs, shape_features)

        def render(shape):
            f = np.full((240, 240, 3), 245, np.uint8)
            cx, cy, s = 120, 120, 20
            if shape == "square":
                cv2.rectangle(f, (cx - s, cy - s), (cx + s, cy + s), 0, -1)
            elif shape == "circle":
                cv2.circle(f, (cx, cy), s, 0, -1)
            elif shape == "line":
                cv2.line(f, (cx - s, cy), (cx + s, cy), 0, 8)
            elif shape == "triangle":
                cv2.fillPoly(f, [np.array([[cx, cy - s], [cx - s, cy + s],
                                           [cx + s, cy + s]])], 0)
            elif shape == "star":
                pts = []
                for i in range(10):
                    r = s if i % 2 == 0 else s * 0.45
                    a = math.radians(-90 + i * 36)
                    pts.append([cx + r * math.cos(a), cy + r * math.sin(a)])
                cv2.fillPoly(f, [np.array(pts, np.int32)], 0)
            return f

        shapes = ("square", "triangle", "star", "circle", "line")
        lenient = cfg_with_defaults({"max_glyph_area_frac": 0.3,
                                     "min_solidity": 0.0, "min_rectangularity": 0.0,
                                     "min_blob_contrast": 0})

        def dominant(frame, c):
            gray = prepare_gray(frame, c)
            return max(find_glyphs(preprocess(frame, c, gray=gray), c, gray=gray),
                       key=lambda z: z.area)

        templates = [{"label": s, "v": shape_features(dominant(render(s), lenient))}
                     for s in shapes]
        # dash_aspect_min=1.15 would trip the threshold tree on star/triangle
        c = cfg_with_defaults({"shape_classify": True, "dash_aspect_min": 1.15,
                               "shape_templates": templates, "max_glyph_area_frac": 0.3,
                               "min_solidity": 0.0, "min_rectangularity": 0.0,
                               "min_blob_contrast": 0})
        for s in shapes:
            self.assertEqual(dominant(render(s), c).shape, s, f"{s} mislabelled")

    def test_bright_marks_on_dark_background(self):
        # field setup: engraved (bright) marks lit from below, polarity flipped
        book = generate_codebook(count=20, bits=8, min_distance=3)
        frame = render_scene([(book[4], 450, 260, 20.0)], bg=25, color=235)
        _, cards, _ = detect_cards(
            frame, {"slots": 8, "dark_on_light": False}, book)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].code_id, 4)

    def test_normalize_illumination_recovers_uneven_backlight(self):
        # simulate an uneven light plate: strong horizontal brightness ramp
        book = generate_codebook(count=20, bits=8, min_distance=3)
        frame = render_scene([(book[6], 450, 260, 0.0)]).astype(np.float64)
        ramp = np.linspace(0.35, 1.0, frame.shape[1])[None, :, None]
        frame = np.clip(frame * ramp, 0, 255).astype(np.uint8)
        cfg = {"slots": 8, "normalize_illumination": True,
               "min_blob_contrast": 30}
        _, cards, _ = detect_cards(frame, cfg, book)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].code_id, 6)

    def test_binary_decode_mode_returns_raw_id(self):
        frame = render_scene([("00001011", 450, 260, -15.0)])
        _, cards, _ = detect_cards(
            frame, {"slots": 8, "decode_mode": "binary"}, codebook=None)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].code_id, int("00001011", 2) + 1)
        self.assertEqual(cards[0].status, "binary")


if __name__ == "__main__":
    unittest.main()
