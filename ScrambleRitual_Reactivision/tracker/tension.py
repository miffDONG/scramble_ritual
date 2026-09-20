"""
Tension math + overlay for the exhibition server (webui/exhibit_server.py).

Everything here is source-agnostic: nodes only need .cx/.cy in frame pixels,
and distances are normalized by the TABLE (ROI) long side so 0..1 tension
means the same physical closeness on any camera / zoom / resolution.
The default connection graph is the minimum spanning tree (see tension_graph).
"""

import math

import cv2


def roi_to_px(roi, w, h):
    """Normalized [x, y, w, h] (0..1) -> clamped integer (x1, y1, x2, y2),
    or None if unset / degenerate."""
    if not roi or len(roi) != 4:
        return None
    x, y, rw, rh = roi
    if rw <= 0 or rh <= 0:
        return None
    x1 = max(0, min(w - 1, int(round(x * w))))
    y1 = max(0, min(h - 1, int(round(y * h))))
    x2 = max(x1 + 1, min(w, int(round((x + rw) * w))))
    y2 = max(y1 + 1, min(h, int(round((y + rh) * h))))
    return (x1, y1, x2, y2)


#: two equilateral plates touch edge-to-edge (one rotated 180°) at exactly
#: side / sqrt(3) between centres — the closest they can be without overlapping
TRI_MIN_CENTER_FACTOR = 1.0 / math.sqrt(3.0)


def tension_thresholds(rt, side_px, long_px):
    """Distance thresholds for the tension curve, both normalized by the TABLE
    (ROI) LONG side so tension 0..1 means the same physical closeness on any
    source or zoom, and so `d_far` is exactly half the long side.

        d_far  = tension reaches 0    = half the table's long side  -> 0.5
        d_near = tension reaches 1    = edge-touching centre distance
                                        (side/sqrt3), object-size derived

    Returns (d_near, d_far, source) where source describes what set d_near.
    Distance passed to pair_tension must be normalized the same way:
        dist_norm = centre_distance_px / long_px
    """
    d_far = 0.5                             # half the long side, by definition
    if side_px and side_px > 0 and long_px > 0:
        return (side_px * TRI_MIN_CENTER_FACTOR) / long_px, d_far, \
            f"side={side_px:.0f}px"
    # fallback when the object size is unknown (no calibration / not objects)
    return float(rt.get("tension_contact", 0.06)), d_far, "fixed"


def pair_tension(dist, d_near, d_far):
    """Monotonic tension between two plates as they approach — no drop on
    overlap (that '→0' replacement was removed).

        멀어짐        dist >= d_far           -> 0
        가까워지는 중  d_far .. d_near         -> rise 0 -> 1 (linear)
        변이 닿음·겹침 dist <= d_near          -> 1 (stays at max)

    `dist`, `d_near`, `d_far` are all normalized by the table long side.
    """
    if dist >= d_far:
        return 0.0
    if dist <= d_near:
        return 1.0
    return round((d_far - dist) / max(d_far - d_near, 1e-6), 4)


def _fold(values, rule):
    """Combine a list of per-edge tensions into one scalar. Empty -> 0."""
    if not values:
        return 0.0
    if rule == "min":
        return min(values)
    if rule == "avg":
        return sum(values) / len(values)
    return max(values)                     # "max" (default)


def object_tension(i, dists, d_near, d_far, connect="all", fold="max", knn=3,
                   link_radius=None):
    """Tension on object i, folded from its distances to the other n-1 objects.

    `dists` is the list of normalized centre distances from i to every other
    object (already excluding i itself).

        connect: which neighbours are LINKED (graph topology)
            all  — every other object (n-1)
            near — only those within `link_radius` (a tunable connection
                   radius, independent of the tension curve's d_far)
            knn  — the k nearest objects
        fold: how the per-edge tensions combine — min / avg / max

    `link_radius` defaults to d_far when unset (legacy behaviour). Set it
    SMALLER than d_far so 'near' actually prunes distant links — otherwise on
    a normal table every pair sits inside d_far and near == all.
    """
    if not dists:
        return 0.0
    edges = sorted(dists)
    if connect == "near":
        r = d_far if link_radius is None else link_radius
        edges = [d for d in edges if d < r]
    elif connect == "knn":
        edges = edges[:max(1, int(knn))]
    tensions = [pair_tension(d, d_near, d_far) for d in edges]
    return round(_fold(tensions, fold), 4)


def mst_edges(dist, n):
    """Minimum spanning tree (Kruskal) over a dense distance matrix.
    Returns [(i, j)] with i < j — n-1 edges linking every node."""
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    out = []
    for _d, i, j in sorted((dist[i][j], i, j)
                           for i in range(n) for j in range(i + 1, n)):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
            out.append((i, j))
            if len(out) == n - 1:
                break
    return out


def tension_graph(nodes, long_px, d_near, d_far, connect="mst", fold="max",
                  knn=3, link_radius=None):
    """The tension graph the algorithm actually builds — the single source of
    truth for both the OSC per-object value and the overlay visualization.

    `nodes` carry .cx/.cy (frame pixels). Distances are normalized by the
    table long side. Returns (edges, node_tensions):
        edges          — [(i, j, edge_tension)] undirected UNION of every
                         node's considered neighbours (so knn's asymmetric
                         links still show as a connection)
        node_tensions  — [float] per node

    connect selects the graph topology (which neighbours a node folds over):
        mst  — minimum spanning tree over centre distances (default): every
               object is linked into ONE tree with n-1 edges, so the layout
               reads as a single chain/branching structure and each object's
               tension comes only from its tree neighbours
        all  — every other object (n-1)
        near — only those within `link_radius` (defaults to d_far)
        knn  — the k nearest objects
    all/near/knn mirror object_tension() exactly; mst needs the whole set and
    therefore only exists here.
    """
    n = len(nodes)
    if n == 0:
        return [], []
    long_px = max(long_px, 1e-6)
    r_near = d_far if link_radius is None else link_radius
    dist = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = math.hypot(nodes[i].cx - nodes[j].cx,
                           nodes[i].cy - nodes[j].cy) / long_px
            dist[i][j] = dist[j][i] = d
    considered = []
    if connect == "mst":
        adj = [[] for _ in range(n)]
        for i, j in mst_edges(dist, n):
            adj[i].append(j)
            adj[j].append(i)
        for i in range(n):
            considered.append(sorted(adj[i], key=lambda j: dist[i][j]))
    else:
        for i in range(n):
            order = sorted((j for j in range(n) if j != i), key=lambda j: dist[i][j])
            if connect == "near":
                order = [j for j in order if dist[i][j] < r_near]
            elif connect == "knn":
                order = order[:max(1, int(knn))]
            considered.append(order)
    node_t = []
    for i in range(n):
        ts = [pair_tension(dist[i][j], d_near, d_far) for j in considered[i]]
        node_t.append(round(_fold(ts, fold), 4) if ts else 0.0)
    edges, seen = [], set()
    for i in range(n):
        for j in considered[i]:
            key = (i, j) if i < j else (j, i)
            if key in seen:
                continue
            seen.add(key)
            edges.append((key[0], key[1],
                          pair_tension(dist[key[0]][key[1]], d_near, d_far)))
    return edges, node_t


def tension_color(t):
    """BGR ramp for a tension value: light gray (0) -> hot orange-red (1)."""
    t = max(0.0, min(1.0, t))
    return (int(200 * (1 - t)), int(200 - 110 * t), int(200 + 55 * t))


def draw_tension_graph(vis, graph):
    """Draw the tension graph: edges colored/weighted by pair tension, and each
    node's folded tension. `graph` = (node_xy, edges, node_tensions)."""
    points, edges, node_t = graph
    pts = [(int(x), int(y)) for x, y in points]
    for i, j, t in edges:
        if t <= 0.001:                 # far / inactive: faint thin link
            cv2.line(vis, pts[i], pts[j], (70, 70, 70), 1, cv2.LINE_AA)
            continue
        col = tension_color(t)
        cv2.line(vis, pts[i], pts[j], col, 1 + int(round(2 * t)), cv2.LINE_AA)
        if t >= 0.1:                    # label the taut edges at their midpoint
            mx, my = (pts[i][0] + pts[j][0]) // 2, (pts[i][1] + pts[j][1]) // 2
            cv2.putText(vis, f"{t:.2f}", (mx - 12, my - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
    for (x, y), t in zip(pts, node_t):  # node = folded tension
        col = tension_color(t)
        cv2.circle(vis, (x, y), 5, col, -1, cv2.LINE_AA)
        cv2.putText(vis, f"{t:.2f}", (x + 9, y - 9),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
    return vis
