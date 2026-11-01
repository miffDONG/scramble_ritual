import unittest
from types import SimpleNamespace

from tracker import tension as T


def node(x, y):
    return SimpleNamespace(cx=float(x), cy=float(y))


class TestPairAndFold(unittest.TestCase):
    def test_pair_tension_curve(self):
        d_near, d_far = 0.1, 0.5
        self.assertEqual(T.pair_tension(0.6, d_near, d_far), 0.0)
        self.assertEqual(T.pair_tension(0.5, d_near, d_far), 0.0)
        self.assertEqual(T.pair_tension(0.1, d_near, d_far), 1.0)
        self.assertEqual(T.pair_tension(0.0, d_near, d_far), 1.0)     # overlap stays 1
        self.assertAlmostEqual(T.pair_tension(0.3, d_near, d_far), 0.5, places=3)

    def test_fold(self):
        self.assertEqual(T._fold([0.2, 0.8, 0.0], "max"), 0.8)
        self.assertEqual(T._fold([0.2, 0.8, 0.0], "min"), 0.0)
        self.assertAlmostEqual(T._fold([0.2, 0.8, 0.0], "avg"), 1.0 / 3.0)
        self.assertEqual(T._fold([], "max"), 0.0)

    def test_thresholds(self):
        d_near, d_far, src = T.tension_thresholds({}, 100.0, 1000.0)
        self.assertAlmostEqual(d_near, 100.0 / 3 ** 0.5 / 1000.0)
        self.assertEqual(d_far, 0.5)
        self.assertTrue(src.startswith("side="))
        d_near, _, src = T.tension_thresholds({"tension_contact": 0.07}, 0, 1000.0)
        self.assertEqual((d_near, src), (0.07, "fixed"))


class TestMst(unittest.TestCase):
    def test_mst_edges_chain(self):
        # four points on a line: MST is the chain 0-1-2-3, never the long links
        pts = [node(0, 0), node(10, 0), node(20, 0), node(30, 0)]
        n = len(pts)
        dist = [[abs(pts[i].cx - pts[j].cx) for j in range(n)] for i in range(n)]
        self.assertEqual(T.mst_edges(dist, n), [(0, 1), (1, 2), (2, 3)])

    def test_mst_edges_count_and_connectivity(self):
        pts = [node(0, 0), node(100, 0), node(0, 100), node(100, 100), node(50, 50)]
        n = len(pts)
        dist = [[((pts[i].cx - pts[j].cx) ** 2 + (pts[i].cy - pts[j].cy) ** 2) ** 0.5
                 for j in range(n)] for i in range(n)]
        edges = T.mst_edges(dist, n)
        self.assertEqual(len(edges), n - 1)
        # the centre is the hub: every corner links to it (all corner-corner
        # links are longer than corner-centre)
        self.assertEqual(sorted(edges), [(0, 4), (1, 4), (2, 4), (3, 4)])
        self.assertEqual(T.mst_edges([[0.0]], 1), [])

    def test_tension_graph_mst_is_default_and_uses_tree_edges_only(self):
        # three objects in a line: 0 —(near)— 1 —(near)— 2 ; 0-2 is far
        pts = [node(0, 0), node(100, 0), node(200, 0)]
        long_px = 1000.0
        d_near, d_far = 0.05, 0.5
        edges, node_t = T.tension_graph(pts, long_px, d_near, d_far)   # default connect
        self.assertEqual(sorted((i, j) for i, j, _ in edges), [(0, 1), (1, 2)])
        # avg fold exposes the topology: the middle object has two tree
        # neighbours at 0.1, the ends have one — with `all`, the ends would
        # also see the 0.2 link and average lower
        _, t_mst = T.tension_graph(pts, long_px, d_near, d_far, "mst", "avg")
        _, t_all = T.tension_graph(pts, long_px, d_near, d_far, "all", "avg")
        self.assertAlmostEqual(t_mst[0], T.pair_tension(0.1, d_near, d_far), places=3)
        self.assertLess(t_all[0], t_mst[0])
        self.assertAlmostEqual(t_mst[1], t_all[1], places=3)      # middle: same links

    def test_tension_graph_other_topologies(self):
        pts = [node(0, 0), node(100, 0), node(200, 0)]
        edges_all, _ = T.tension_graph(pts, 1000.0, 0.05, 0.5, "all")
        self.assertEqual(len(edges_all), 3)
        edges_knn, _ = T.tension_graph(pts, 1000.0, 0.05, 0.5, "knn", knn=1)
        self.assertEqual(sorted((i, j) for i, j, _ in edges_knn), [(0, 1), (1, 2)])
        edges_near, _ = T.tension_graph(pts, 1000.0, 0.05, 0.5, "near", link_radius=0.15)
        self.assertEqual(sorted((i, j) for i, j, _ in edges_near), [(0, 1), (1, 2)])
        self.assertEqual(T.tension_graph([], 1000.0, 0.05, 0.5), ([], []))
        edges1, t1 = T.tension_graph([node(5, 5)], 1000.0, 0.05, 0.5)
        self.assertEqual((edges1, t1), ([], [0.0]))

    def test_object_tension_matches_graph_for_all(self):
        pts = [node(0, 0), node(100, 0), node(200, 0)]
        _, node_t = T.tension_graph(pts, 1000.0, 0.05, 0.5, "all", "avg")
        self.assertAlmostEqual(
            node_t[0], T.object_tension(0, [0.1, 0.2], 0.05, 0.5, "all", "avg"), places=3)


class TestRoi(unittest.TestCase):
    def test_roi_to_px(self):
        self.assertEqual(T.roi_to_px([0.5, 0.5, 0.5, 0.5], 1280, 800), (640, 400, 1280, 800))
        self.assertIsNone(T.roi_to_px(None, 10, 10))
        self.assertIsNone(T.roi_to_px([0, 0, 0, 1], 10, 10))


if __name__ == "__main__":
    unittest.main()
