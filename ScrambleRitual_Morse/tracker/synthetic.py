"""
합성 씬 생성기 — 카메라 없이 파이프라인을 검증한다.

웨이브폼 오브제: 사인 곡선을 따라 휘는 가로 스트립 폴리곤.
render() 는 지정한 (cx, cy, angle)에 오브제들을 그린 그레이 프레임을 반환하므로
테스트가 '정답값'과 검출 결과를 비교할 수 있다.

scenario(t) 는 데모용 타임라인: 정렬 -> 회전·산개 -> 접촉 -> 겹침 -> 복귀.
"""

import math

import cv2
import numpy as np

SIZE = (640, 480)              # (w, h)


def waveform_poly(length=120, amp=10, thickness=18, n_periods=2, steps=48):
    """원점 중심, x축 방향 웨이브 스트립 폴리곤 (Nx2 float) — 기본/레거시 형상."""
    xs = np.linspace(-length / 2, length / 2, steps)
    wave = amp * np.sin(np.linspace(0, 2 * math.pi * n_periods, steps))
    top = np.stack([xs, wave - thickness / 2], axis=1)
    bot = np.stack([xs[::-1], wave[::-1] + thickness / 2], axis=1)
    return np.concatenate([top, bot])


def waveform_poly_from_audio(audio, length=120.0, max_half=15.0, min_half=4.5,
                             steps=64, smooth=5):
    """오디오 조각의 진폭 포락선 -> 웨이브폼 실루엣 폴리곤. 오브제 = 그 소리의 모양.

    min_half 는 무음 구간에서도 형상이 끊기지 않게 하는 최소 두께
    (검출 모폴로지 open 커널보다 두꺼워야 함). smooth 는 핀치 방지용 이동평균.
    """
    a = np.abs(np.asarray(audio, dtype=np.float64))
    seg = max(len(a) // steps, 1)
    env = np.array([a[i * seg:(i + 1) * seg].max() for i in range(steps)])
    k = np.ones(smooth) / smooth
    env = np.convolve(env, k, mode="same")
    env /= env.max() or 1.0
    half = min_half + env * (max_half - min_half)
    xs = np.linspace(-length / 2, length / 2, steps)
    top = np.stack([xs, -half], axis=1)
    bot = np.stack([xs[::-1], half[::-1]], axis=1)
    poly = np.concatenate([top, bot])
    # 질량중심을 원점으로 — 비대칭 형상도 '잡은 곳'과 검출 중심이 일치하게
    return poly - _poly_centroid(poly)


def _poly_centroid(pts):
    """다각형 면적 무게중심 (shoelace)."""
    x, y = pts[:, 0], pts[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    area2 = cross.sum()
    if abs(area2) < 1e-9:
        return pts.mean(axis=0)
    cx = ((x + xn) * cross).sum() / (3.0 * area2)
    cy = ((y + yn) * cross).sum() / (3.0 * area2)
    return np.array([cx, cy])


def bank_shapes(bank, **kw):
    """FragmentBank -> 프래그먼트별 형상 폴리곤 리스트 (조각 16개 = 모양 16개)."""
    return [waveform_poly_from_audio(f, **kw) for f in bank.frags]


def render(objs, size=SIZE, bg=25, fg=235, polys=None):
    """objs: [(cx, cy, angle_deg)] 또는 [(cx, cy, angle_deg, scale)] -> 그레이 프레임.

    polys: 오브제별 폴리곤 리스트 (없으면 기본 웨이브 스트립).
    """
    w, h = size
    frame = np.full((h, w), bg, np.uint8)
    default = waveform_poly()
    for i, o in enumerate(objs):
        cx, cy, ang = o[0], o[1], o[2]
        scale = o[3] if len(o) > 3 else 1.0
        base = polys[i] if polys is not None and i < len(polys) else default
        r = math.radians(ang)
        # 픽셀 좌표계 회전 (vision.detect 의 각도 규약과 동일)
        rot = np.array([[math.cos(r), -math.sin(r)],
                        [math.sin(r), math.cos(r)]])
        pts = (base * scale) @ rot.T + [cx, cy]
        cv2.fillPoly(frame, [pts.astype(np.int32)], fg)
    return frame


def scenario(t: float):
    """데모 타임라인 (0~20s 루프). 반환: [(cx, cy, angle)]"""
    t = t % 20.0
    # 기준 행: 간격(140) > 오브제 길이(120) — 정렬 상태에서 서로 닿지 않아야 함
    row = [(110 + i * 140, 240, 0.0) for i in range(4)]

    if t < 4.0:                                                   # 1) 정렬 유지
        return row
    if t < 8.0:                                                   # 2) 회전·산개
        k = (t - 4.0) / 4.0
        return [(110 + i * 140 + 40 * k * math.sin(i * 2.1),
                 240 + 70 * k * math.cos(i * 1.7),
                 70.0 * k * math.sin(i * 1.3 + 1.0)) for i in range(4)]
    if t < 12.0:                                                  # 3) 두 개가 다가와 접촉
        k = min(1.0, (t - 8.0) / 3.0)
        gap = 130 * (1 - k) + 4
        return [(320 - gap / 2 - 60, 240, 0.0), (320 + gap / 2 + 60, 240, 0.0),
                (140, 120, 30.0), (500, 360, -45.0)]
    if t < 16.0:                                                  # 4) 두 개가 포개짐 (겹침)
        k = min(1.0, (t - 12.0) / 3.0)
        d = 130 * (1 - k)
        return [(320 - d, 240, 20.0), (320 + d, 240, -20.0),
                (140, 120, 30.0), (500, 360, -45.0)]
    k = (t - 16.0) / 4.0                                          # 5) 정렬 복귀
    cur = [(320 - 0, 240, 20.0), (320 + 0, 240, -20.0),
           (140, 120, 30.0), (500, 360, -45.0)]
    return [(c[0] + (r[0] - c[0]) * k, c[1] + (r[1] - c[1]) * k,
             c[2] + (r[2] - c[2]) * k) for c, r in zip(cur, row)]


def export_svg(path, bank=None, cols=4, cell=(170, 90), pad=20):
    """16개 형상을 4x4 시트 SVG로 내보낸다 — 실물 제작(레이저컷/CNC) 도면의 출발점."""
    from .granular import FragmentBank

    bank = bank or FragmentBank.synth()
    shapes = bank_shapes(bank)
    rows = (len(shapes) + cols - 1) // cols
    w, h = cols * cell[0] + pad * 2, rows * cell[1] + pad * 2
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
             f'width="{w}" height="{h}">',
             '<style>text{font:11px monospace;fill:#444}'
             'path{fill:none;stroke:#111;stroke-width:1.2}</style>']
    for i, poly in enumerate(shapes):
        cx = pad + (i % cols) * cell[0] + cell[0] / 2
        cy = pad + (i // cols) * cell[1] + cell[1] / 2
        d = "M " + " L ".join(f"{cx + x:.1f},{cy + y:.1f}" for x, y in poly) + " Z"
        parts.append(f'<path d="{d}"/>')
        parts.append(f'<text x="{cx - cell[0] / 2 + 6}" y="{cy - cell[1] / 2 + 14}">'
                     f'{bank.labels[i]}</text>')
    parts.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="프래그먼트 형상 도구")
    ap.add_argument("--export-svg", metavar="PATH", required=True,
                    help="16개 웨이브폼 형상을 SVG 시트로 저장")
    a = ap.parse_args()
    print("저장:", export_svg(a.export_svg))
