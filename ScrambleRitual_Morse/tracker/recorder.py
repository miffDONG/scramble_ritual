"""
세션 녹화·리플레이 — 조작 세션을 JSONL 로 기록하고, 트래커 없이 재생한다.

용도
1. 매핑 튜닝: 같은 조작을 반복 재생하며 config 가중치 버전을 귀로 비교
2. 사운드 디자이너 핸드오프: 녹화본만으로 Max/MSP 패치 작업 가능 (--osc 재생)

기록 형식 (한 줄 = 한 프레임):
    {"t": 1.23, "params": {...}, "objects": [[id, cx, cy, angle], ...],
     "contacts": [[a, b, x, y], ...], "events": [["touch", {...}], ...]}

녹화:  sandbox/run 에 --record session.jsonl 플래그
재생:  .venv/bin/python -m tracker.recorder session.jsonl            # 내장 신스
       .venv/bin/python -m tracker.recorder session.jsonl --osc 127.0.0.1:9000 --mute
       옵션: --loop, --speed 1.5, --wav source.wav
"""

import argparse
import json
import time
from types import SimpleNamespace


class Recorder:
    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")
        self.n = 0

    def write(self, t, params, state):
        row = {
            "t": round(t, 4),
            "params": {k: round(float(v), 5) for k, v in params.items()},
            "objects": [[lid, round(d.cx, 1), round(d.cy, 1), round(d.angle, 1)]
                        for lid, d in state.objects],
            "contacts": [[a, b, round(px, 1), round(py, 1)]
                         for a, b, (px, py) in state.contacts],
            "events": [[ev, _jsonable(data)] for ev, data in state.events],
        }
        self.f.write(json.dumps(row) + "\n")
        self.n += 1

    def close(self):
        self.f.close()
        return self.n


def _jsonable(d):
    return {k: (list(v) if isinstance(v, (tuple, set, frozenset)) else v)
            for k, v in d.items()}


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def to_state(row):
    """기록 행 -> OscSender 가 기대하는 형태의 유사 SceneState."""
    return SimpleNamespace(
        objects=[(lid, SimpleNamespace(cx=cx, cy=cy, angle=ang))
                 for lid, cx, cy, ang in row["objects"]],
        contacts=[(a, b, (x, y)) for a, b, x, y in row["contacts"]],
        events=[(ev, data) for ev, data in row["events"]],
    )


def replay(rows, engine=None, osc=None, frame_size=(640, 480),
           speed=1.0, on_frame=None, arranger=None):
    """기록을 타이밍 그대로 재생. engine/osc/arranger 는 선택."""
    t0 = time.monotonic()
    for row in rows:
        target = row["t"] / speed
        wait = target - (time.monotonic() - t0)
        if wait > 0:
            time.sleep(wait)
        state = to_state(row)
        if engine:
            engine.set_params(row["params"])
            if arranger:
                order, pans = arranger.update(state, frame_size[0])
                engine.set_arrangement(order, pans)
            for ev, data in state.events:
                if ev in ("touch", "merge"):
                    pt = data.get("point")
                    engine.push_event(ev, pt[0] / frame_size[0] if pt else 0.5)
        if osc:
            osc.send(row["params"], state, frame_size)
        if on_frame:
            on_frame(row)


def main():
    ap = argparse.ArgumentParser(description="세션 리플레이")
    ap.add_argument("session", help="녹화 파일 (.jsonl)")
    ap.add_argument("--osc", metavar="HOST:PORT")
    ap.add_argument("--mute", action="store_true", help="내장 신스 끄기")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--wav", nargs="+", metavar="WAV", help="소스 WAV 최대 4개")
    args = ap.parse_args()

    rows = load(args.session)
    if not rows:
        raise SystemExit("빈 녹화 파일")
    dur = rows[-1]["t"]
    print(f"[{args.session}: {len(rows)}프레임 / {dur:.1f}s]")

    from .sound import OscSender
    osc = OscSender(*((args.osc.split(":")[0], int(args.osc.split(":")[1]))
                      if args.osc else (None,)))
    engine = None
    arranger = None
    if not args.mute:
        from .granular import Arranger, FragmentBank, GranularEngine
        bank = FragmentBank.from_wavs(args.wav) if args.wav else FragmentBank.synth()
        engine = GranularEngine(bank=bank)
        arranger = Arranger(bank)
        engine.start_stream()

    def log(row):
        p = row["params"]
        print(f"\r[{row['t']:6.1f}s] x={p['scramble']:.2f} "
              f"grain={p['grain_ms']:.0f}ms n={int(p.get('n', 0))} ", end="")

    try:
        while True:
            replay(rows, engine, osc, speed=args.speed, on_frame=log,
                   arranger=arranger)
            print()
            if not args.loop:
                break
    except KeyboardInterrupt:
        print("\n중단")
    finally:
        if engine:
            engine.stop_stream()


if __name__ == "__main__":
    main()
