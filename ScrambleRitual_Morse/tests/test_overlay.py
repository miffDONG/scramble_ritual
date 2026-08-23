"""The read has to be visible, not just correct.

Two layers are covered here:

  * the card layer (tracker.overlay) — which blobs became ONE id, drawn as a
    hull + a chain in reading order, and what each slot was read as;
  * the object layer (webui.server.draw_tension_graph) — the measured values
    that leave over OSC (ID / tilt / flip / tension) drawn next to the object
    they belong to, and the links tension is folded from.

Both used to be either missing or reduced to an unlabelled line/dot.
"""

import numpy as np
import pytest

from tracker.morse import detect_cards, generate_codebook, render_scene
from tracker.overlay import (
    CARD_PALETTE, UNKNOWN_CARD_BGR, card_color, draw_card_reads,
    draw_read_summary, overlay_scale,
)
from webui import server as S

BOOK = generate_codebook(20, 8, 3)


def detected():
    frame = render_scene([(BOOK[5], 300, 160, 0.0),
                          (BOOK[2], 300, 360, 14.0)])
    _, cards, glyphs = detect_cards(frame, {"slots": 8}, BOOK)
    assert len(cards) == 2
    return frame, cards, glyphs


def painted(before, after):
    """Pixels the overlay changed."""
    return int(np.count_nonzero(np.any(before != after, axis=2)))


def test_card_layer_draws_the_group_and_the_chain():
    frame, cards, _ = detected()
    vis = frame.copy()
    draw_card_reads(vis, cards)
    assert painted(frame, vis) > 500

    # the chain has to be drawn BETWEEN the slots, not just at their centres:
    # sample the midpoint between two consecutive slots of a card
    card = cards[0]
    a, b = card.slots[0], card.slots[1]
    mx, my = int((a.cx + b.cx) / 2), int((a.cy + b.cy) / 2)
    assert np.any(vis[my, mx] != frame[my, mx]), "no link drawn between slots"


def test_each_id_keeps_its_own_colour_and_unknown_reads_red():
    frame, cards, _ = detected()
    colors = {c.code_id: card_color(c) for c in cards}
    assert len(set(colors.values())) == len(colors)     # two IDs, two colours
    for card in cards:                                   # stable across frames
        assert card_color(card) == CARD_PALETTE[card.code_id % len(CARD_PALETTE)]
    cards[0].code_id = None
    assert card_color(cards[0]) == UNKNOWN_CARD_BGR


def test_card_layer_survives_slots_without_glyphs():
    """Ground-truth sim cards carry fabricated slots; the layer must not
    depend on a real contour existing."""
    cards = S.objects_to_cards([{"code_id": 3, "x": 0.5, "y": 0.5}],
                               900, 520, BOOK, 8)
    for slot in cards[0].slots:
        slot.glyph = None
    vis = np.full((520, 900, 3), 240, np.uint8)
    draw_card_reads(vis, cards)          # must not raise
    assert painted(np.full((520, 900, 3), 240, np.uint8), vis) > 0


def test_empty_input_is_a_no_op():
    vis = np.full((120, 160, 3), 200, np.uint8)
    before = vis.copy()
    draw_card_reads(vis, [])
    assert np.array_equal(vis, before)


def test_summary_names_the_ids_read():
    frame, cards, _ = detected()
    vis = frame.copy()
    draw_read_summary(vis, cards)
    assert painted(frame, vis) > 0


def test_overlay_scale_follows_the_frame_size():
    assert overlay_scale((480, 640, 3)) < overlay_scale((1600, 1200, 3))
    assert overlay_scale((10, 10, 3)) >= 0.75        # clamped, never vanishes


# ---- object readout ------------------------------------------------------

def object_graph(objs):
    """The graph exactly as Pipeline._send_osc stashes it for the overlay."""
    w, h = S.SIM_CANVAS
    cards = S.objects_to_cards(objs, w, h, BOOK, 8)
    long_px = max(w, h)
    d_near, d_far, _ = S.tension_thresholds({"tension_contact": 0.06},
                                            float(S.SIM_TRI_W), long_px)
    edges, node_t = S.tension_graph(cards, long_px, d_near, d_far,
                                    "all", "max", 3, 0.2)
    nodes = [{"cx": float(c.cx), "cy": float(c.cy), "id": c.code_id,
              "bits": c.bits, "tilt": float(c.angle), "flip": bool(c.flip),
              "tension": float(node_t[i]),
              "freq": float(S.Pipeline._object_freq(c))}
             for i, c in enumerate(cards)]
    return cards, (nodes, edges, node_t)


def test_object_readout_draws_links_and_per_object_values():
    cards, graph = object_graph([
        {"code_id": 1, "x": 0.25, "y": 0.5, "tilt": 0.0},
        {"code_id": 2, "x": 0.45, "y": 0.5, "tilt": 12.0, "flip": True},
        {"code_id": 5, "x": 0.75, "y": 0.5, "tilt": -8.0},
    ])
    nodes, edges, node_t = graph
    assert len(nodes) == 3 and len(edges) == 3        # every pair linked

    w, h = S.SIM_CANVAS
    frame = np.full((h, w, 3), 245, np.uint8)
    vis = frame.copy()
    S.draw_tension_graph(vis, graph)
    assert painted(frame, vis) > 1000

    # a link is drawn along the segment between two objects, not only at them
    a, b = nodes[0], nodes[1]
    mx, my = int((a["cx"] + b["cx"]) / 2), int((a["cy"] + b["cy"]) / 2)
    band = vis[my - 3:my + 4, mx - 3:mx + 4]
    assert np.any(band != 245), "no link drawn between two objects"


def test_object_readout_carries_the_values_that_are_sent_over_osc():
    """The overlay numbers must be the OSC payload, not a parallel estimate."""
    objs = [{"code_id": 1, "x": 0.25, "y": 0.5, "tilt": 7.0, "flip": True},
            {"code_id": 2, "x": 0.55, "y": 0.5, "tilt": -3.0}]
    cards, (nodes, edges, node_t) = object_graph(objs)
    for node, card, t in zip(nodes, cards, node_t):
        assert node["id"] == card.code_id
        assert node["bits"] == card.bits
        assert node["tilt"] == pytest.approx(card.angle)
        assert node["flip"] == card.flip
        assert node["tension"] == pytest.approx(t)
        assert node["freq"] == pytest.approx(S.Pipeline._object_freq(card))
    # tension is symmetric here (two objects), and both are within d_far
    assert node_t[0] == pytest.approx(node_t[1])
    assert node_t[0] > 0.0


def test_object_readout_handles_a_single_object_and_no_objects():
    _, graph = object_graph([{"code_id": 4, "x": 0.5, "y": 0.5}])
    nodes, edges, node_t = graph
    assert len(nodes) == 1 and edges == [] and node_t == [0.0]
    vis = np.full((520, 900, 3), 245, np.uint8)
    S.draw_tension_graph(vis, graph)               # must not raise
    assert painted(np.full((520, 900, 3), 245, np.uint8), vis) > 0

    empty = np.full((520, 900, 3), 245, np.uint8)
    S.draw_tension_graph(empty, ([], [], []))
    assert painted(np.full((520, 900, 3), 245, np.uint8), empty) == 0


# ---- readout density -----------------------------------------------------

def test_readout_keeps_the_four_measured_values_in_both_forms():
    """Compact only drops the bit string and the derived freq — ID, tilt,
    flip and tension are what the sound engine acts on and must stay."""
    node = {"id": 7, "bits": "00011011", "tilt": 12.4, "flip": True,
            "tension": 0.42, "freq": -1.0}
    full = S.readout_lines(node, compact=False)
    compact = S.readout_lines(node, compact=True)
    assert len(full) == 3 and len(compact) == 1
    for text in (" ".join(full), compact[0]):
        assert "ID07" in text
        assert "12.4" in text or "+12" in text        # tilt
        assert "ON" in text or "F+" in text           # flip
        assert "0.42" in text                         # tension
    assert "00011011" in " ".join(full)               # bits: full form only


def test_readout_never_prints_negative_zero_tilt():
    lines = S.readout_lines({"id": 1, "tilt": -0.02, "tension": 0.0}, True)
    assert "-0d" not in lines[0] and "+0d" in lines[0]


def test_blocks_placed_at_the_same_anchor_do_not_overlap():
    """A full table puts plates close together; two readouts pinned to the
    same spot must not land on each other."""
    vis = np.full((520, 900, 3), 30, np.uint8)
    taken = []
    a = S._place_block(vis, ["ID01 +0d F- T0.90"], (400, 260), (0, 200, 255),
                       1.0, taken)
    b = S._place_block(vis, ["ID02 +0d F- T0.90"], (400, 260), (0, 200, 255),
                       1.0, taken)
    assert S._box_overlap(a, b) == 0
    assert len(taken) == 2


def test_box_overlap_measures_area():
    assert S._box_overlap((0, 0, 10, 10), (5, 5, 15, 15)) == 25
    assert S._box_overlap((0, 0, 10, 10), (10, 0, 20, 10)) == 0


def test_a_full_table_renders_every_object_without_error():
    objs = [{"code_id": i + 1, "x": 0.05 + 0.045 * i, "y": 0.5}
            for i in range(20)]
    _, graph = object_graph(objs)
    nodes, edges, node_t = graph
    assert len(nodes) == 20
    w, h = S.SIM_CANVAS
    vis = np.full((h, w, 3), 245, np.uint8)
    S.draw_tension_graph(vis, graph)
    assert painted(np.full((h, w, 3), 245, np.uint8), vis) > 5000
