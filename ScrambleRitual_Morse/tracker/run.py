"""
Scramble Ritual — 트래커 메인 루프

합성 데모 (카메라 불필요):
    .venv/bin/python -m tracker.run --seconds 20
실카메라:
    .venv/bin/python -m tracker.run --camera 0 --show
영상 파일:
    .venv/bin/python -m tracker.run --video capture.mov --show
Max/MSP 송출:
    .venv/bin/python -m tracker.run --camera 0 --osc 127.0.0.1:9000

--show : 검출 오버레이 창 (컨투어·ID·각도·접점·겹침 그룹·파라미터 HUD)
"""

import argparse
import json
import os
import time

# .camera must be imported before cv2 (it sets an OpenCV videoio env switch
# that is only read while the library loads) — see tracker/camera.py.
from .camera import open_camera

import cv2

from . import synthetic
from .scene import SceneTracker, features
from .sound import OscSender, SoundMapper
from .vision import detect, find_contacts

CFG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load_cfg():
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def open_source(args):
    """프레임 제너레이터와 소스 설명을 돌려준다."""
    if args.camera is not None:
        try:
            cap, backend = open_camera(args.camera)
            return lambda t: cap.read()[1], f"camera:{args.camera}({backend})"
        except RuntimeError as exc:
            # 카메라 미인증/부재/프레임 없음 — 죽지 말고 합성 씬으로 폴백
            # (preview 서버가 살아 있도록).
            print(f"[경고] {exc} — 합성 씬으로 폴백")
    if args.video:
        cap = cv2.VideoCapture(args.video)
        if not cap.isOpened():
            raise SystemExit(f"영상 파일을 열 수 없음: {args.video}")
        return lambda t: cap.read()[1], f"video:{args.video}"
    return lambda t: synthetic.render(synthetic.scenario(t)), "synthetic"


def draw_overlay(frame, state, params, labels=None):
    vis = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()
    merged_ids = {i for g in state.overlaps for i in g}
    for lid, d in state.objects:
        color = (255, 0, 255) if lid in merged_ids else (0, 220, 0)
        cv2.drawContours(vis, [d.contour], -1, color, 2)
        tag = f" {labels[lid]}" if labels and labels.get(lid) else ""
        cv2.putText(vis, f"#{lid}{tag} {d.angle:+.0f}d", (int(d.cx) - 34, int(d.cy) - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    for a, b, (px, py) in state.contacts:
        cv2.circle(vis, (int(px), int(py)), 7, (0, 0, 255), 2)
        cv2.putText(vis, f"{a}-{b}", (int(px) + 8, int(py) - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)
    hud = (f"x={params['scramble']:.2f} grain={params['grain_ms']:.0f}ms "
           f"dens={params['density_hz']:.0f}Hz n={int(params['n'])}")
    cv2.putText(vis, hud, (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                (255, 255, 255), 1, cv2.LINE_AA)
    return vis


def main():
    ap = argparse.ArgumentParser(description="Scramble Ritual tracker")
    ap.add_argument("--camera", type=int, help="카메라 인덱스")
    ap.add_argument("--video", help="영상 파일 경로")
    ap.add_argument("--osc", metavar="HOST:PORT", help="예: 127.0.0.1:9000")
    ap.add_argument("--seconds", type=float, default=20.0, help="합성 데모 길이")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--show", action="store_true", help="오버레이 창 표시")
    ap.add_argument("--snapshot", metavar="DIR", help="5초마다 오버레이 PNG 저장")
    ap.add_argument("--record", metavar="PATH", help="세션을 JSONL로 녹화 (tracker.recorder 로 재생)")
    args = ap.parse_args()

    recorder = None
    if args.record:
        from .recorder import Recorder
        recorder = Recorder(args.record)

    cfg = load_cfg()
    get_frame, src = open_source(args)
    tracker = SceneTracker(cfg.get("scene"))
    mapper = SoundMapper(cfg.get("sound"))
    osc = OscSender(*((args.osc.split(":")[0], int(args.osc.split(":")[1]))
                      if args.osc else (None,)))
    print(f"[소스: {src} / OSC: {args.osc or '미사용(콘솔 출력)'}]")

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
            mask, dets = detect(frame, cfg.get("vision"))
            state = tracker.update(dets, find_contacts(dets, cfg.get("vision")))
            feat = features(state, (frame.shape[1], frame.shape[0]))
            params = mapper.map(feat)
            osc.send(params, state, (frame.shape[1], frame.shape[0]))
            if recorder:
                recorder.write(t, params, state)
            n_frames += 1

            for ev, data in state.events:
                print(f"  [{t:5.1f}s] {ev:7s} {data}")
            if t - last_log >= 1.0:
                last_log = t
                print(f"[{t:5.1f}s] n={feat['n']} disorder={feat['disorder']:.2f} "
                      f"scatter={feat['scatter']:.2f} chain={feat['chain']:.2f} "
                      f"overlap={feat['overlap_ratio']:.2f} -> "
                      f"x={params['scramble']:.2f} grain={params['grain_ms']:.0f}ms")
            if args.show or args.snapshot:
                vis = draw_overlay(frame, state, params)
                if args.show:
                    cv2.imshow("scramble-tracker", vis)
                    if cv2.waitKey(1) & 0xFF == 27:      # ESC
                        break
                if args.snapshot and int(t) % 5 == 0 and abs(t - round(t)) < dt:
                    os.makedirs(args.snapshot, exist_ok=True)
                    cv2.imwrite(os.path.join(args.snapshot, f"t{int(t):03d}.png"), vis)
            time.sleep(max(0.0, dt - ((time.monotonic() - t0) - t)))
    finally:
        if recorder:
            print(f"[녹화 저장: {args.record} ({recorder.close()}프레임)]")
        cv2.destroyAllWindows()
    print(f"\n완료: {n_frames}프레임 처리 ({n_frames / max(t, 1e-9):.1f}fps)")


if __name__ == "__main__":
    main()
