"""
단계별 브링업 진단 — 실카메라 + 실물 아크릴 오브제 도입 검증용.

run.py 는 전체 파이프라인을 한 번에 돌리므로 어디서 막혔는지 보기 어렵다.
이 도구는 능력을 '한 단계씩 쌓아 올리며' 각 단계가 통과해야 다음으로 넘어가는
게이트(gate) 방식으로 카메라/조명/오브제를 검증한다.

    1  인식    오브제가 몇 개로 잡히는가 (컨투어·중심·개수)
    2  거리    오브제 중심 간 거리 (px / 정규화), 최근접 쌍 강조
    3  기울기  각 오브제 장축 각도 + 방향 축선
    4  OSC     위 기하 정보를 Max/MSP 로 송출 (docs/bringup.maxpat 로 수신 확인)
    5  소리    최근접 거리 -> 사운드 변화 (내장 신스 --sound / OSC)

--stage N 은 1..N 을 '누적'해서 보여준다 (stage 3 이면 인식+거리+기울기 동시 표시).

실행 예 (자기 터미널에서 — 카메라 권한 필요!):
    .venv/bin/python -m tracker.bringup --stage 1 --camera 0 --expect 4
    .venv/bin/python -m tracker.bringup --stage 2 --camera 0
    .venv/bin/python -m tracker.bringup --stage 3 --camera 0
    .venv/bin/python -m tracker.bringup --stage 4 --camera 0 --osc 127.0.0.1:9000
    .venv/bin/python -m tracker.bringup --stage 5 --camera 0 --osc 127.0.0.1:9000 --sound

카메라 없이 도구 자체를 먼저 점검 (합성 씬):
    .venv/bin/python -m tracker.bringup --stage 5 --sound --seconds 30
"""

import argparse
import math
import os
import time

import cv2

from . import synthetic
from .run import load_cfg, open_source
from .scene import SceneTracker, features
from .sound import SoundMapper
from .vision import detect, find_contacts

STAGE_NAME = {1: "인식", 2: "거리", 3: "기울기", 4: "OSC", 5: "소리"}
# 오버레이용 ASCII 태그 (OpenCV putText 는 한글 미지원 — 콘솔만 한글)
STAGE_TAG = {1: "DETECT", 2: "DISTANCE", 3: "TILT", 4: "OSC", 5: "SOUND"}

# 단계 통과 후 다음으로 넘어가기 전 사람이 눈으로 확인할 체크포인트
GATE = {
    1: "오브제 수가 안정적으로 맞게 잡히는가? (--expect 로 기대 수 지정) "
       "깜빡임/뭉침 없으면 --stage 2 로.",
    2: "거리 선·수치가 오브제를 움직일 때 매끄럽게 따라오는가? 되면 --stage 3 으로.",
    3: "축선이 실제 기울기를 따라가는가? (오차 ±2~3도) 되면 --stage 4 로.",
    4: "Max 에서 /sr/... 가 들어오는가? (docs/bringup.maxpat) 되면 --stage 5 로.",
    5: "오브제를 붙였다 떼면 소리가 변하는가? 여기까지 되면 기본 루프 완성.",
}


# ── 기하 ─────────────────────────────────────────────────────────
def pairwise(objs):
    """[(id, Detection)] -> [(a, b, dist_px, (ax,ay), (bx,by))]  a<b id 순."""
    out = []
    for i in range(len(objs)):
        for j in range(i + 1, len(objs)):
            (ai, a), (bj, b) = objs[i], objs[j]
            lo, hi = (ai, a, b), (bj, b, a)
            if ai > bj:
                lo, hi = hi, lo
            d = math.hypot(a.cx - b.cx, a.cy - b.cy)
            out.append((lo[0], hi[0], d,
                        (lo[1].cx, lo[1].cy), (hi[1].cx, hi[1].cy)))
    return out


def nearest(pairs):
    return min(pairs, key=lambda p: p[2]) if pairs else None


# ── 오버레이 (단계 누적) ──────────────────────────────────────────
GREEN, CYAN, RED, YELLOW, WHITE = ((0, 220, 0), (255, 220, 0), (0, 0, 255),
                                   (0, 230, 230), (255, 255, 255))


def draw(frame, state, stage, diag, expect=None):
    vis = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()
    h, w = vis.shape[:2]
    objs = state.objects
    near = diag.get("near")

    # stage 2: 모든 쌍 거리선 (최근접은 빨강 강조)
    if stage >= 2:
        for a, b, d, pa, pb in diag.get("pairs", []):
            hot = near and (a, b) == (near[0], near[1])
            col = RED if hot else (90, 90, 90)
            cv2.line(vis, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])),
                     col, 2 if hot else 1, cv2.LINE_AA)
            if hot:
                mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
                cv2.putText(vis, f"{d:.0f}px / {d / math.hypot(w, h):.2f}",
                            (int(mx) + 6, int(my) - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, RED, 1, cv2.LINE_AA)

    # stage 1: 컨투어 + 중심 + id ; stage 3: 기울기 축선
    for lid, d in objs:
        cv2.drawContours(vis, [d.contour], -1, GREEN, 2)
        cv2.circle(vis, (int(d.cx), int(d.cy)), 3, GREEN, -1)
        label = f"#{lid}"
        frag = diag.get("labels", {}).get(lid)
        if stage >= 5 and frag:
            label += f" [{frag}]"               # 이 웨이브폼이 든 사운드 조각
        if stage >= 3:
            label += f" {d.angle:+.0f}d"
            r = math.radians(d.angle)
            ln = 0.5 * max(d.bbox[2], d.bbox[3]) + 14
            p1 = (int(d.cx - math.cos(r) * ln), int(d.cy - math.sin(r) * ln))
            p2 = (int(d.cx + math.cos(r) * ln), int(d.cy + math.sin(r) * ln))
            cv2.line(vis, p1, p2, CYAN, 2, cv2.LINE_AA)
        cv2.putText(vis, label, (int(d.cx) - 30, int(d.cy) - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 1, cv2.LINE_AA)

    # 상단 HUD — 단계별 핵심 수치
    n = len(objs)
    line1 = f"STAGE {stage} [{STAGE_TAG[stage]}]  objects={n}"
    if expect is not None:
        line1 += f"/{expect} " + ("OK" if n == expect else "X")
    cv2.putText(vis, line1, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                YELLOW if (expect is not None and n != expect) else WHITE,
                2, cv2.LINE_AA)
    line2 = ""
    if stage >= 2 and near:
        line2 += f"nearest {near[0]}-{near[1]}={near[2]:.0f}px ({near[3]:.2f}) "
    if stage >= 4:
        line2 += f"OSC->{diag.get('osc','off')} tx={diag.get('tx',0)} "
    if stage >= 5:
        p = diag.get("params", {})
        line2 += (f"scramble={diag.get('ctrl', 0.0):.2f} "
                  f"grain={p.get('grain_ms', 0):.0f}ms dens={p.get('density_hz', 0):.0f}Hz")
    if line2:
        cv2.putText(vis, line2, (10, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    WHITE, 1, cv2.LINE_AA)
    return vis


# ── OSC (브링업 전용 단순 주소계 /sr/...) ─────────────────────────
class BringupOsc:
    def __init__(self, host, port):
        from pythonosc.udp_client import SimpleUDPClient
        self.c = SimpleUDPClient(host, port)
        self.tx = 0

    def send(self, state, diag, w, h):
        diag_norm = math.hypot(w, h)
        labels = diag.get("labels", {})
        self.c.send_message("/sr/n", len(state.objects))
        for lid, d in state.objects:
            # id, cx, cy, angle, frag(조각 라벨 A0.. -> Max 에서 보이스 선택)
            self.c.send_message("/sr/obj", [int(lid), d.cx / w, d.cy / h,
                                            float(d.angle), labels.get(lid, "")])
        for a, b, dist, _, _ in diag.get("pairs", []):
            self.c.send_message("/sr/dist", [int(a), int(b),
                                             float(dist), dist / diag_norm])
        near = diag.get("near")
        if near:
            self.c.send_message("/sr/nearest",
                                [int(near[0]), int(near[1]), float(near[3])])
        # stage 5: 조합 사운드 파라미터 — Max 가 동일 조합을 만들 수 있도록
        for k, v in diag.get("params", {}).items():
            self.c.send_message(f"/sr/param/{k}", float(v))
        if "ctrl" in diag:
            self.c.send_message("/sr/sound", float(diag["ctrl"]))
        self.tx += 1


# stage 5 의 사운드 '조합'은 SoundMapper(관계특징->입자) + Arranger(오브제->프래그먼트
# 배정·좌우 순서·팬) + GranularEngine 으로 한다 — sandbox 와 동일 경로, 입력만 카메라.
# 4개 오브제 = 4개 사운드 조각(A0,B0,C0,D0). 정렬되면 온전한 루프, 흐트러지면 파편 콜라주.


def main():
    ap = argparse.ArgumentParser(description="Scramble Ritual 단계별 브링업")
    ap.add_argument("--stage", type=int, default=1, choices=[1, 2, 3, 4, 5])
    ap.add_argument("--camera", type=int, help="카메라 인덱스")
    ap.add_argument("--video", help="영상 파일 경로")
    ap.add_argument("--osc", metavar="HOST:PORT", help="예: 127.0.0.1:9000")
    ap.add_argument("--sound", action="store_true", help="stage 5 내장 신스 모니터(조합 사운드)")
    ap.add_argument("--wav", nargs="+", metavar="WAV",
                    help="소스 WAV 최대 4개 — 각 4조각으로 잘려 오브제에 배정 (없으면 합성음 4종)")
    ap.add_argument("--gain", type=float, default=0.5, help="내장 신스 출력 게인")
    ap.add_argument("--audio-device", type=int, metavar="N",
                    help="출력 오디오 장치 인덱스 (--list-audio 로 확인)")
    ap.add_argument("--list-audio", action="store_true", help="오디오 장치 목록 출력 후 종료")
    ap.add_argument("--expect", type=int, help="stage 1 기대 오브제 수 (게이트 체크)")
    ap.add_argument("--seconds", type=float, default=30.0, help="합성 씬 길이")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--no-show", action="store_true", help="창 없이 콘솔만")
    args = ap.parse_args()

    if args.list_audio:
        import sounddevice as sd
        print(sd.query_devices())
        print(f"\n현재 기본 출력 = {sd.default.device[1]}  "
              f"(--audio-device N 으로 변경)")
        return

    cfg = load_cfg()
    get_frame, src = open_source(args)
    tracker = SceneTracker(cfg.get("scene"))
    show = not args.no_show

    osc = None
    if args.osc and args.stage >= 4:
        host, port = args.osc.split(":")
        osc = BringupOsc(host, int(port))

    # stage 5: 조합 사운드 경로 — 관계특징->입자(SoundMapper) + 오브제->조각 배정(Arranger)
    mapper = arranger = bank = engine = None
    if args.stage >= 5:
        from .granular import Arranger, FragmentBank
        bank = FragmentBank.from_wavs(args.wav) if args.wav else FragmentBank.synth()
        arranger = Arranger(bank)
        mapper = SoundMapper(cfg.get("sound"))
        print(f"  [프래그먼트 뱅크: {len(bank.frags)}조각 "
              f"({bank.labels[0]}~{bank.labels[-1]}) — 오브제 좌->우 = 재생 순서]")
        if args.sound:
            from .granular import GranularEngine
            engine = GranularEngine(gain=args.gain, bank=bank)
            try:
                engine.start_stream(device=args.audio_device)
                print(f"  [내장 신스 ON → 출력장치: {engine.output_name()} | "
                      f"gain={args.gain}]  소리 안 나면: 시스템 볼륨/출력 확인, "
                      f"--list-audio 로 장치 점검")
                engine.push_event("touch", 0.5)   # 시작 테스트 클릭 — 라우팅 확인용
            except Exception as e:
                print(f"  [오디오 시작 실패: {e}] — --list-audio 로 장치 확인, "
                      f"--audio-device N 지정")
                engine = None

    print(f"[브링업 STAGE {args.stage} ({STAGE_NAME[args.stage]}) | 소스:{src} "
          f"| OSC:{args.osc if osc else '미사용'} | 소리:{'on' if engine else 'off'}]")
    print(f"  게이트 → {GATE[args.stage]}")
    if args.camera is None and not args.video:
        print("  (카메라 미지정 — 합성 씬으로 도구 점검 중. 실검증은 --camera 0)")
    print("  창에서 ESC 종료\n")

    dt = 1.0 / args.fps
    t0 = time.monotonic()
    last_log = -1.0
    n_frames = 0
    try:
        while True:
            t = time.monotonic() - t0
            live = args.camera is not None or args.video
            if not live and t >= args.seconds:
                break
            frame = get_frame(t)
            if frame is None:
                break
            h, w = frame.shape[:2]
            mask, dets = detect(frame, cfg.get("vision"))
            state = tracker.update(dets, find_contacts(dets, cfg.get("vision")))

            diag = {}
            if args.stage >= 2:
                diag["pairs"] = pairwise(state.objects)
                nr = nearest(diag["pairs"])
                if nr:                       # (a, b, dist_px, dist_norm)
                    diag["near"] = (nr[0], nr[1], nr[2], nr[2] / math.hypot(w, h))
            if args.stage >= 5:
                feat = features(state, (w, h))
                params = mapper.map(feat)               # 관계특징 -> 입자 파라미터
                order, pans = arranger.update(state, w)  # 오브제 좌->우 = 재생 순서
                diag["params"] = params
                diag["ctrl"] = params["scramble"]
                diag["labels"] = {lid: arranger.label(lid)
                                  for lid, _ in state.objects}
                if engine:
                    engine.set_params(params)
                    engine.set_arrangement(order, pans)  # 4 조각을 위치대로 조합
                    for ev, data in state.events:
                        if ev in ("touch", "merge"):     # 맞닿음/겹침 -> 트랜지언트
                            pt = data.get("point")
                            engine.push_event(ev, pt[0] / w if pt else 0.5)
            if osc:
                osc.send(state, diag, w, h)
                diag["osc"], diag["tx"] = args.osc, osc.tx

            n_frames += 1
            if t - last_log >= 1.0:
                last_log = t
                msg = f"[{t:5.1f}s] n={len(state.objects)}"
                if args.expect is not None:
                    msg += f"/{args.expect}" + ("ok" if len(state.objects) == args.expect else " <-X")
                nr = diag.get("near")
                if nr:
                    msg += f" nearest {nr[0]}-{nr[1]}={nr[2]:.0f}px({nr[3]:.2f})"
                if "ctrl" in diag:
                    seq = " ".join(diag.get("labels", {}).get(l, "?")
                                   for l, _ in sorted(state.objects, key=lambda o: o[1].cx))
                    msg += f" scramble={diag['ctrl']:.2f} seq=[{seq}]"
                if osc:
                    msg += f" osc_tx={osc.tx}"
                print(msg)

            if show:
                cv2.imshow(f"bringup-stage{args.stage}", draw(frame, state,
                           args.stage, diag, args.expect))
                if cv2.waitKey(1) & 0xFF == 27:
                    break
            time.sleep(max(0.0, dt - ((time.monotonic() - t0) - t)))
    finally:
        if engine:
            engine.stop_stream()
        cv2.destroyAllWindows()
    print(f"\n완료: {n_frames}프레임 ({n_frames / max(t, 1e-9):.1f}fps)")
    print(f"다음 단계 판단 → {GATE[args.stage]}")


if __name__ == "__main__":
    main()
