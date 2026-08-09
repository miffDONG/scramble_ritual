"""
Morse-code tracker runner.

Examples:
    python -m tracker.morse_run --video ../sampleVideo/morseCode.MOV --show
    python -m tracker.morse_run --camera 0 --sound
    python -m tracker.morse_run --sim --seconds 20 --show
"""

import argparse
import json
import math
import os
import threading
import time

import cv2
import numpy as np

from .granular import FragmentBank, GranularEngine
from .morse import detect_cards, generate_codebook, render_scene
from .sound import SoundMapper

CFG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load_cfg():
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def orbbec_frame_to_bgr(frame, ob_format):
    width = frame.get_width()
    height = frame.get_height()
    data = np.frombuffer(frame.get_data(), dtype=np.uint8)
    fmt = frame.get_format()

    if fmt == ob_format.RGB:
        img = data.reshape((height, width, 3))
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    if fmt == ob_format.BGR:
        return data.reshape((height, width, 3)).copy()
    if fmt == ob_format.MJPG:
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    if fmt == ob_format.YUYV:
        img = data.reshape((height, width, 2))
        return cv2.cvtColor(img, cv2.COLOR_YUV2BGR_YUY2)
    if fmt == ob_format.UYVY:
        img = data.reshape((height, width, 2))
        return cv2.cvtColor(img, cv2.COLOR_YUV2BGR_UYVY)
    if fmt == ob_format.NV12:
        img = data.reshape((height * 3 // 2, width))
        return cv2.cvtColor(img, cv2.COLOR_YUV2BGR_NV12)
    if fmt == ob_format.NV21:
        img = data.reshape((height * 3 // 2, width))
        return cv2.cvtColor(img, cv2.COLOR_YUV2BGR_NV21)
    if fmt == ob_format.I420:
        img = data.reshape((height * 3 // 2, width))
        return cv2.cvtColor(img, cv2.COLOR_YUV2BGR_I420)

    raise RuntimeError(f"unsupported Orbbec color format: {fmt}")


def open_orbbec_source(args):
    try:
        from pyorbbecsdk import Config, Context, OBFormat, OBSensorType, Pipeline
    except ImportError as exc:
        raise SystemExit(
            "--orbbec requires the Orbbec Python SDK. Install it in this "
            "environment with: python -m pip install pyorbbecsdk2. "
            "On macOS, Orbbec currently publishes ARM64 wheels; Intel Macs "
            "may need UVC mode or a Windows/Linux/Apple Silicon host."
        ) from exc

    ctx = Context()
    devices = ctx.query_devices()
    if devices.get_count() == 0:
        raise SystemExit("cannot find Orbbec camera")

    pipeline = Pipeline()
    config = Config()
    try:
        profiles = pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)
        try:
            profile = profiles.get_video_stream_profile(0, 0, OBFormat.RGB, 0)
        except Exception:
            profile = profiles.get_default_video_stream_profile()
        config.enable_stream(profile)
    except Exception as exc:
        raise SystemExit(f"cannot configure Orbbec color stream: {exc}") from exc

    try:
        pipeline.start(config)
    except Exception as exc:
        raise SystemExit(f"cannot open Orbbec camera: {exc}") from exc

    def read_orbbec(_t):
        for _ in range(5):
            frames = pipeline.wait_for_frames(1000)
            if not frames:
                continue
            color = frames.get_color_frame()
            if not color:
                continue
            frame = orbbec_frame_to_bgr(color, OBFormat)
            if frame is not None:
                return frame
        return None

    def close_orbbec():
        try:
            pipeline.stop()
        except Exception:
            pass

    read_orbbec.close = close_orbbec
    return read_orbbec, "orbbec", args.fps


def open_source(args, codebook):
    if args.orbbec:
        return open_orbbec_source(args)

    if args.camera is not None:
        cap = cv2.VideoCapture(args.camera)
        if not cap.isOpened():
            raise SystemExit(f"cannot open camera: {args.camera}")
        fps = cap.get(cv2.CAP_PROP_FPS) or args.fps
        return lambda t: cap.read()[1], f"camera:{args.camera}", fps

    if args.video:
        cap = cv2.VideoCapture(args.video)
        if not cap.isOpened():
            raise SystemExit(f"cannot open video: {args.video}")
        fps = cap.get(cv2.CAP_PROP_FPS) or args.fps

        def read_video(_t):
            ok, frame = cap.read()
            return frame if ok else None

        return read_video, f"video:{args.video}", fps

    codes = list(codebook.values()) if codebook else [
        format(i, f"0{args.slots}b") for i in range(1, 5)
    ]

    def read_sim(t):
        k = 0.5 + 0.5 * math.sin(t * 0.6)
        cards = [
            (codes[0], 220, 160, 8 + 18 * k),
            (codes[1], 610, 170, -24 + 14 * k),
            (codes[2], 260, 360, -12 - 20 * k),
            (codes[3], 650, 360, 35 - 18 * k),
        ]
        return render_scene(cards)

    return read_sim, "synthetic-morse", args.fps


class MorseSoundTable:
    def __init__(self, bank, map_path=None):
        self.bank = bank
        self.mapping = {}
        if map_path:
            with open(map_path, encoding="utf-8") as f:
                raw = json.load(f)
            label_to_index = {label: i for i, label in enumerate(bank.labels)}
            for key, value in raw.items():
                cid = int(key)
                if isinstance(value, str):
                    self.mapping[cid] = label_to_index[value]
                else:
                    self.mapping[cid] = int(value)

    def fragment_for(self, code_id):
        if code_id is None:
            return None
        if code_id in self.mapping:
            return self.mapping[code_id] % len(self.bank.frags)
        return (code_id - 1) % len(self.bank.frags)

    def label_for(self, code_id):
        fi = self.fragment_for(code_id)
        if fi is None:
            return "?"
        return self.bank.labels[fi]


def card_pairs(cards, frame_size):
    """Pairwise measurements between cards: normalized center distance and
    relative axis tilt in degrees. Cards are indexed left-to-right."""
    w, h = frame_size
    diag = math.hypot(w, h)
    ordered = sorted(cards, key=lambda c: (c.cx, c.cy))
    pairs = []
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            a, b = ordered[i], ordered[j]
            dist = math.hypot(b.cx - a.cx, b.cy - a.cy) / max(diag, 1e-6)
            rel = (b.angle - a.angle + 180.0) % 360.0 - 180.0
            pairs.append((i, j, dist, rel))
    return pairs


def features_from_cards(cards, frame_size):
    w, h = frame_size
    n = len(cards)
    if n == 0:
        return {"n": 0, "disorder": 0.0, "scatter": 0.0, "contact_ratio": 0.0,
                "chain": 0.0, "overlap_ratio": 0.0, "spacing": 0.0}

    sx = sum(math.cos(math.radians(2 * c.angle)) for c in cards)
    sy = sum(math.sin(math.radians(2 * c.angle)) for c in cards)
    disorder = 1.0 - math.hypot(sx, sy) / n if n > 1 else 0.0

    mx = sum(c.cx for c in cards) / n
    my = sum(c.cy for c in cards) / n
    if n >= 3:
        sxx = sum((c.cx - mx) ** 2 for c in cards) / n
        syy = sum((c.cy - my) ** 2 for c in cards) / n
        sxy = sum((c.cx - mx) * (c.cy - my) for c in cards) / n
        half = (sxx + syy) / 2.0
        off = math.hypot((sxx - syy) / 2.0, sxy)
        lmax = half + off
        lmin = max(0.0, half - off)
        scatter = lmin / lmax if lmax > 1e-6 else 0.0
    else:
        scatter = 0.0

    # mean nearest-neighbour spacing, normalized by the frame diagonal
    # (ported from the wave version's pairwise distances in bringup.py)
    spacing = 0.0
    if n >= 2:
        diag = math.hypot(w, h)
        nn = []
        for a in cards:
            best = min(math.hypot(b.cx - a.cx, b.cy - a.cy)
                       for b in cards if b is not a)
            nn.append(best)
        spacing = (sum(nn) / n) / max(diag, 1e-6)

    return {
        "n": float(n),
        "disorder": max(0.0, min(1.0, disorder)),
        "scatter": max(0.0, min(1.0, scatter)),
        "contact_ratio": 0.0,
        "chain": 0.0,
        "overlap_ratio": 0.0,
        "spacing": max(0.0, min(1.0, spacing)),
    }


def _osc_arg(v):
    return f"{v:.3f}" if isinstance(v, float) else str(v)


class MorseOscSender:
    """UDP 송출과 콘솔 로깅을 워커 스레드로 분리 — 캡처 루프가 소켓/print 에
    막히지 않도록. 프레임은 최신 1장만 큐잉하고 밀린 것은 버린다."""

    def __init__(self, host=None, port=9000, prefix="/scramble",
                 log=False, log_hz=5.0):
        self.prefix = prefix
        self.client = None
        self.target = f"{host}:{port}"
        self.log = log
        # 0 (or negative) means every frame; otherwise throttle to log_hz frames/s
        self.log_period = 1.0 / log_hz if log_hz > 0 else 0.0
        self._next_log = 0.0
        self.sent = 0
        self.dropped = 0
        self._pending = None
        self._stop = False
        self._cv = threading.Condition()
        self._thread = None
        if host:
            from pythonosc.udp_client import SimpleUDPClient
            self.client = SimpleUDPClient(host, port)
            self._thread = threading.Thread(target=self._worker,
                                            name="osc-send", daemon=True)
            self._thread.start()

    def send(self, params, cards, frame_size, table, features=None, t=0.0):
        """캡처 루프에서 호출 — 프레임을 넘기고 즉시 반환한다."""
        if not self.client:
            return
        with self._cv:
            if self._pending is not None:
                self.dropped += 1
            self._pending = (params, cards, frame_size, table, features, t)
            self._cv.notify()

    def close(self):
        if not self._thread:
            return
        with self._cv:
            self._stop = True
            self._cv.notify()
        self._thread.join(timeout=1.0)

    def _worker(self):
        while True:
            with self._cv:
                while self._pending is None and not self._stop:
                    self._cv.wait()
                job, self._pending = self._pending, None
                if job is None:
                    return
            try:
                self._send_frame(*job)
            except OSError as e:
                print(f"[osc send failed: {e}]")

    def _emit(self, addr, args, show):
        self.client.send_message(addr, args)
        self.sent += 1
        if show:
            body = args if isinstance(args, list) else [args]
            print(f"  -> {addr} {' '.join(_osc_arg(a) for a in body)}")

    def _send_frame(self, params, cards, frame_size, table, features, t):
        show = False
        if self.log and t >= self._next_log:
            self._next_log = t + self.log_period
            show = True
            print(f"[osc {t:5.1f}s -> {self.target}] cards={len(cards)} "
                  f"sent={self.sent} dropped={self.dropped}")

        w, h = frame_size
        for k, v in params.items():
            self._emit(f"{self.prefix}/param/{k}", float(v), show)
        if features and "spacing" in features:
            self._emit(f"{self.prefix}/param/spacing",
                       float(features["spacing"]), show)
        for card in cards:
            self._emit(
                f"{self.prefix}/morse",
                [int(card.code_id or 0), card.bits, table.label_for(card.code_id),
                 float(card.cx / w), float(card.cy / h), float(card.angle),
                 card.status, float(card.score)], show)
        for i, j, dist, rel in card_pairs(cards, frame_size):
            self._emit(
                f"{self.prefix}/pair", [int(i), int(j), float(dist), float(rel)],
                show)


def draw_overlay(frame, cards, glyphs, params, table):
    vis = frame.copy()
    if vis.ndim == 2:
        vis = cv2.cvtColor(vis, cv2.COLOR_GRAY2BGR)

    colors = {
        "start": (0, 220, 255),
        "end": (255, 180, 0),
        "dot": (0, 200, 0),
        "dash": (180, 80, 255),
        "unknown": (100, 100, 100),
    }
    for g in glyphs:
        cv2.drawContours(vis, [g.contour], -1, colors.get(g.kind, (100, 100, 100)), 1)

    for card in cards:
        ok = card.code_id is not None
        color = (0, 230, 0) if ok else (0, 0, 255)
        cv2.line(vis, (int(card.start.cx), int(card.start.cy)),
                 (int(card.end.cx), int(card.end.cy)), color, 2, cv2.LINE_AA)
        for slot in card.slots:
            scol = (0, 220, 0) if slot.bit != "?" else (0, 0, 255)
            cv2.circle(vis, (int(slot.cx), int(slot.cy)), 5, scol, 1, cv2.LINE_AA)
            cv2.putText(vis, slot.bit, (int(slot.cx) - 4, int(slot.cy) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, scol, 1, cv2.LINE_AA)
        text = f"{card.label}->{table.label_for(card.code_id)} {card.bits} {card.status}"
        cv2.putText(vis, text, (int(card.cx) - 90, int(card.cy) - 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

    hud = (f"morse={len(cards)} x={params['scramble']:.2f} "
           f"grain={params['grain_ms']:.0f}ms dens={params['density_hz']:.0f}Hz")
    cv2.putText(vis, hud, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (255, 255, 255), 2, cv2.LINE_AA)
    return vis


def run_image(args, mcfg, codebook):
    """One-shot diagnostic on a still photo: decode, print measurements,
    save overlay + threshold mask."""
    frame = cv2.imread(args.image)
    if frame is None:
        raise SystemExit(f"cannot read image: {args.image}")
    h, w = frame.shape[:2]
    if args.max_dim and max(w, h) > args.max_dim:
        s = args.max_dim / max(w, h)
        frame = cv2.resize(frame, (int(w * s), int(h * s)),
                           interpolation=cv2.INTER_AREA)
        h, w = frame.shape[:2]

    mask, cards, glyphs = detect_cards(frame, mcfg, codebook)
    known = [c for c in cards if c.code_id is not None]
    feats = features_from_cards(known, (w, h))
    params = SoundMapper(load_cfg().get("sound")).map(feats)
    bank = FragmentBank.synth()
    table = MorseSoundTable(bank, args.map)

    print(f"[image: {args.image} -> {w}x{h} | glyphs={len(glyphs)} cards={len(cards)}]")
    for c in cards:
        print(f"  {c.label} bits={c.bits} status={c.status} score={c.score:.2f} "
              f"axis={c.angle:+.1f}deg center=({c.cx:.0f},{c.cy:.0f})")
    for i, j, dist, rel in card_pairs(known, (w, h)):
        print(f"  pair {i}-{j}: dist={dist:.3f} rel_tilt={rel:+.1f}deg")
    print(f"  spacing={feats['spacing']:.3f} disorder={feats['disorder']:.2f} "
          f"scatter={feats['scatter']:.2f}")

    vis = draw_overlay(frame, cards, glyphs, params, table)
    outdir = args.snapshot or os.path.join("diag", "morse")
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.image))[0]
    overlay_path = os.path.join(outdir, f"image_{stem}_overlay.png")
    mask_path = os.path.join(outdir, f"image_{stem}_mask.png")
    cv2.imwrite(overlay_path, vis)
    cv2.imwrite(mask_path, mask)
    print(f"  saved {overlay_path} , {mask_path}")
    if args.show:
        cv2.imshow("scramble-morse", vis)
        cv2.waitKey(0)
        cv2.destroyAllWindows()


def main():
    ap = argparse.ArgumentParser(description="Scramble Ritual Morse tracker")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--camera", type=int)
    src.add_argument("--orbbec", action="store_true",
                     help="use an Orbbec RGB-D camera via pyorbbecsdk2")
    src.add_argument("--video")
    src.add_argument("--sim", action="store_true")
    src.add_argument("--image", metavar="PATH",
                     help="one-shot diagnostic on a still photo")
    ap.add_argument("--max-dim", type=int, default=1600,
                    help="downscale --image so its longest side fits this")
    ap.add_argument("--slots", type=int, default=8)
    ap.add_argument("--code-count", type=int, default=20)
    ap.add_argument("--decode", choices=["codebook", "binary"], default="codebook")
    ap.add_argument("--map", help="JSON map: Morse ID -> bank fragment index or label")
    ap.add_argument("--wav", nargs="+", metavar="WAV",
                    help="source WAV files, each split into 4 fragments")
    ap.add_argument("--sound", action="store_true")
    ap.add_argument("--gain", type=float, default=0.5)
    ap.add_argument("--audio-device", type=int)
    ap.add_argument("--osc", metavar="HOST:PORT")
    ap.add_argument("--prefix", default="/scramble")
    ap.add_argument("--osc-log", action="store_true",
                    help="print every OSC message as it is sent")
    ap.add_argument("--osc-log-hz", type=float, default=5.0,
                    help="throttle --osc-log to N frames/s (0 = every frame)")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--snapshot", metavar="DIR")
    args = ap.parse_args()

    cfg = load_cfg()
    mcfg = dict(cfg.get("morse", {}))
    mcfg.update({
        "slots": args.slots,
        "code_count": args.code_count,
        "decode_mode": args.decode,
    })
    codebook = None
    if args.decode == "codebook":
        codebook = generate_codebook(args.code_count, args.slots,
                                     mcfg.get("min_hamming", 3))

    if args.image:
        run_image(args, mcfg, codebook)
        return

    get_frame, src_name, source_fps = open_source(args, codebook)
    fps = args.fps or source_fps or 30.0
    dt = 1.0 / fps

    bank = FragmentBank.from_wavs(args.wav) if args.wav else FragmentBank.synth()
    table = MorseSoundTable(bank, args.map)
    mapper = SoundMapper(cfg.get("sound"))
    engine = None
    if args.sound:
        engine = GranularEngine(gain=args.gain, bank=bank)
        engine.start_stream(device=args.audio_device)

    osc = None
    if args.osc:
        host, port = args.osc.split(":")
        osc = MorseOscSender(host, int(port), args.prefix,
                             log=args.osc_log, log_hz=args.osc_log_hz)

    print(f"[Morse source: {src_name} | decode:{args.decode} | sound:{'on' if engine else 'off'}]")
    if codebook:
        print("  codebook:", " ".join(f"{i:02d}:{b}" for i, b in codebook.items()))

    t0 = time.monotonic()
    last_log = -1.0
    n_frames = 0
    try:
        while True:
            t = time.monotonic() - t0
            if args.sim and t >= args.seconds:
                break
            frame = get_frame(t)
            if frame is None:
                break
            h, w = frame.shape[:2]
            mask, cards, glyphs = detect_cards(frame, mcfg, codebook)
            known = [c for c in cards if c.code_id is not None]
            feats = features_from_cards(known, (w, h))
            params = mapper.map(feats)

            order_cards = sorted(known, key=lambda c: c.cx)
            order = [table.fragment_for(c.code_id) for c in order_cards]
            pans = [min(max(c.cx / w, 0.05), 0.95) for c in order_cards]
            if engine:
                engine.set_params(params)
                engine.set_arrangement(order, pans)
            if osc:
                osc.send(params, known, (w, h), table, feats, t)

            if t - last_log >= 1.0:
                last_log = t
                seq = " ".join(f"{c.label}:{table.label_for(c.code_id)}"
                               for c in order_cards) or "-"
                raw = " ".join(c.bits for c in cards) or "-"
                print(f"[{t:5.1f}s] cards={len(cards)} known={len(known)} "
                      f"seq=[{seq}] bits=[{raw}] x={params['scramble']:.2f}")

            if args.show or args.snapshot:
                vis = draw_overlay(frame, cards, glyphs, params, table)
                if args.show:
                    cv2.imshow("scramble-morse", vis)
                    if cv2.waitKey(1) & 0xFF == 27:
                        break
                if args.snapshot and n_frames % max(1, int(fps)) == 0:
                    os.makedirs(args.snapshot, exist_ok=True)
                    cv2.imwrite(os.path.join(args.snapshot, f"morse_{n_frames:05d}.png"), vis)

            n_frames += 1
            if not args.video:
                time.sleep(max(0.0, dt - ((time.monotonic() - t0) - t)))
    finally:
        if osc:
            osc.close()
        if engine:
            engine.stop_stream()
        close_source = getattr(get_frame, "close", None)
        if close_source:
            close_source()
        cv2.destroyAllWindows()
    print(f"done: {n_frames} frames")


if __name__ == "__main__":
    main()
