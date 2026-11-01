import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from tracker.idfix import (
    IdFixer, IdModel, LOCKED, N_IDS, N_RAW, default_trees_path, parse_trees, tree_steps,
)
from tracker.reactivision import INSTALL_DIR, ExhibitObject
from webui import exhibit_server as X
from tests.test_exhibit_server import bare_pipeline

FPS = 60.0


def ob(fid, cx, cy=100.0, sid=None):
    return ExhibitObject(track_id=fid if sid is None else sid, code_id=fid,
                         nx=0, ny=0, angle=0, cx=cx, cy=cy)


class Run:
    """Feeds frames at FPS; returns {code_id: src_id} of each output."""
    def __init__(self, fx):
        self.fx, self.t = fx, 100.0

    def frame(self, objs):
        self.t += 1.0 / FPS
        return self.fx.update(objs, self.t)

    def ids(self, objs):
        return sorted(o.code_id for o in self.frame(objs))


def fixer(**kw):
    kw.setdefault("gate_px", 30.0)
    return IdFixer(IdModel(), **kw)


class TestIdFixer(unittest.TestCase):
    def test_clean_ids_pass_through(self):
        r = Run(fixer())
        for _ in range(20):
            out = r.frame([ob(0, 100), ob(7, 300)])
        self.assertEqual(sorted((o.code_id, o.src_id) for o in out), [(0, 0), (7, 7)])

    def test_jump_above_24_keeps_id(self):
        r = Run(fixer())
        for _ in range(80):
            r.frame([ob(0, 100)])
        for raw in (30, 95, 30, 30, 95):
            out = r.frame([ob(raw, 101, sid=99)])
            self.assertEqual([(o.code_id, o.src_id) for o in out], [(0, raw)])

    def test_jump_inside_range_keeps_id(self):
        r = Run(fixer())
        for _ in range(80):
            r.frame([ob(0, 100), ob(5, 300)])
        for _ in range(10):                      # 0 misread as 9 (a 1-step neighbour)
            self.assertEqual(r.ids([ob(9, 100), ob(5, 300)]), [0, 5])

    def test_misread_onto_present_id_is_never_duplicated(self):
        r = Run(fixer())
        for _ in range(80):
            r.frame([ob(0, 100), ob(9, 300)])
        for _ in range(30):                      # both report 9
            self.assertEqual(r.ids([ob(9, 100), ob(9, 300)]), [0, 9])

    def test_duplicate_raw_ids_get_distinct_outputs(self):
        r = Run(fixer())
        for _ in range(80):                      # unconfirmed ones wait STILL_S
            out = r.ids([ob(3, 100), ob(3, 300), ob(3, 500)])
        self.assertEqual(len(out), 3)
        self.assertEqual(len(set(out)), 3)
        self.assertIn(3, out)
        self.assertTrue(all(0 <= k < N_IDS for k in out))

    def test_misread_start_is_held_then_correct(self):
        r = Run(fixer(first_delay_s=0.1))
        self.assertEqual(r.ids([ob(30, 100)]), [])          # held back
        outs = [r.ids([ob(0, 100)]) for _ in range(10)]
        self.assertEqual([o for o in outs if o], [[0]] * len([o for o in outs if o]))
        self.assertTrue(outs[-1] == [0])

    def test_sustained_new_reading_switches_after_frames(self):
        fx = fixer(switch_frames=5)
        r = Run(fx)
        for _ in range(12):                      # confirmed, not yet locked
            r.frame([ob(4, 100)])
        seen = [r.ids([ob(11, 100)])[0] for _ in range(30)]
        self.assertEqual(seen[0], 4)
        self.assertEqual(seen[-1], 11)
        self.assertEqual(fx.switches, 1)

    def test_locked_needs_more_frames(self):
        fx = fixer(switch_frames=5)
        r = Run(fx)
        for _ in range(80):
            r.frame([ob(4, 100)])
        self.assertEqual(fx.tracks[0].state, LOCKED)
        seen = [r.ids([ob(11, 100)])[0] for _ in range(12)]
        self.assertEqual(set(seen), {4})

    def test_short_dropout_keeps_identity(self):
        r = Run(fixer(lost_s=0.5))
        for _ in range(80):
            r.frame([ob(6, 100)])
        for _ in range(6):                       # 0.1 s gone
            r.frame([])
        self.assertEqual(r.ids([ob(40, 102)]), [6])

    def test_moving_object_is_followed(self):
        r = Run(fixer(gate_px=30))
        x = 100.0
        for _ in range(80):
            x += 8.0                             # 480 px/s
            out = r.ids([ob(2, x)])
        self.assertEqual(out, [2])
        self.assertEqual(len(r.fx.tracks), 1)

    def test_more_than_25_objects(self):
        r = Run(fixer())
        objs = [ob(i % 25, 50.0 * i) for i in range(27)]
        for _ in range(80):
            out = r.ids(objs)
        self.assertEqual(out, list(range(25)))

    def test_memory_keeps_id_of_marker_that_returns_misread(self):
        r = Run(fixer(lost_s=0.2))
        for _ in range(80):
            r.frame([ob(12, 700), ob(3, 300)])
        for _ in range(30):                      # lifted: track dies
            r.frame([ob(3, 300)])
        for _ in range(80):                      # back at the same spot, read as 90
            out = r.ids([ob(90, 702), ob(3, 300)])
        self.assertEqual(out, [3, 12])

    def test_memory_loses_to_real_readings(self):
        r = Run(fixer(lost_s=0.2))
        for _ in range(80):
            r.frame([ob(12, 700)])
        for _ in range(30):
            r.frame([])
        for _ in range(40):                      # another marker put there
            out = r.ids([ob(7, 700)])
        self.assertEqual(out, [7])

    def test_unlearned_reading_above_24_is_no_evidence(self):
        m = IdModel()
        ll = m.loglik(46)
        self.assertAlmostEqual(float(ll.max() - ll.min()), 0.0)

    def test_pin_overrides_and_learns(self):
        fx = fixer()
        r = Run(fx)
        for _ in range(20):
            r.frame([ob(46, 500), ob(14, 100)])  # someone else holds 14
        tid = next(t.tid for t in fx.tracks if t.raw == 46)
        self.assertTrue(fx.pin(tid, 14))
        for _ in range(IdModel.RELOG_EVERY + 5):
            out = r.frame([ob(46, 500), ob(14, 100)])
        got = {o.src_id: o.code_id for o in out}
        self.assertEqual(got[46], 14)            # pin wins the contested id
        self.assertNotEqual(got[14], 14)
        self.assertGreater(fx.model.counts[14, 46], IdModel.RELOG_EVERY)
        # a fresh fixer with the learned model reads 46 as 14 on its own
        r2 = Run(IdFixer(fx.model, gate_px=30))
        for _ in range(20):
            out = r2.ids([ob(46, 500)])
        self.assertEqual(out, [14])

    def test_carried_misread_is_not_bridged(self):
        fx = fixer()
        fx.table_px = 1000.0
        r = Run(fx)
        for _ in range(80):
            r.frame([ob(11, 100)])
        x = 100.0
        outs = []
        for _ in range(30):                      # carried at 0.5 table/s, read as 70
            x += 500.0 / FPS
            outs.append(r.ids([ob(70, x)]))
        self.assertEqual(outs[-1], [])
        self.assertGreater(fx.withheld, 0)

    def test_lifted_marker_misread_nearby_is_not_revived(self):
        r = Run(fixer(lost_s=0.2))
        for _ in range(80):
            r.frame([ob(16, 200)])
        for _ in range(30):                      # lifted
            r.frame([])
        x, outs = 260.0, []
        for _ in range(40):                      # waved below the camera, read as 38
            x += 300.0 / FPS
            outs.append(r.ids([ob(38, x)]))
        self.assertEqual([o for o in outs if o], [])
        for _ in range(30):                      # held still in the air < STILL_S
            outs.append(r.ids([ob(38, x)]))
        self.assertEqual([o for o in outs if o], [])

    def test_persistent_misread_on_table_is_sent_after_still(self):
        r = Run(fixer())
        outs = [r.ids([ob(46, 300)]) for _ in range(90)]
        self.assertEqual(outs[30], [])
        self.assertEqual(len(outs[-1]), 1)

    def test_still_misread_is_bridged(self):
        r = Run(fixer())
        for _ in range(80):
            r.frame([ob(11, 100)])
        for _ in range(30):
            self.assertEqual(r.ids([ob(70, 100)]), [11])

    def test_learned_misread_is_bridged_while_moving(self):
        fx = fixer()
        for _ in range(IdModel.RELOG_EVERY):
            fx.model.learn(11, 70)
        r = Run(fx)
        for _ in range(80):
            r.frame([ob(11, 100)])
        x = 100.0
        for _ in range(30):
            x += 500.0 / FPS
            out = r.ids([ob(70, x)])
        self.assertEqual(out, [11])

    def test_learning_counts_locked_misreads(self):
        fx = fixer(learn=True)
        r = Run(fx)
        for i in range(120):
            r.frame([ob(30 if i % 10 == 0 else 0, 100)])
        self.assertGreater(fx.model.counts[0, 30], 0)
        self.assertGreater(fx.model.counts[0, 0], fx.model.counts[0, 30])


class TestIdModel(unittest.TestCase):
    def test_prior_rows_normalized(self):
        m = IdModel()
        self.assertTrue(np.allclose(np.exp(m.logp).sum(axis=0), 1.0))
        self.assertIsNone(m.loglik(N_RAW))

    def test_learned_counts_change_likelihood_and_persist(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            m = IdModel(counts_path=path)
            before = m.loglik(30)[0] - m.loglik(30)[4]
            for _ in range(IdModel.RELOG_EVERY):
                m.learn(0, 30)
            self.assertGreater(m.loglik(30)[0] - m.loglik(30)[4], before)
            m.save_if_dirty()
            m2 = IdModel(counts_path=path)
            self.assertEqual(m2.counts[0, 30], IdModel.RELOG_EVERY)

    @unittest.skipUnless(os.path.isfile(default_trees_path(INSTALL_DIR)), "no default.trees")
    def test_tree_steps(self):
        shapes = parse_trees(default_trees_path(INSTALL_DIR))
        self.assertEqual(len(shapes), N_RAW)
        steps = tree_steps(shapes)
        self.assertEqual(steps[0, 0], 0)
        self.assertEqual(steps[0, 9], 1)         # {6,2,1,1,..} -> one dot lost
        self.assertEqual(steps[0, 108], 4)       # inverted copy: other root colour


class TestPipelineIdfix(unittest.TestCase):
    def pipe(self, enabled):
        rt = X.json.loads(X.json.dumps(X.EXHIBIT_DEFAULTS))
        rt.update(osc_enabled=False, idfix_enabled=enabled)
        p = bare_pipeline(rt)
        p.infer_on = True
        p.rtv = mock.Mock(format=(1000, 1000, 60))
        p._idfix = IdFixer(IdModel())
        p._idfix_out = []
        return p

    def frame(self, p, fid, real=True, sid=1):
        o = ExhibitObject(track_id=sid, code_id=fid, nx=0.2, ny=0.2, angle=0)
        p._process_frame([o], real=real)
        return sorted(p._ritual.objects)

    def test_off_passes_raw_ids(self):
        p = self.pipe(False)
        for _ in range(10):
            self.frame(p, 0)
        self.assertIn(30, self.frame(p, 30, sid=2))

    def test_on_corrects_and_clock_reuses_output(self):
        p = self.pipe(True)
        for _ in range(80):
            self.frame(p, 0)
        n = p._idfix.frames
        self.assertEqual(self.frame(p, 30, sid=2), [0])
        self.assertEqual(self.frame(p, 30, real=False, sid=2), [0])
        self.assertEqual(p._idfix.frames, n + 1)      # clock tick adds no evidence


if __name__ == "__main__":
    unittest.main()
