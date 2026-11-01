"""Frame-lived objects and contact-stable MST. No per-node degree limit."""
import math
from dataclasses import replace

from tracker.tension import mst_edges, pair_tension


class RitualState:
    def __init__(self):
        self.objects = {}
        self.missing = {}
        self.edges = set()
        self.pairs = {}
        self.heartbeat = -1

    def update(self, observations, roi=None):
        seen = set()
        for obj in observations:
            if roi and not (roi[0] <= obj.cx < roi[2] and roi[1] <= obj.cy < roi[3]):
                continue
            key = int(obj.code_id)
            if key in seen:
                continue
            seen.add(key)
            self.objects[key] = replace(obj)
            self.missing[key] = 0
        for key in list(self.objects):
            if key not in seen:
                self.missing[key] += 1
                if self.missing[key] >= 5:
                    del self.objects[key]
                    del self.missing[key]
        self.heartbeat = (self.heartbeat + 1) % 101
        return list(self.objects.values())

    def graph(self, long_px, d_near, d_far, margin=0.0):
        if not math.isfinite(margin) or margin < 0 or not 0 <= d_near + margin < d_far:
            raise ValueError("contact + margin must be finite and in [0, d_far)")
        nodes = list(self.objects.values())
        ids = list(self.objects)
        n = len(nodes)
        distances = [[0.0] * n for _ in nodes]
        for i in range(n):
            for j in range(i + 1, n):
                distances[i][j] = distances[j][i] = math.hypot(
                    nodes[i].cx - nodes[j].cx, nodes[i].cy - nodes[j].cy) / max(long_px, 1e-6)
        contact = d_near + margin
        # Compare unrounded distances: display rounding must not lock a near-contact edge.
        preferred = {(i, j) for i in range(n) for j in range(i + 1, n)
                     if tuple(sorted((ids[i], ids[j]))) in self.edges
                     and distances[i][j] <= contact}
        tree = mst_edges(distances, n, preferred)
        self.edges = {tuple(sorted((ids[i], ids[j]))) for i, j in tree}
        edges = [(i, j, pair_tension(distances[i][j], contact, d_far)) for i, j in tree]
        neighbours = [[] for _ in nodes]
        for i, j, tension in edges:
            neighbours[i].append((j, tension))
            neighbours[j].append((i, tension))
        tensions, pairs = [], {}
        for i, key in enumerate(ids):
            maximum = max((t for _, t in neighbours[i]), default=0.0)
            candidates = [j for j, t in neighbours[i] if t == maximum and t > 0]
            old = self.pairs.get(key, -1)
            if old in [ids[j] for j in candidates]:
                chosen = old
            elif candidates:
                chosen = ids[min(candidates, key=lambda j: (distances[i][j], ids[j]))]
            else:
                chosen = -1
            tensions.append(maximum)
            pairs[key] = chosen
        self.pairs = pairs
        return edges, tensions
