"""
가상 전시 시뮬레이션 — 라이트박스·실물 없이 설치 설계 전체를 검증한다.

'가상 관람 세션' 타임라인(약 58초)을 백라이트 실루엣으로 렌더링해
설계 문서(docs/installation-design.md)의 핵심 주장을 자동 판정한다:

    V1 형상 ID 정확도      16형상 x 3회전 자기식별
    V2 손 배제            미등록 실루엣(손)이 트랙을 만들지 않음
    V3 재배치 정체성       오브제를 치웠다 다시 놓아도 같은 조각
    V4 상태기계           IDLE→ACTIVE→IDLE→ACTIVE 전환 타이밍
    V5 처리 지연           프레임당 검출+매칭+추적 ms (예산 10ms)
    V6 음악 반응           정렬↔해체에 따른 scramble·펄스 모핑

시나리오: 빈 판 → 4개 배치(A0 B0 C0 D0) → 좌우 교환(재편곡) → 기울임·산개
→ 손 스윕(무시돼야 함) → 포개기(merge) → C0 제거 후 재배치(같은 소리 복원)
→ 8초 정지(IDLE 진입) → 재개(ACTIVE) → 정렬 복귀.

실행:
    .venv/bin/python -m tracker.simulate
    .venv/bin/python -m tracker.simulate --montage out.png   # 8컷 오버레이 시트
"""

import argparse
import math
import time

import cv2
import numpy as np

from . import synthetic
from .granular import FragmentBank
from .identity import IdentityTracker, ShapeIdentifier
from .scene import SceneTracker, features
from .sound import SoundMapper
from .vision import detect, find_contacts

W, H = synthetic.SIZE
ROW_X = [110, 250, 390, 530]
FRAGS = [0, 4, 8, 12]                     # A0 B0 C0 D0

# 백라이트 = 고정 임계값. Otsu 는 빈 판(단일 계조)에서 노이즈를 반으로 갈라
# 유령 블롭을 만든다 — 시뮬레이션 v1 에서 실측 확인된 설계 결함.
VCFG = {"invert": True, "thresh": 150, "min_area_px": 300,
        "max_area_frac": 0.6, "max_objects": 0}


def smooth(k):
    k = max(0.0, min(1.0, k))
    return k * k * (3 - 2 * k)


def hand_poly():
    """미등록 실루엣 — 손바닥+손가락 비슷한 블롭."""
    th = np.linspace(0, 2 * math.pi, 40, endpoint=False)
    r = 42 + 10 * np.sin(3 * th) + 6 * np.cos(7 * th)
    return np.stack([r * np.cos(th) * 1.3, r * np.sin(th)], axis=1)


def scene_at(t):
    """t -> ([(frag, x, y, ang)], hand(x,y)|None)  가상 관람 타임라인."""
    objs = []
    # A0: 배치 4s, 8~14s 좌->우 교환 — 위쪽 호를 그리며 (관통 방지, 실물 동선)
    if t >= 4:
        k = smooth((t - 8) / 6)
        xa = 110 + (530 - 110) * k
        ya = 240 - 90 * math.sin(math.pi * k)
        objs.append((0, xa, ya, 0.0))
    # B0: 배치 5s, 14~20s 기울임 65도, 24~30s D0 쪽으로 접근·복귀, 47s~ 정렬 복귀
    if t >= 5:
        ang = 65 * smooth((t - 14) / 6)
        xb = 250
        if 24 <= t < 27:
            xb = 250 - (250 - 130) * smooth((t - 24) / 3)
        elif 27 <= t < 28.5:
            xb = 130
        elif 28.5 <= t < 30:
            xb = 130 + (250 - 130) * smooth((t - 28.5) / 1.5)
        if t >= 47:
            k = smooth((t - 47) / 6)
            xb = 250 + (390 - 250) * k
            ang = 65 * (1 - k)
        objs.append((4, xb, 240, ang))
    # C0: 배치 6s, 14~20s 산개(아래로), 30s 제거, 33s 재배치(회전), 47s~ 정렬
    if 6 <= t < 30:
        k = smooth((t - 14) / 6)
        objs.append((8, 390 + 20 * k, 240 + 90 * k, 0.0))
    elif t >= 33:
        x, y, a = 150, 120, 40.0
        if t >= 47:
            k = smooth((t - 47) / 6)
            x, y, a = 150 + (250 - 150) * k, 120 + (240 - 120) * k, 40 * (1 - k)
        objs.append((8, x, y, a))
    # D0: 배치 7s, 8~14s 우->좌 교환 — 아래쪽 호, 44~46s 살짝 이동(재개 신호)
    if t >= 7:
        k = smooth((t - 8) / 6)
        xd = 530 - (530 - 110) * k
        yd = 240 + 90 * math.sin(math.pi * k) - 40 * smooth((t - 44) / 2)
        if t >= 47:
            yd = 200 + (240 - 200) * smooth((t - 47) / 6)
        objs.append((12, xd, yd, 0.0))
    hand = None
    if 20 <= t < 24:
        hand = (80 + (t - 20) / 4 * 480, 330)
    return objs, hand


class StateMachine:
    """IDLE(숨쉬기) <-> ACTIVE(반응). 이동 에너지 기반."""

    def __init__(self, idle_timeout=6.0, motion_px=1.5):
        self.idle_timeout = idle_timeout
        self.motion_px = motion_px
        self.state = "IDLE"
        self._prev = {}
        self._last_motion = -1e9
        self.transitions = []          # [(t, state)]

    def update(self, t, tracks, n_events):
        energy = sum(math.hypot(tr.cx - self._prev[i][0], tr.cy - self._prev[i][1])
                     for i, tr in tracks.items() if i in self._prev)
        self._prev = {i: (tr.cx, tr.cy) for i, tr in tracks.items()}
        if energy > self.motion_px or n_events:
            self._last_motion = t
        want = "ACTIVE" if t - self._last_motion < self.idle_timeout else "IDLE"
        if want != self.state:
            self.state = want
            self.transitions.append((round(t, 1), want))
        return self.state


def run(args):
    bank = FragmentBank.synth()
    shapes = synthetic.bank_shapes(bank)
    ident = ShapeIdentifier.from_bank(bank)
    assign = IdentityTracker(confirm=3)
    tracker = SceneTracker()
    mapper = SoundMapper()
    sm = StateMachine(idle_timeout=args.idle_timeout)
    rng = np.random.default_rng(7)
    hand = hand_poly()

    # V1: 등록 자기식별 (16형상 x 3회전)
    v1_ok = v1_n = 0
    for i in range(16):
        for ang in (0, 50, -70):
            fr = synthetic.render([(320, 240, ang)], polys=[shapes[i]])
            _, ds = detect(fr, {"max_objects": 0})
            if len(ds) == 1:
                v1_n += 1
                v1_ok += (ident.match(ds[0])[0] == i)

    dt = 1.0 / args.fps
    n_frames = int(args.seconds * args.fps)
    log = []                          # (t, n, scramble, pulse, state, ms)
    events_all = []                   # (t, type, data)
    confirms = []                     # (t, lid, frag)
    rejects_hand_phase = 0
    montage_t = [6, 12, 18, 22, 27.5, 34.5, 40, 50]
    montage = []

    for fi_ in range(n_frames):
        t = fi_ * dt
        objs, hd = scene_at(t)
        polys = [shapes[f] for f, *_ in objs]
        draw = [(x, y, a) for _, x, y, a in objs]
        if hd:
            draw.append((hd[0], hd[1], 15.0))
            polys.append(hand)
        frame = synthetic.render(draw, polys=polys)
        backlit = cv2.bitwise_not(frame)
        noisy = np.clip(backlit.astype(np.int16)
                        + rng.normal(0, 5, backlit.shape).astype(np.int16),
                        0, 255).astype(np.uint8)

        p0 = time.perf_counter()
        _, dets = detect(noisy, VCFG)
        # 정체성 필터: 등록 형상 일치 or 기존 트랙 중심 포함 -> 유지, 아니면 배제
        kept, matches = [], {}
        for d in dets:
            fr_idx, _ = ident.match(d)
            keep = fr_idx is not None
            if not keep:
                for tr in tracker.tracks.values():
                    if cv2.pointPolygonTest(d.contour, (tr.cx, tr.cy), True) >= -10:
                        keep = True
                        break
            if keep:
                matches[id(d)] = fr_idx
                kept.append(d)
            elif 20 <= t < 24.5:
                rejects_hand_phase += 1
        state = tracker.update(kept, find_contacts(kept, VCFG))
        ms = (time.perf_counter() - p0) * 1000.0

        prev_assigned = dict(assign.assigned)
        for lid, d in state.objects:
            assign.vote(lid, matches.get(id(d)))
        assign.prune(set(tracker.tracks))
        for lid, fr_idx in assign.assigned.items():
            if lid not in prev_assigned:
                confirms.append((round(t, 2), lid, fr_idx))

        feat = features(state, (W, H))
        params = mapper.map(feat)
        pulse = 319.0 - (319.0 - 135.0) * params["scramble"]
        st = sm.update(t, tracker.tracks, len(state.events))
        for ev, data in state.events:
            events_all.append((round(t, 2), ev, data))
        log.append((t, feat["n"], params["scramble"], pulse, st, ms))

        if montage_t and t >= montage_t[0]:
            montage_t.pop(0)
            vis = cv2.cvtColor(noisy, cv2.COLOR_GRAY2BGR)
            for lid, d in state.objects:
                fr_idx = assign.frag_of(lid)
                lab = bank.labels[fr_idx] if fr_idx is not None else "?"
                cv2.drawContours(vis, [d.contour], -1, (0, 200, 0), 2)
                cv2.putText(vis, f"{lab}", (int(d.cx) - 12, int(d.cy) - 16),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            cv2.putText(vis, f"t={t:4.1f}s {st} x={params['scramble']:.2f} "
                        f"pulse={pulse:.0f}ms", (10, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 80, 0), 2)
            montage.append(vis)

    # ── 판정 ─────────────────────────────────────────────────────
    def phase(a, b, col):
        vals = [r[col] for r in log if a <= r[0] < b]
        return sum(vals) / max(len(vals), 1)

    enters_hand = [e for e in events_all if e[1] == "enter" and 20 <= e[0] < 24.5]
    v2 = (len(enters_hand) == 0) and rejects_hand_phase > 0

    c0_confirms = [(t, lid) for t, lid, fr in confirms if fr == 8]
    v3 = (len(c0_confirms) >= 2 and c0_confirms[-1][0] > 33
          and c0_confirms[-1][1] != c0_confirms[0][1])

    tr = sm.transitions
    # 마지막 동작 = C0 재배치(33s) → 33+idle_timeout(6) ≈ 39s 에 IDLE,
    # 44s D0 이동으로 재개 — 설계값 90s 를 6s 로 축약한 타이밍 검증
    v4 = (len(tr) >= 3 and tr[0][1] == "ACTIVE" and 4 <= tr[0][0] <= 6
          and tr[1][1] == "IDLE" and 38 <= tr[1][0] <= 42
          and tr[2][1] == "ACTIVE" and 44 <= tr[2][0] <= 46.5)

    ms_all = sorted(r[5] for r in log)
    ms_mean = sum(ms_all) / len(ms_all)
    ms_p95 = ms_all[int(len(ms_all) * 0.95)]
    v5 = ms_p95 < 10.0

    x_aligned, x_tilt = phase(10, 13, 2), phase(18, 20, 2)
    x_stack, x_final = phase(27, 28.5, 2), phase(56, 58, 2)
    v6 = x_aligned < 0.15 and x_tilt > 0.35 and x_stack > x_aligned and x_final < 0.2

    print("\n══ 가상 전시 시뮬레이션 결과 ══")
    print(f"프레임 {len(log)}개 ({args.seconds:.0f}s @ {args.fps:.0f}fps 가상)")
    print(f"\nV1 형상 ID 정확도    {'PASS' if v1_ok == v1_n else 'FAIL'}  {v1_ok}/{v1_n}")
    print(f"V2 손 배제           {'PASS' if v2 else 'FAIL'}  "
          f"배제 {rejects_hand_phase}회, 유령 트랙 {len(enters_hand)}개")
    print(f"V3 재배치 정체성      {'PASS' if v3 else 'FAIL'}  "
          f"C0 확정 이력 {c0_confirms}")
    print(f"V4 상태기계          {'PASS' if v4 else 'FAIL'}  전환 {tr}")
    print(f"V5 처리 지연         {'PASS' if v5 else 'FAIL'}  "
          f"평균 {ms_mean:.1f}ms / p95 {ms_p95:.1f}ms (예산 10ms)")
    print(f"V6 음악 반응         {'PASS' if v6 else 'FAIL'}  "
          f"정렬 {x_aligned:.2f} → 기울임·산개 {x_tilt:.2f} → 포개기 {x_stack:.2f} "
          f"→ 복귀 {x_final:.2f}")
    print(f"   펄스 모핑: {319 - 184 * x_aligned:.0f}ms(정렬) ↔ "
          f"{319 - 184 * x_tilt:.0f}ms(해체)")
    print("\n주요 이벤트:")
    for t, ev, data in events_all:
        if ev in ("merge", "split") or (ev in ("enter", "leave") and t > 28):
            print(f"  [{t:5.1f}s] {ev:6s} {data}")
    print(f"확정 배정: {[(t, f'#{lid}', bank.labels[fr]) for t, lid, fr in confirms]}")

    if args.montage and montage:
        rows = [np.hstack(montage[i:i + 4]) for i in (0, 4) if montage[i:i + 4]]
        while len(rows) > 1 and rows[-1].shape[1] != rows[0].shape[1]:
            pad = np.zeros((rows[-1].shape[0],
                            rows[0].shape[1] - rows[-1].shape[1], 3), np.uint8)
            rows[-1] = np.hstack([rows[-1], pad])
        sheet = np.vstack(rows)
        sheet = cv2.resize(sheet, None, fx=0.55, fy=0.55)
        cv2.imwrite(args.montage, sheet)
        print(f"\n[몽타주 저장: {args.montage}]")

    return all([v1_ok == v1_n, v2, v3, v4, v5, v6])


def main():
    ap = argparse.ArgumentParser(description="가상 전시 시뮬레이션")
    ap.add_argument("--seconds", type=float, default=58.0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--idle-timeout", type=float, default=6.0,
                    help="정지 후 IDLE 진입 시간 (전시 설계값 90s 의 축약)")
    ap.add_argument("--montage", metavar="PNG", help="8컷 오버레이 시트 저장 경로")
    args = ap.parse_args()
    ok = run(args)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
