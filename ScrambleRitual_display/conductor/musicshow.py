"""
Scramble Ritual — 음악 반응 웨이브 쇼 러너

시뮬레이션 (하드웨어·오디오 장치 불필요, 합성 드럼 루프):
    .venv/bin/python -m conductor.musicshow --sim --seconds 12
음원 파일 (스피커 재생 + 분석 동기, --mute 로 재생 생략):
    .venv/bin/python -m conductor.musicshow --wav track.wav
    .venv/bin/python -m conductor.musicshow --wav track.wav --port /dev/cu.usbserial-XXXX
실시간 입력 (마이크/라인인 — 공연장 PA 신호를 그대로 받을 때):
    .venv/bin/python -m conductor.musicshow --listen [--device N]
    .venv/bin/python -m conductor.musicshow --list-devices

패턴 모드 (--mode): ripple(타격 파문) / sweep(저역 진행파) / spectrum(대역 기둥)
                    / mix(기본 — 셋 혼합)

경로: 오디오 -> AudioAnalyzer(특징) -> WaveField(필드) -> select_target(밀도)
      -> FrameGuard(쿨다운·동시ON·연속ON 보호) -> link.send_frame -> 릴레이->전자석
"""

import argparse
import json
import os
import time

from .link import MockLink, SerialLink
from .music import AudioAnalyzer, MicSource, SimSource, WavSource
from .protocol import GRID
from .waves import MODES, FrameGuard, WaveField, select_target

CFG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
BARS = " ▁▂▃▄▅▆▇█"


def load_cfg():
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _bar(v):
    return BARS[max(0, min(8, int(v * 8.999)))]


def render(frame, feat, guard, t, mode, ansi=True):
    head = (f"♪ mode={mode}  t={t:6.1f}s  ON={guard.on_count():3d}/144  "
            f"flips={guard.flips_total}  forced_off={guard.forced_off_total}"
            + ("  *ONSET*" if feat["onset"] else ""))
    meter = ("bands " + "".join(_bar(v) for v in feat["bands"])
             + f"   bass {_bar(feat['bass'])}  rms {_bar(feat['rms'])}")
    rows = []
    for r in range(GRID - 1, -1, -1):              # 위가 11행, 아래가 0행
        rows.append(" ".join("■" if frame[r * GRID + c] else "·" for c in range(GRID)))
    out = head + "\n" + meter + "\n" + "\n".join(rows)
    if ansi:
        print("\x1b[H\x1b[2J" + out, flush=True)
    else:
        print(out + "\n", flush=True)


def run_show(link, source, cfg, args):
    mcfg = cfg.get("music", {})
    fps = args.fps or mcfg.get("fps", 20)
    dt = 1.0 / fps
    analyzer = AudioAnalyzer(source.samplerate, mcfg.get("audio"))
    field_gen = WaveField(mcfg.get("wave"), mode=args.mode)
    guard = FrameGuard(mcfg.get("guard"))
    sel = mcfg.get("select", {})
    floor = sel.get("density_floor", 0.0)
    gain = sel.get("density_gain", 1.0)
    max_on = guard.cfg["max_on"]

    link.set_limit(max_on)                         # 펌웨어 2차 방어선도 동일 상한으로

    seconds = args.seconds
    if seconds is None:
        seconds = source.duration or float("inf")

    playback = None
    if args.wav and not args.mute:
        import sounddevice as sd
        sd.play(source.data, source.samplerate)
        playback = sd

    t0 = time.monotonic()
    n_sent = n_fail = 0
    try:
        while True:
            t = time.monotonic() - t0
            if t >= seconds:
                break
            samples = source.read(t, analyzer.block)
            feat = analyzer.feed(samples, t, dt)
            field = field_gen.update(feat, dt)
            energy = 0.6 * feat["bass"] + 0.4 * feat["rms"]
            target = select_target(field, energy, max_on, floor=floor, gain=gain)
            frame = guard.commit(target, t)
            a = link.send_frame(frame)
            n_sent += 1
            if not a.ok:
                n_fail += 1
            if a.flags & 1:
                print("[경고] 보드 워치독 안전정지가 있었음 — 호스트 전송 주기 점검")
            if not args.quiet:
                render(frame, feat, guard, t, args.mode, ansi=not args.no_ansi)
            time.sleep(max(0.0, dt - ((time.monotonic() - t0) - t)))
    except KeyboardInterrupt:
        pass
    finally:
        link.all_off()
        if playback:
            playback.stop()
        if hasattr(source, "close"):
            source.close()
    print(f"\n완료: {n_sent}프레임 전송, 실패 {n_fail}, 총 토글 {guard.flips_total}회, "
          f"열보호 강제OFF {guard.forced_off_total}회")


def main():
    ap = argparse.ArgumentParser(description="음악 반응 웨이브 쇼 (릴레이-전자석)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--wav", help="WAV 파일 (재생하며 분석 동기)")
    src.add_argument("--listen", action="store_true", help="마이크/라인인 실시간 입력")
    src.add_argument("--sim", action="store_true", help="합성 드럼 루프 (장치 불필요)")
    ap.add_argument("--device", type=int, help="--listen 입력 장치 번호")
    ap.add_argument("--list-devices", action="store_true", help="오디오 장치 목록 출력")
    ap.add_argument("--mode", choices=sorted(MODES), default="mix")
    ap.add_argument("--port", help="시리얼 포트 (없으면 MockLink 시뮬레이션)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, help="실행 시간 (기본: WAV 길이/무한)")
    ap.add_argument("--fps", type=float, help="프레임레이트 (기본: config.json)")
    ap.add_argument("--mute", action="store_true", help="WAV 스피커 재생 생략")
    ap.add_argument("--no-ansi", action="store_true", help="화면 클리어 없이 출력")
    ap.add_argument("--quiet", action="store_true", help="프레임 렌더링 생략")
    args = ap.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    cfg = load_cfg()
    mcfg = cfg.get("music", {})
    block = mcfg.get("audio", {}).get("block", 2048)

    if args.wav:
        source = WavSource(args.wav)
        print(f"[WAV: {args.wav} — {source.duration:.1f}s @ {source.samplerate}Hz]")
    elif args.listen:
        source = MicSource(device=args.device, block=block)
        print(f"[실시간 입력 @ {source.samplerate}Hz — Ctrl+C 로 종료]")
    else:
        source = SimSource()
        print("[합성 드럼 루프 시뮬레이션 — 실음원은 --wav, 실입력은 --listen]")
        if args.seconds is None:
            args.seconds = 12.0

    link = SerialLink(args.port, args.baud) if args.port else MockLink()
    if not args.port:
        print("[시뮬레이션 모드 — MockLink. 실보드는 --port 지정]")

    run_show(link, source, cfg, args)


if __name__ == "__main__":
    main()
