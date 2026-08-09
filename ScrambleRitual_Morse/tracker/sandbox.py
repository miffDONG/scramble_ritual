"""
마우스 샌드박스 — 피지컬 오브제 없이 '조작 -> 사운드 변화'를 검증한다.

가상 웨이브폼 오브제를 마우스로 움직이면, 렌더된 프레임이 실제 비전 파이프라인
(검출 -> 추적 -> 특징 -> 사운드 매핑)을 그대로 통과해 내장 그래뉼러 신스로 들린다.
나중에 카메라가 생기면 입력만 카메라 프레임으로 바뀐다 — 나머지는 동일.

실행:
    .venv/bin/python -m tracker.sandbox            # 인터랙티브 (마우스+키보드)
    .venv/bin/python -m tracker.sandbox --auto     # 20초 시나리오 자동 재생(조작 불필요)
    .venv/bin/python -m tracker.sandbox --osc 127.0.0.1:9000   # Max/MSP 동시 송출
    .venv/bin/python -m tracker.sandbox --mute     # 내장 신스 끄기 (OSC만)
    .venv/bin/python -m tracker.sandbox --wav source.wav       # 그레인 소스 교체

조작:
    드래그        오브제 이동
    Q / E         선택 오브제 회전 (±5도)
    A             커서 위치에 오브제 추가  /  X  선택 오브제 삭제
    R             기준 정렬로 리셋
    ESC           종료
"""

import argparse
import math
import time

import cv2

from . import synthetic
from .run import draw_overlay, load_cfg
from .scene import SceneTracker, features
from .sound import OscSender, SoundMapper
from .vision import detect, find_contacts

W, H = synthetic.SIZE


class VirtualObjects:
    """가상 오브제들 — 각 오브제는 프래그먼트 하나를 들고 그 웨이브폼 모양을 갖는다.

    배정 정책(스프레드 순서)을 검출측 Arranger 와 동일하게 맞춰서
    '보이는 모양'과 '들리는 조각'이 일치하도록 한다.
    """

    def __init__(self, bank):
        self.bank = bank
        self._rank = {f: i for i, f in enumerate(bank.spread_order)}
        self.selected = None
        self._drag = None
        self.reset()

    def reset(self):
        self._free = list(self.bank.spread_order)
        self.objs = [[110 + i * 140, 240, 0.0, self._free.pop(0)]
                     for i in range(4)]

    def frags(self):
        return [o[3] for o in self.objs]

    # cv2 마우스 콜백
    def on_mouse(self, event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN:
            d, idx = min(((math.hypot(o[0] - x, o[1] - y), i)
                          for i, o in enumerate(self.objs)), default=(1e9, None))
            self.selected = idx if d < 90 else None
            self._drag = (x, y) if self.selected is not None else None
        elif event == cv2.EVENT_MOUSEMOVE and self._drag and self.selected is not None:
            dx, dy = x - self._drag[0], y - self._drag[1]
            self._drag = (x, y)
            o = self.objs[self.selected]
            o[0] = min(max(o[0] + dx, 30), W - 30)
            o[1] = min(max(o[1] + dy, 30), H - 30)
        elif event == cv2.EVENT_LBUTTONUP:
            self._drag = None

    def on_key(self, key, cursor=(W // 2, H // 2)):
        if key in (ord("q"), ord("Q")) and self.selected is not None:
            self.objs[self.selected][2] -= 5.0
        elif key in (ord("e"), ord("E")) and self.selected is not None:
            self.objs[self.selected][2] += 5.0
        elif key in (ord("a"), ord("A")) and self._free:
            self.objs.append([cursor[0], cursor[1], 0.0, self._free.pop(0)])
            self.selected = len(self.objs) - 1
        elif key in (ord("x"), ord("X")) and self.selected is not None:
            self._free.append(self.objs.pop(self.selected)[3])
            self._free.sort(key=self._rank.get)
            self.selected = None
        elif key in (ord("r"), ord("R")):
            self.reset()
            self.selected = None


def main():
    ap = argparse.ArgumentParser(description="Scramble Ritual sound sandbox")
    ap.add_argument("--auto", action="store_true", help="시나리오 자동 재생")
    ap.add_argument("--osc", metavar="HOST:PORT")
    ap.add_argument("--mute", action="store_true", help="내장 신스 끄기")
    ap.add_argument("--wav", nargs="+", metavar="WAV",
                    help="소스 WAV 최대 4개 — 각각 4조각으로 잘려 오브제에 배정")
    ap.add_argument("--gain", type=float, default=0.5)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--record", metavar="PATH", help="세션을 JSONL로 녹화 (tracker.recorder 로 재생)")
    args = ap.parse_args()

    recorder = None
    if args.record:
        from .recorder import Recorder
        recorder = Recorder(args.record)

    cfg = load_cfg()
    tracker = SceneTracker(cfg.get("scene"))
    mapper = SoundMapper(cfg.get("sound"))
    osc = OscSender(*((args.osc.split(":")[0], int(args.osc.split(":")[1]))
                      if args.osc else (None,)))

    from .granular import Arranger, FragmentBank
    bank = FragmentBank.from_wavs(args.wav) if args.wav else FragmentBank.synth()
    arranger = Arranger(bank)
    shapes = synthetic.bank_shapes(bank)        # 조각 16개 = 웨이브폼 모양 16개
    print(f"[프래그먼트 뱅크: 사운드 {len(bank.frags) // bank.pieces}개 x {bank.pieces}조각 "
          f"= {len(bank.frags)}개 ({bank.labels[0]}~{bank.labels[-1]})]")

    engine = None
    if not args.mute:
        from .granular import GranularEngine
        engine = GranularEngine(gain=args.gain, bank=bank)
        engine.start_stream()
        print("[내장 그래뉼러 신스 ON — 끄려면 --mute]")

    virt = VirtualObjects(bank)
    win = "scramble-sandbox"
    cv2.namedWindow(win)
    if not args.auto:
        cv2.setMouseCallback(win, virt.on_mouse)
        print("[드래그=이동  Q/E=회전  A=추가  X=삭제  R=리셋  ESC=종료]")

    dt = 1.0 / args.fps
    t0 = time.monotonic()
    try:
        while True:
            t = time.monotonic() - t0
            if args.auto:
                objs = synthetic.scenario(t)
                frags = bank.spread_order[:len(objs)]
            else:
                objs = [tuple(o[:3]) for o in virt.objs]
                frags = virt.frags()
            frame = synthetic.render(objs, polys=[shapes[f] for f in frags])

            mask, dets = detect(frame, cfg.get("vision"))
            state = tracker.update(dets, find_contacts(dets, cfg.get("vision")))
            feat = features(state, (W, H))
            params = mapper.map(feat)

            order, pans = arranger.update(state, W)
            if engine:
                engine.set_params(params)
                engine.set_arrangement(order, pans)
                for ev, data in state.events:
                    if ev in ("touch", "merge"):
                        pt = data.get("point")
                        pan = pt[0] / W if pt else 0.5
                        engine.push_event(ev, pan)
            osc.send(params, state, (W, H))
            if recorder:
                recorder.write(t, params, state)

            labels = {lid: arranger.label(lid) for lid, _ in state.objects}
            vis = draw_overlay(frame, state, params, labels)
            if virt.selected is not None and not args.auto and virt.selected < len(virt.objs):
                o = virt.objs[virt.selected]
                cv2.circle(vis, (int(o[0]), int(o[1])), 12, (0, 255, 255), 2)
            cv2.imshow(win, vis)

            key = cv2.waitKey(max(1, int(dt * 1000) - 8)) & 0xFF
            if key == 27:
                break
            if key != 255 and not args.auto:
                virt.on_key(key)
    finally:
        if engine:
            engine.stop_stream()
        if recorder:
            print(f"[녹화 저장: {args.record} ({recorder.close()}프레임)]")
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
