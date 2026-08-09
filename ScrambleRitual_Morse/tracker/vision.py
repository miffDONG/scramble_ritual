"""
프레임 단위 비전 코어: 웨이브폼 오브제 검출 + 접촉(맞닿음) 판정.

검출 (detect)
- 전경 분리: Otsu 임계값 (invert 설정으로 명/암 극성 선택, IR 백라이트=실루엣이면 invert)
- 모폴로지 open/close 로 노이즈 제거 후 외곽 컨투어 추출
  (close 커널은 contact_gap_px 보다 작아야 함 — 크면 접촉 틈이 메워져 한 덩어리가 됨)
- 각 오브제: 중심(모멘트), 기울기(minAreaRect 장축, [-90,90)도), 면적, 컨투어

접촉 (find_contacts)
- 서로 다른 두 컨투어가 contact_gap_px 이내로 근접하면 '맞닿음'
- 한쪽 마스크를 팽창시켜 교집합 픽셀이 생기는지 검사 (비볼록 형상도 정확)
- 접점 좌표 = 교집합 픽셀의 무게중심 → 어디가 맞닿았는지

겹침(두 오브제가 한 덩어리로 합쳐진 상태)은 프레임 단독으로는 판별 불가 —
시간적 추적이 필요하므로 scene.SceneTracker 가 담당한다.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

DEFAULTS = {
    "invert": False,           # True: 오브제가 배경보다 어두움 (IR 백라이트 실루엣)
    "thresh": 0,               # 0=Otsu 자동, >0=고정 밝기 임계값(0~255).
                               #   밝은 오브제(흰 UV프린트)만 잡고 책상·매트는 버릴 때 사용
    "blur_px": 5,
    "morph_open_px": 5,        # 스펙클 노이즈 제거
    "morph_close_px": 3,       # 내부 구멍 메움 — 오브제 간 틈(접촉 판정 대상)보다 작아야 함!
    "min_area_px": 400,
    "max_area_frac": 0.5,      # 프레임의 50% 이상은 조명 오류로 간주
    "max_objects": 0,          # >0: 면적 상위 N개만 남김 (오브제 수가 고정일 때 잡것 컷)
    "contact_gap_px": 6,       # 이 거리 이내면 '맞닿음'
    "contact_min_px": 4,       # 교집합 최소 픽셀 수 (노이즈 컷)
}


@dataclass
class Detection:
    cx: float
    cy: float
    angle: float               # 주축 각도, [-90, 90) deg, 픽셀 좌표계
    area: float
    contour: np.ndarray = field(repr=False)
    bbox: tuple = (0, 0, 0, 0)  # x, y, w, h


def _cfg(cfg):
    out = dict(DEFAULTS)
    if cfg:
        out.update(cfg)
    return out


def preprocess(frame, cfg=None) -> np.ndarray:
    """프레임 -> 이진 마스크 (오브제=255)."""
    c = _cfg(cfg)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    b = c["blur_px"] | 1
    blur = cv2.GaussianBlur(gray, (b, b), 0)
    if c["thresh"] > 0:        # 고정 임계값 — 장면 전체가 밝아도 흔들리지 않음
        _, mask = cv2.threshold(blur, c["thresh"], 255, cv2.THRESH_BINARY)
    else:                      # Otsu 자동 — 오브제/배경 2계조가 뚜렷할 때만 적합
        _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if c["invert"]:
        mask = cv2.bitwise_not(mask)
    ko = np.ones((c["morph_open_px"], c["morph_open_px"]), np.uint8)
    kc = np.ones((c["morph_close_px"], c["morph_close_px"]), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, ko)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kc)
    return mask


def detect(frame, cfg=None):
    """프레임 -> (마스크, [Detection])."""
    c = _cfg(cfg)
    mask = preprocess(frame, c)
    max_area = mask.shape[0] * mask.shape[1] * c["max_area_frac"]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    dets = []
    for cnt in contours:
        m = cv2.moments(cnt)
        if m["m00"] < c["min_area_px"] or m["m00"] > max_area:
            continue
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        # 기울기: minAreaRect 장축 — 모멘트 주축은 웨이브 위상에 따라
        # 수 도(degree) 편향이 생기므로 외접 회전사각형이 더 정확하다
        (_, _), (rw, rh), rang = cv2.minAreaRect(cnt)
        if rw < rh:
            rang += 90.0           # 장축 기준으로 통일
        angle = rang % 180.0
        if angle >= 90.0:
            angle -= 180.0
        dets.append(Detection(cx, cy, angle, m["m00"], cnt, cv2.boundingRect(cnt)))
    if c["max_objects"] > 0 and len(dets) > c["max_objects"]:
        # 면적 큰 순으로 상위 N개만 — 웨이브폼이 가장 큰 밝은 덩어리이므로 잡것 탈락
        dets.sort(key=lambda d: d.area, reverse=True)
        dets = dets[:c["max_objects"]]
    dets.sort(key=lambda d: (d.cx, d.cy))
    return mask, dets


def find_contacts(dets, cfg=None):
    """[Detection] -> [(i, j, (x, y))]  i<j, (x,y)=접점 좌표."""
    c = _cfg(cfg)
    gap = c["contact_gap_px"]
    out = []
    for i in range(len(dets)):
        for j in range(i + 1, len(dets)):
            a, b = dets[i], dets[j]
            # 바운딩박스 근접 게이트 (비싼 마스크 연산 전 컷)
            if _bbox_gap(a.bbox, b.bbox) > gap * 2:
                continue
            pt = _contact_point(a, b, gap, c["contact_min_px"])
            if pt is not None:
                out.append((i, j, pt))
    return out


def _bbox_gap(b1, b2):
    x1, y1, w1, h1 = b1
    x2, y2, w2, h2 = b2
    dx = max(x2 - (x1 + w1), x1 - (x2 + w2), 0)
    dy = max(y2 - (y1 + h1), y1 - (y2 + h2), 0)
    return max(dx, dy)


def _contact_point(a, b, gap, min_px):
    """a 마스크를 gap 만큼 팽창시켜 b 와의 교집합 무게중심을 구한다."""
    pad = gap + 2
    x = min(a.bbox[0], b.bbox[0]) - pad
    y = min(a.bbox[1], b.bbox[1]) - pad
    xe = max(a.bbox[0] + a.bbox[2], b.bbox[0] + b.bbox[2]) + pad
    ye = max(a.bbox[1] + a.bbox[3], b.bbox[1] + b.bbox[3]) + pad
    w, h = xe - x, ye - y
    ma = np.zeros((h, w), np.uint8)
    mb = np.zeros((h, w), np.uint8)
    cv2.drawContours(ma, [a.contour - [x, y]], -1, 255, cv2.FILLED)
    cv2.drawContours(mb, [b.contour - [x, y]], -1, 255, cv2.FILLED)
    k = np.ones((gap * 2 + 1, gap * 2 + 1), np.uint8)
    inter = cv2.bitwise_and(cv2.dilate(ma, k), mb)
    n = cv2.countNonZero(inter)
    if n < min_px:
        return None
    m = cv2.moments(inter, binaryImage=True)
    return (x + m["m10"] / m["m00"], y + m["m01"] / m["m00"])
