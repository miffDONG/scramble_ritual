"""
시간적 추적: 검출 결과에 ID를 부여하고 겹침/분리를 판별한다.

겹침(overlap)의 정의
- 두 오브제가 카메라 시점에서 포개지면 컨투어가 '한 덩어리'로 합쳐진다.
- 단일 프레임으로는 큰 오브제 하나와 구분 불가 → 직전 프레임까지 따로 추적되던
  두 트랙이 같은 검출 하나로 수렴하면 그 검출을 '겹침 그룹'으로 판정한다.
- 덩어리가 다시 나뉘면 가장 가까운 트랙에 재배정(분리, split).

이벤트 (사운드 트리거용)
    enter / leave   오브제 등장·퇴장
    merge / split   겹침 시작·해제 (멤버 id 목록 포함)
    touch / release 맞닿음 시작·해제 (쌍 + 접점)

특징 (features) — 사운드 매핑의 입력
    n            보이는 덩어리 수
    disorder     기울기 무질서도 0~1 (방향 통계, 0=모두 평행)
    scatter      배열 비선형성 0~1 (중심들이 한 직선 위면 0, 등방 분포면 1)
    contact_ratio 맞닿은 쌍 수 / (n-1)
    chain        접촉 그래프 최대 연결 성분 크기 / n (0~1, 1=전부 한 줄로 연결)
    overlap_ratio 겹침 그룹에 묶인 트랙 수 / 전체 트랙 수
"""

import math
from dataclasses import dataclass, field

DEFAULTS = {
    "match_dist_px": 80,      # 트랙-검출 매칭 최대 거리 (프레임당 이동량보다 크게)
    "max_missed": 8,          # 이 프레임 수만큼 안 보이면 트랙 제거
    "pos_alpha": 0.55,        # 위치 EMA 계수 (1=스무딩 없음)
    "angle_alpha": 0.45,
}


@dataclass
class Track:
    id: int
    cx: float
    cy: float
    angle: float
    area: float
    age: int = 0
    missed: int = 0
    group: frozenset = None    # 겹침 그룹 멤버 id들 (자신 포함), 없으면 None
    solo_area: float = 0.0     # 단독으로 보였을 때의 면적 (겹침 정도 추정용)


@dataclass
class SceneState:
    objects: list = field(default_factory=list)    # [(logical_id, Detection)]
    tracks: dict = field(default_factory=dict)     # id -> Track
    contacts: list = field(default_factory=list)   # [(idA, idB, (x, y))]
    overlaps: list = field(default_factory=list)   # [frozenset(ids)]
    events: list = field(default_factory=list)     # [(type, data)]


def _ang_lerp(a, b, alpha):
    """mod-180 각도 EMA — 2θ 벡터 공간에서 보간."""
    ax, ay = math.cos(math.radians(2 * a)), math.sin(math.radians(2 * a))
    bx, by = math.cos(math.radians(2 * b)), math.sin(math.radians(2 * b))
    x, y = ax + (bx - ax) * alpha, ay + (by - ay) * alpha
    return math.degrees(math.atan2(y, x)) / 2.0


class SceneTracker:
    def __init__(self, cfg=None):
        self.cfg = dict(DEFAULTS)
        if cfg:
            self.cfg.update(cfg)
        self.tracks = {}
        self.next_id = 1
        self._prev_contact_keys = set()
        self._prev_groups = set()

    # ── 메인 업데이트 ────────────────────────────────────────────
    def update(self, dets, det_contacts) -> SceneState:
        c = self.cfg
        events = []

        # 1) 트랙-검출 일대일 그리디 매칭 (거리 오름차순)
        cand = []
        for tid, tr in self.tracks.items():
            for di, d in enumerate(dets):
                dist = math.hypot(tr.cx - d.cx, tr.cy - d.cy)
                if dist <= c["match_dist_px"]:
                    cand.append((dist, tid, di))
        cand.sort(key=lambda x: x[0])
        track_of_det = {}            # det index -> [track ids]
        det_of_track = {}
        for dist, tid, di in cand:
            if tid in det_of_track or di in track_of_det:
                continue
            det_of_track[tid] = di
            track_of_det[di] = [tid]

        # 2) 미배정 트랙: 이미 배정된 검출의 컨투어 안에 있으면 합류 -> 겹침 그룹
        import cv2
        for tid, tr in self.tracks.items():
            if tid in det_of_track:
                continue
            for di, d in enumerate(dets):
                if di not in track_of_det:
                    continue
                if cv2.pointPolygonTest(d.contour, (tr.cx, tr.cy), True) >= -c["match_dist_px"] * 0.5:
                    track_of_det[di].append(tid)
                    det_of_track[tid] = di
                    break

        # 3) 트랙 갱신 / 신규 / 소실 처리
        for di, d in enumerate(dets):
            tids = track_of_det.get(di)
            if not tids:
                tid = self.next_id
                self.next_id += 1
                self.tracks[tid] = Track(tid, d.cx, d.cy, d.angle, d.area,
                                         solo_area=d.area)
                track_of_det[di] = [tid]
                events.append(("enter", {"id": tid}))
                continue
            for tid in tids:
                tr = self.tracks[tid]
                tr.cx += (d.cx - tr.cx) * c["pos_alpha"]
                tr.cy += (d.cy - tr.cy) * c["pos_alpha"]
                tr.angle = _ang_lerp(tr.angle, d.angle, c["angle_alpha"])
                tr.area = d.area
                if len(tids) == 1:
                    tr.solo_area = d.area
                tr.age += 1
                tr.missed = 0

        for tid in list(self.tracks):
            if tid not in det_of_track:
                tr = self.tracks[tid]
                tr.missed += 1
                if tr.missed > c["max_missed"]:
                    events.append(("leave", {"id": tid}))
                    del self.tracks[tid]

        # 4) 겹침 그룹 확정 + merge/split 이벤트
        groups = set()
        group_det = {}
        for di, tids in track_of_det.items():
            g = frozenset(tids) if len(tids) >= 2 else None
            for tid in tids:
                if tid in self.tracks:
                    self.tracks[tid].group = g
            if g:
                groups.add(g)
                group_det[g] = dets[di]
        for g in groups - self._prev_groups:
            # 면적비: ~1.0 = 옆으로 맞닿아 합쳐짐, 낮을수록 위로 포개짐(적층)
            solo_sum = sum(self.tracks[i].solo_area for i in g if i in self.tracks)
            ratio = group_det[g].area / solo_sum if solo_sum > 0 else 1.0
            events.append(("merge", {"ids": sorted(g), "area_ratio": round(ratio, 3)}))
        for g in self._prev_groups - groups:
            alive = [i for i in g if i in self.tracks]
            if alive:
                events.append(("split", {"ids": sorted(g)}))
        self._prev_groups = groups

        # 5) 논리 객체 목록 + 접촉을 논리 id 로 변환 (+ touch/release 이벤트)
        objects = [(min(track_of_det[di]), d) for di, d in enumerate(dets)
                   if di in track_of_det]
        contacts = []
        keys = set()
        for i, j, pt in det_contacts:
            if i in track_of_det and j in track_of_det:
                a, b = min(track_of_det[i]), min(track_of_det[j])
                if a > b:
                    a, b = b, a
                contacts.append((a, b, pt))
                keys.add((a, b))
        for a, b in keys - self._prev_contact_keys:
            pt = next(p for x, y, p in contacts if (x, y) == (a, b))
            events.append(("touch", {"ids": [a, b], "point": pt}))
        for a, b in self._prev_contact_keys - keys:
            events.append(("release", {"ids": [a, b]}))
        self._prev_contact_keys = keys

        return SceneState(objects, dict(self.tracks), contacts,
                          sorted(groups, key=sorted), events)


# ── 특징 추출 ────────────────────────────────────────────────────
def features(state: SceneState, frame_size) -> dict:
    w, h = frame_size
    objs = state.objects
    n = len(objs)
    if n == 0:
        return {"n": 0, "disorder": 0.0, "scatter": 0.0, "contact_ratio": 0.0,
                "chain": 0.0, "overlap_ratio": 0.0}

    # 방향 무질서도: 1 - |mean(e^{i2θ})|
    sx = sum(math.cos(math.radians(2 * d.angle)) for _, d in objs)
    sy = sum(math.sin(math.radians(2 * d.angle)) for _, d in objs)
    disorder = 1.0 - math.hypot(sx, sy) / n if n > 1 else 0.0

    # 공간 흩어짐: 배열의 비선형성 — 중심들이 한 직선 위에 있으면 0, 등방 분포면 1
    # (절대 퍼짐이 아니라 '가지런함'의 붕괴를 측정 — 일렬 정렬은 넓게 놓여도 0)
    mx = sum(d.cx for _, d in objs) / n
    my = sum(d.cy for _, d in objs) / n
    if n >= 3:
        sxx = sum((d.cx - mx) ** 2 for _, d in objs) / n
        syy = sum((d.cy - my) ** 2 for _, d in objs) / n
        sxy = sum((d.cx - mx) * (d.cy - my) for _, d in objs) / n
        half, off = (sxx + syy) / 2.0, math.hypot((sxx - syy) / 2.0, sxy)
        lmax, lmin = half + off, max(0.0, half - off)
        scatter = lmin / lmax if lmax > 1e-6 else 0.0
    else:
        scatter = 0.0

    # 접촉 그래프 최대 연결 성분 (union-find)
    parent = {lid: lid for lid, _ in objs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in state.contacts:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    sizes = {}
    for lid, _ in objs:
        r = find(lid)
        sizes[r] = sizes.get(r, 0) + 1
    chain = max(sizes.values()) / n if n > 1 else 0.0

    n_tracks = len(state.tracks) or 1
    merged = sum(len(g) for g in state.overlaps)

    return {
        "n": n,
        "disorder": max(0.0, min(1.0, disorder)),
        "scatter": scatter,
        "contact_ratio": min(1.0, len(state.contacts) / max(n - 1, 1)),
        "chain": chain,
        "overlap_ratio": min(1.0, merged / n_tracks),
    }
