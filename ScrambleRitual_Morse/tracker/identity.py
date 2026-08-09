"""
오브제 정체성 — 실루엣 형상으로 '어느 웨이브폼인지' 식별한다.

원리: 웨이브폼 오브제의 형상은 그 사운드 조각의 진폭 포락선이다(제작 규약).
따라서 검출 실루엣을 장축 기준 정준 방향으로 돌려 '장축을 따라 폭 프로파일'을
재면 그것이 곧 포락선 = 조각의 지문이 된다.

- 회전 불변: 검출이 주는 minAreaRect 장축 각도로 마스크를 역회전
- 180도 모호성: 프로파일 정/역방향 둘 다 비교
- 스케일: 카메라 고정 전제 — 절대 폭(공칭 norm_px 로 나눔)이 진폭 정보를 보존
  (합성 16형상 x 4회전 = 64/64 식별, Hu 모멘트 matchShapes 는 45/64 에 그침)

전시 등록 플로우: 오브제를 하나씩 올려 ShapeIdentifier.register() -> 저장/로드.
손/팔 등 미등록 실루엣은 어떤 기준과도 안 맞아 자동 배제된다.
"""

import math

import cv2
import numpy as np

DEFAULTS = {
    "bins": 48,          # 프로파일 해상도
    "norm_px": 30.0,     # 공칭 최대 폭(px) — venue 캘리브레이션 대상
    "thresh": 0.10,      # 이하면 일치 (합성 자기점수 최대 0.058 의 ~1.7배 여유)
    "confirm": 3,        # 연속 일치 프레임 수 -> 확정 (히스테리시스)
}


def width_profile(det, bins=48, norm_px=30.0):
    """Detection -> 장축 방향 폭 프로파일 (bins,) — 정준 회전한 채운 마스크의 열별 폭."""
    x, y, w, h = det.bbox
    pad = 6
    m = np.zeros((h + 2 * pad, w + 2 * pad), np.uint8)
    cv2.drawContours(m, [det.contour - [x - pad, y - pad]], -1, 255, cv2.FILLED)
    H, W = m.shape
    diag = int(math.hypot(W, H)) + 4
    canvas = np.zeros((diag, diag), np.uint8)
    oy, ox = (diag - H) // 2, (diag - W) // 2
    canvas[oy:oy + H, ox:ox + W] = m
    M = cv2.getRotationMatrix2D((diag / 2, diag / 2), det.angle, 1.0)
    rot = cv2.warpAffine(canvas, M, (diag, diag))
    cols = (rot > 127).sum(axis=0).astype(np.float64)
    nz = np.nonzero(cols)[0]
    if len(nz) < 2:
        return np.zeros(bins)
    cols = cols[nz[0]:nz[-1] + 1]
    prof = np.interp(np.linspace(0, 1, bins),
                     np.linspace(0, 1, len(cols)), cols)
    return prof / norm_px


class ShapeIdentifier:
    """기준 프로파일 뱅크와 대조해 프래그먼트 인덱스를 돌려준다."""

    def __init__(self, cfg=None):
        self.cfg = dict(DEFAULTS)
        if cfg:
            self.cfg.update(cfg)
        self.refs = []          # [(frag_idx, profile)]
        self.labels = {}        # frag_idx -> 라벨

    # ── 등록 ─────────────────────────────────────────────────────
    def register(self, frag_idx, det, label=""):
        c = self.cfg
        self.refs.append((frag_idx, width_profile(det, c["bins"], c["norm_px"])))
        self.labels[frag_idx] = label or str(frag_idx)

    @classmethod
    def from_bank(cls, bank, cfg=None):
        """합성 형상으로 뱅크 전체 등록 — 시뮬레이션/개발용.
        (실물 전시는 캘리브레이션 때 실촬영 실루엣으로 register)"""
        from . import synthetic
        from .vision import detect
        ident = cls(cfg)
        shapes = synthetic.bank_shapes(bank)
        for i, poly in enumerate(shapes):
            frame = synthetic.render([(320, 240, 0.0)], polys=[poly])
            _, dets = detect(frame, {"max_objects": 0})
            if len(dets) == 1:
                ident.register(i, dets[0], bank.labels[i])
        return ident

    # ── 대조 ─────────────────────────────────────────────────────
    def match(self, det):
        """Detection -> (frag_idx | None, score). None = 미등록 실루엣(손 등)."""
        c = self.cfg
        p = width_profile(det, c["bins"], c["norm_px"])
        pr = p[::-1]
        best, best_s = None, 1e9
        for fi, ref in self.refs:
            s = min(np.abs(p - ref).mean(), np.abs(pr - ref).mean())
            if s < best_s:
                best, best_s = fi, s
        if best_s > c["thresh"]:
            return None, best_s
        return best, best_s


class IdentityTracker:
    """트랙 id 에 프래그먼트를 히스테리시스로 확정 배정한다.

    - 매 프레임 (lid, frag) 투표를 받아 연속 confirm 회 일치 시 확정
    - 확정 뒤에는 재대조 불필요 (비용 절감) — 트랙이 죽으면 해제
    - 같은 frag 가 두 트랙에 확정되는 것을 방지 (먼저 확정한 쪽 우선)
    """

    def __init__(self, confirm=3):
        self.confirm = confirm
        self.assigned = {}       # lid -> frag (확정)
        self._votes = {}         # lid -> [frag, count]

    def vote(self, lid, frag):
        if lid in self.assigned or frag is None:
            return
        if frag in self.assigned.values():
            return
        v = self._votes.get(lid)
        if v and v[0] == frag:
            v[1] += 1
            if v[1] >= self.confirm:
                self.assigned[lid] = frag
                del self._votes[lid]
        else:
            self._votes[lid] = [frag, 1]

    def prune(self, alive_lids):
        for lid in list(self.assigned):
            if lid not in alive_lids:
                del self.assigned[lid]
        for lid in list(self._votes):
            if lid not in alive_lids:
                del self._votes[lid]

    def frag_of(self, lid):
        return self.assigned.get(lid)
