import json
import os
import unittest
import unittest.mock
from types import SimpleNamespace

from tracker.morse import cfg_with_defaults, generate_codebook
from webui import server as S


BOOK = generate_codebook(20, 8, 3)


class TestObjectsToCards(unittest.TestCase):
    def test_ground_truth_fields(self):
        objs = [
            {"code_id": 5, "x": 0.30, "y": 0.50, "tilt": 12.0, "flip": False,
             "shape": "square", "pitch": None},
            {"code_id": 2, "x": 0.70, "y": 0.50, "tilt": -8.0, "flip": True,
             "shape": "star", "pitch": 4.0},
        ]
        cards = S.objects_to_cards(objs, 900, 520, BOOK, 8)
        self.assertEqual(len(cards), 2)
        a, b = cards
        # bits come from the codebook by ID
        self.assertEqual(a.bits, BOOK[5])
        self.assertEqual(b.bits, BOOK[2])
        # centre = triangle centre (normalized * frame)
        self.assertAlmostEqual(a.cx, 0.30 * 900, places=3)
        self.assertAlmostEqual(a.cy, 0.50 * 520, places=3)
        self.assertEqual(a.angle, 12.0)
        self.assertFalse(a.flip)
        self.assertTrue(b.flip)
        self.assertEqual(b.pitch, 4.0)
        # exactly `slots` symbols, each carrying its bit
        self.assertEqual(len(a.slots), 8)
        self.assertEqual("".join(s.bit for s in a.slots), a.bits)

    def test_overlay_geometry_present(self):
        # fabricated start/end glyphs must exist for draw_overlay
        cards = S.objects_to_cards([{"code_id": 1, "x": 0.5, "y": 0.5}],
                                   900, 520, BOOK, 8)
        c = cards[0]
        self.assertIsNotNone(c.start)
        self.assertIsNotNone(c.end)
        self.assertEqual(c.start.shape, "triangle")  # default anchor shape

    def test_flip_mirrors_chain_above_object_without_reversing_bits(self):
        base = {"code_id": 5, "x": 0.5, "y": 0.5, "tilt": 0.0}
        normal = S.objects_to_cards([{**base, "flip": False}], 900, 520,
                                    BOOK, 8)[0]
        flipped = S.objects_to_cards([{**base, "flip": True}], 900, 520,
                                     BOOK, 8)[0]

        # Front-face chain is below the object centre; a top/bottom face flip
        # mirrors it above while preserving the anchor-to-symbol bit order.
        self.assertGreater(normal.start.cy, normal.cy)
        self.assertLess(flipped.start.cy, flipped.cy)
        self.assertEqual("".join(s.bit for s in flipped.slots), BOOK[5])
        self.assertLess(flipped.start.cx, flipped.slots[-1].cx)

    def test_180_rotation_moves_chain_above_but_reverses_screen_order(self):
        card = S.objects_to_cards([
            {"code_id": 5, "x": 0.5, "y": 0.5, "tilt": 180.0,
             "flip": False}], 900, 520, BOOK, 8)[0]

        self.assertLess(card.start.cy, card.cy)
        self.assertGreater(card.start.cx, card.slots[-1].cx)


class TestTension(unittest.TestCase):
    def test_monotonic_curve(self):
        d_near, d_far = 0.17, 0.5   # d_near = edge-touch, d_far = half long side
        # far -> 0, edge-touch and any overlap -> 1 (no drop on overlap)
        self.assertEqual(S.pair_tension(d_far, d_near, d_far), 0.0)
        self.assertEqual(S.pair_tension(0.6, d_near, d_far), 0.0)     # farther
        self.assertEqual(S.pair_tension(d_near, d_near, d_far), 1.0)  # touch
        self.assertEqual(S.pair_tension(0.05, d_near, d_far), 1.0)    # overlap
        self.assertEqual(S.pair_tension(0.0, d_near, d_far), 1.0)     # deep
        # linear rise between: closer -> higher, and the midpoint is ~0.5
        self.assertLess(S.pair_tension(0.40, d_near, d_far),
                        S.pair_tension(0.25, d_near, d_far))
        mid = (d_near + d_far) / 2
        self.assertAlmostEqual(S.pair_tension(mid, d_near, d_far), 0.5, places=2)

    def test_fold_rules(self):
        vals = [0.8, 0.2, 0.0]
        self.assertEqual(S._fold(vals, "max"), 0.8)
        self.assertEqual(S._fold(vals, "min"), 0.0)
        self.assertAlmostEqual(S._fold(vals, "avg"), 1.0 / 3.0)
        self.assertEqual(S._fold([], "max"), 0.0)     # empty -> 0

    def test_object_tension_connect_and_fold(self):
        d_near, d_far = 0.17, 0.5
        # object i has one near neighbour (touch->1) and two far ones (->0)
        dists = [d_near, 0.6, 0.7]
        # max is unaffected by the far neighbours
        self.assertEqual(
            S.object_tension(0, dists, d_near, d_far, "all", "max"), 1.0)
        # avg over ALL dilutes with the far zeros (1+0+0)/3
        self.assertAlmostEqual(
            S.object_tension(0, dists, d_near, d_far, "all", "avg"),
            round(1.0 / 3.0, 4))
        # avg over NEAR (within d_far) drops the far zeros -> stays 1
        self.assertEqual(
            S.object_tension(0, dists, d_near, d_far, "near", "avg"), 1.0)
        # no neighbours -> 0
        self.assertEqual(
            S.object_tension(0, [], d_near, d_far, "all", "max"), 0.0)

    def test_mst_has_n_minus_one_edges_and_node_local_tension(self):
        # The short chain 0--1--2 is the MST; the long 0--2 edge is excluded.
        nodes = [SimpleNamespace(cx=x, cy=0.0) for x in (0.0, 20.0, 60.0)]
        edges, tensions = S.tension_graph(
            nodes, long_px=100.0, d_near=0.1, d_far=0.5,
            connect="mst", fold="avg")
        self.assertEqual([(i, j) for i, j, _ in edges], [(0, 1), (1, 2)])
        self.assertEqual(len(edges), len(nodes) - 1)
        # edge tensions: 0--1 = .75, 1--2 = .25.  Leaves use one edge;
        # the middle object's OSC tension averages only its two incident edges.
        self.assertEqual(tensions, [0.75, 0.5, 0.25])

    def test_mst_default_and_disconnected_singleton(self):
        nodes = [SimpleNamespace(cx=x, cy=0.0) for x in (0.0, 10.0)]
        edges, tensions = S.tension_graph(
            nodes, long_px=100.0, d_near=0.1, d_far=0.5)
        self.assertEqual(len(edges), 1)
        self.assertEqual(tensions, [1.0, 1.0])
        self.assertEqual(S.tension_graph(
            nodes[:1], 100.0, 0.1, 0.5), ([], [0.0]))

    def test_object_freq_id_band_and_override(self):
        cards = S.objects_to_cards([{"code_id": 7, "x": 0.5, "y": 0.5}],
                                   900, 520, BOOK, 8)
        # ID-derived band, no override
        self.assertEqual(S.Pipeline._object_freq(cards[0]), ((7 * 5) % 12) - 6)
        cards[0].pitch = 3.0
        self.assertEqual(S.Pipeline._object_freq(cards[0]), 3.0)


class TestTable(unittest.TestCase):
    def test_grid_holds_a_full_codebook(self):
        # the table must have a spot for every ID in the 20-code codebook
        self.assertGreaterEqual(len(S.table_slots()), 20)

    def test_slots_keep_every_plate_on_the_table(self):
        # auto-placed plates (either body, flipped or not) stay inside the
        # canvas — a slot at the bare cell centre used to hang off the edge
        w, h = S.SIM_CANVAS
        hw, hh = S.plate_reach()
        for x, y in S.table_slots():
            self.assertGreaterEqual(x * w - hw, -0.5)
            self.assertLessEqual(x * w + hw, w + 0.5)
            self.assertGreaterEqual(y * h - hh, -0.5)
            self.assertLessEqual(y * h + hh, h + 0.5)

    def test_free_slot_skips_taken_ones(self):
        slots = S.table_slots()
        placed = [{"x": x, "y": y} for x, y in slots[:3]]
        self.assertEqual(S.free_table_slot(placed), slots[3])
        self.assertEqual(S.free_table_slot([]), slots[0])

    def test_hexagon_body_is_a_pointy_top_hexagon(self):
        outline, holes = S._body_outline("hexagon")
        self.assertEqual(len(outline), 6)
        self.assertEqual(len(holes), 3)
        ys = [y for _, y in outline]
        xs = [x for x, _ in outline]
        # pointy top/bottom, flat left/right sides -> taller than it is wide
        self.assertGreater(max(ys) - min(ys), max(xs) - min(xs))
        # the chain (anchor + 8 symbols) has to fit between the flat sides
        chain_half = (int(S.SIM_STEP) * 9) / 2.0
        self.assertGreater(max(xs), chain_half)

    def test_unknown_body_falls_back_to_the_triangle(self):
        self.assertEqual(S._body_outline("nope"), S._body_outline("triangle"))


class TestOscTargetMemory(unittest.TestCase):
    """The send address is remembered on its own, without [config.json 저장]."""

    def setUp(self):
        import tempfile, threading
        self.p = S.Pipeline.__new__(S.Pipeline)
        self.p.lock = threading.Lock()
        self.p.runtime = {"osc_host": "127.0.0.1", "osc_port": 9000,
                          "osc_prefix": "/scramble", "osc_recents": []}
        self.p.cfg = {"threshold_clamp": [40, 120]}
        self.p._sync_stabilizer = lambda: None
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "config.json")
        patch = unittest.mock.patch.object(S, "CFG_PATH", self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def saved(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["webui"]

    def test_changing_the_target_writes_it_to_disk(self):
        self.p.update_cfg({"osc_host": "192.168.0.7", "osc_port": 7400,
                           "osc_prefix": "/ritual"})
        on_disk = self.saved()
        self.assertEqual(on_disk["osc_host"], "192.168.0.7")
        self.assertEqual(on_disk["osc_port"], 7400)
        self.assertEqual(on_disk["osc_prefix"], "/ritual")

    def test_recents_are_newest_first_deduped_and_capped(self):
        for port in range(1, S.OSC_RECENTS_MAX + 3):
            self.p.update_cfg({"osc_port": port})
        self.p.update_cfg({"osc_port": 3})          # revisit an old one
        recents = self.p.runtime["osc_recents"]
        self.assertEqual(len(recents), S.OSC_RECENTS_MAX)   # capped
        self.assertEqual(recents[0]["osc_port"], 3)         # newest first
        ports = [t["osc_port"] for t in recents]
        self.assertEqual(len(ports), len(set(ports)))       # no duplicates
        self.assertEqual([t["osc_port"] for t in self.saved()["osc_recents"]],
                         ports)

    def test_tuning_options_are_not_written_out(self):
        # only the address is auto-saved; recognition options still wait for
        # the explicit save button
        self.p.update_cfg({"osc_host": "10.0.0.2", "proc_max_dim": 1920})
        self.assertNotIn("proc_max_dim", self.saved())

    def test_unrelated_options_alone_write_nothing(self):
        self.p.update_cfg({"proc_max_dim": 1920})
        self.assertFalse(os.path.exists(self.path))


class TestRoiIsSessionOnly(unittest.TestCase):
    """The table region always starts empty; it is set by dragging, per
    session, and never rides along in a saved profile."""

    def setUp(self):
        import tempfile, threading
        self.p = S.Pipeline.__new__(S.Pipeline)
        self.p.lock = threading.Lock()
        self.p.cfg = {"threshold_clamp": [40, 120], "slots": 8}
        self.p.runtime = dict(S.RUNTIME_DEFAULTS)
        self.p.active_name = ""
        self.p._sync_stabilizer = lambda: None
        self.p.stab = S.CardStabilizer()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "config.json")
        for target in ("CFG_PATH", "CONFIGS_DIR"):
            val = self.path if target == "CFG_PATH" else self.dir.name
            patch = unittest.mock.patch.object(S, target, val)
            patch.start()
            self.addCleanup(patch.stop)

    def test_default_is_no_region(self):
        self.assertIsNone(S.RUNTIME_DEFAULTS["roi"])

    def test_saving_does_not_write_the_region(self):
        self.p.runtime["roi"] = [0.1, 0.1, 0.5, 0.5]
        self.p.save_cfg("field")
        with open(self.path, encoding="utf-8") as f:
            self.assertNotIn("roi", json.load(f)["webui"])

    def test_loading_a_profile_never_imposes_a_region(self):
        # a profile written before this change still carries one
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"webui": {"roi": [0.0, 0.0, 0.7, 0.9], "osc_port": 7000}},
                      f)
        self.p.runtime["roi"] = [0.2, 0.2, 0.3, 0.3]
        self.p.load_cfg()
        self.assertIsNone(self.p.runtime["roi"])
        self.assertEqual(self.p.runtime["osc_port"], 7000)  # the rest loads

    def test_it_still_applies_while_the_session_lasts(self):
        self.p.set_roi([0.1, 0.2, 0.4, 0.5])
        self.assertEqual(self.p.runtime["roi"], [0.1, 0.2, 0.4, 0.5])


class TestObjectCrud(unittest.TestCase):
    def setUp(self):
        self.p = S.Pipeline.__new__(S.Pipeline)  # bare, no thread/config load
        import threading
        self.p.lock = threading.Lock()
        self.p.runtime = {"sim_objects": []}
        # the plate cap comes from the codebook size, so the bare fixture
        # still needs a config
        self.p.cfg = cfg_with_defaults(None)

    def test_add_without_coords_fills_the_grid(self):
        for _ in range(20):
            self.p.object_op("add", {})
        spots = [(o["x"], o["y"]) for o in self.p._objects()]
        self.assertEqual(len(set(spots)), 20)      # no pile-up on one spot
        self.assertEqual(spots, S.table_slots()[:20])

    def test_body_defaults_to_the_hexagon_plate_and_is_updatable(self):
        self.p.object_op("add", {})
        self.assertEqual(self.p._objects()[0]["body"], S.SIM_DEFAULT_BODY)
        self.assertEqual(S.SIM_DEFAULT_BODY, "hexagon")
        self.p.object_op("update", {"index": 0, "body": "triangle"})
        self.assertEqual(self.p._objects()[0]["body"], "triangle")

    def test_objects_saved_before_the_body_field_still_render_as_triangles(self):
        # sim_objects live in config profiles; an entry with no body key
        # predates the hexagon and must keep the shape it was drawn with
        frame = S.render_objects([{"code_id": 1, "x": 0.5, "y": 0.5}], BOOK, 8)
        tri = S.render_objects([{"code_id": 1, "x": 0.5, "y": 0.5,
                                 "body": "triangle"}], BOOK, 8)
        self.assertTrue((frame == tri).all())

    def test_add_move_update_remove_clear(self):
        objs = self.p.object_op("add", {"x": 0.2, "y": 0.3, "code_id": 5})
        self.assertEqual(len(objs), 1)
        self.assertEqual(objs[0]["code_id"], 5)
        self.p.object_op("move", {"index": 0, "x": 1.5, "y": -0.2})  # clamp
        o = self.p._objects()[0]
        self.assertEqual((o["x"], o["y"]), (1.0, 0.0))
        self.p.object_op("update", {"index": 0, "flip": True, "tilt": 20,
                                    "shape": "star", "pitch": 4})
        o = self.p._objects()[0]
        self.assertTrue(o["flip"])
        self.assertEqual(o["shape"], "star")
        self.assertEqual(o["pitch"], 4.0)
        self.p.object_op("add", {"x": 0.5, "y": 0.5})
        self.assertEqual(len(self.p._objects()), 2)
        self.p.object_op("remove", {"index": 0})
        self.assertEqual(len(self.p._objects()), 1)
        self.p.object_op("clear", {})
        self.assertEqual(self.p._objects(), [])


if __name__ == "__main__":
    unittest.main()


class TestObjectCap(unittest.TestCase):
    """The table holds one plate per codebook ID and no more: a further plate
    would repeat an ID, and OSC receivers identify objects BY their ID."""

    def setUp(self):
        self.p = S.Pipeline.__new__(S.Pipeline)
        import threading
        self.p.lock = threading.Lock()
        self.p.runtime = {"sim_objects": []}
        self.p.cfg = cfg_with_defaults(None)

    def test_adding_stops_at_the_codebook_size(self):
        for _ in range(30):
            self.p.object_op("add", {})
        self.assertEqual(len(self.p._objects()), 20)
        self.assertEqual(self.p.object_limit(), 20)

    def test_every_plate_gets_its_own_id(self):
        for _ in range(20):
            self.p.object_op("add", {})
        ids = [o["code_id"] for o in self.p._objects()]
        self.assertEqual(sorted(ids), list(range(1, 21)))

    def test_a_freed_id_is_reused_before_the_next_number(self):
        for _ in range(20):
            self.p.object_op("add", {})
        self.p.object_op("remove", {"index": 4})       # frees ID 5
        self.p.object_op("add", {})
        self.assertEqual(self.p._objects()[-1]["code_id"], 5)
        self.assertEqual(len(self.p._objects()), 20)

    def test_removing_makes_room_again(self):
        for _ in range(25):
            self.p.object_op("add", {})
        self.p.object_op("remove", {"index": 0})
        self.assertEqual(len(self.p._objects()), 19)
        self.p.object_op("add", {})
        self.assertEqual(len(self.p._objects()), 20)

    def test_the_cap_follows_the_codebook_and_the_table_grid(self):
        self.p.cfg = cfg_with_defaults({"code_count": 6})
        self.assertEqual(self.p.object_limit(), 6)
        for _ in range(10):
            self.p.object_op("add", {})
        self.assertEqual(len(self.p._objects()), 6)
        # never more plates than the auto-placement grid has cells
        self.assertEqual(S.object_capacity(999), len(S.table_slots()))
