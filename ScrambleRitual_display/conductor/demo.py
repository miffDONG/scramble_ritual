"""
Scramble Ritual — 데모 러너

시뮬레이션 (하드웨어 불필요):
    python3 -m conductor.demo --seconds 6
실보드 연결:
    python3 -m conductor.demo --port /dev/cu.usbserial-XXXX
유틸리티:
    python3 -m conductor.demo --port ... --test 4     # 내장 테스트 패턴 (매핑 확인)
    python3 -m conductor.demo --port ... --all-off    # 비상 전체 OFF

데모 시나리오: 정렬(0) -> 흐트러짐(1) 램프 -> 유지 -> 정렬 복귀.
실제 설치에서는 scripted_scramble() 자리에 슬라이드 트래커 입력이 들어간다.
"""

import argparse
import json
import os
import time

from .engine import Engine
from .link import MockLink, SerialLink
from .protocol import GRID

CFG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load_cfg():
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def scripted_scramble(t: float, total: float) -> float:
    """데모용 입력 곡선: 1/4 정렬 -> 램프업 -> 1/4 유지 -> 램프다운."""
    q = total / 4.0
    if t < q:
        return 0.0
    if t < 2 * q:
        return (t - q) / q
    if t < 3 * q:
        return 1.0
    return max(0.0, 1.0 - (t - 3 * q) / q)


def render(frame, x, on, flips, ansi=True):
    rows = []
    for r in range(GRID - 1, -1, -1):              # 위가 11행, 아래가 0행
        rows.append(" ".join("■" if frame[r * GRID + c] else "·" for c in range(GRID)))
    bar = "#" * int(x * 20)
    head = f"scramble x={x:4.2f} [{bar:<20}]  ON={on:3d}/144  flips={flips}"
    out = head + "\n" + "\n".join(rows)
    if ansi:
        print("\x1b[H\x1b[2J" + out, flush=True)
    else:
        print(out + "\n", flush=True)


def run_show(link, cfg, seconds, fps, ansi, quiet):
    eng = Engine(cfg.get("engine"))
    dt = 1.0 / fps
    t0 = time.monotonic()
    n_sent = n_fail = 0
    last_frame = eng.frame
    while True:
        t = time.monotonic() - t0
        if t >= seconds:
            break
        target = scripted_scramble(t, seconds)
        eng.update(target, dt)
        last_frame = eng.generate(t)
        a = link.send_frame(last_frame)
        n_sent += 1
        if not a.ok:
            n_fail += 1
        if a.flags & 1:
            print("[경고] 보드 워치독 안전정지가 있었음 — 호스트 전송 주기 점검")
        if not quiet:
            render(last_frame, eng.x, eng.on_count(), eng.flips_total, ansi)
        time.sleep(max(0.0, dt - ((time.monotonic() - t0) - t)))
    link.all_off()
    print(f"\n완료: {n_sent}프레임 전송, 실패 {n_fail}, 총 토글 {eng.flips_total}회 "
          f"(평균 {eng.flips_total / max(seconds, 1e-9):.1f}회/초)")
    return last_frame, eng


def main():
    ap = argparse.ArgumentParser(description="Scramble Ritual demo runner")
    ap.add_argument("--port", help="시리얼 포트 (없으면 MockLink 시뮬레이션)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--fps", type=float, default=20.0)
    ap.add_argument("--no-ansi", action="store_true", help="화면 클리어 없이 출력")
    ap.add_argument("--quiet", action="store_true", help="프레임 렌더링 생략")
    ap.add_argument("--test", type=int, metavar="N", help="내장 테스트 패턴 0~4 실행")
    ap.add_argument("--all-off", action="store_true", help="전체 OFF 후 종료")
    args = ap.parse_args()

    cfg = load_cfg()
    link = SerialLink(args.port, args.baud) if args.port else MockLink()
    if not args.port:
        print("[시뮬레이션 모드 — MockLink. 실보드는 --port 지정]")

    if args.all_off:
        a = link.all_off()
        print("ALL OFF:", "ok" if a.ok else f"실패(err={a.err})")
        return
    if args.test is not None:
        a = link.test(args.test)
        print(f"테스트 패턴 {args.test}:", "ok" if a.ok else f"실패(err={a.err})")
        print("보드가 자체 구동 중. 종료하려면 --all-off 실행.")
        return

    run_show(link, cfg, args.seconds, args.fps, ansi=not args.no_ansi, quiet=args.quiet)


if __name__ == "__main__":
    main()
