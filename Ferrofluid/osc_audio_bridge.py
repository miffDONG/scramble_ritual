# osc_audio_bridge.py  (v3 — Arduino Mega 2560 · 전자석 25채널, 펌웨어 coil25_mega)
#
# SuperCollider '/audio' [id, amp, tension]  ->  Mega "H" + 16진수 2글자 x 25 + "\n"
#
#   SC 사운드 ID 하나 = 코일 하나 (1:1)
#     ID 0 -> CH1 (D22)  ...  ID 24 -> CH25 (D46)
#     보드1 = ID 0~4 (D22~26)   보드2 = ID 5~9 (D27~31)   보드3 = ID 10~14 (D32~36)
#     보드4 = ID 15~19 (D37~41) 보드5 = ID 20~24 (D42~46)
#   이전 3채널 버전은 _backup_3ch/ 폴더에 보관 (펌웨어는 coil5_mega)
#
#   1) 앰플리튜드 -> 코일 세기 (주된 구동원)
#   2) tension -> 혼돈량(chaos). SC 사운드와 같은 곡선으로 계산
#        chaos = ((tension - 0.5) / 0.49) 를 0~1로 자른 뒤 제곱,  tension >= 1.0(해소)이면 0
#      chaos가 커질수록 세기가 위아래로 더 크게, 더 빠르게 왔다갔다 함 (0.6Hz -> 12Hz)
#
# 실행
#   python osc_audio_bridge.py              본 운영
#   python osc_audio_bridge.py --test       CH1~CH25 차례로 0.6초씩 + 보드별 5개 동시 ON으로 배선 확인
#   python osc_audio_bridge.py --sweep 1    CH1을 0->255로 천천히 올려 움직이기 시작하는 값 찾기 (1~25)
#   python osc_audio_bridge.py --off        전 채널 OFF
#   python osc_audio_bridge.py --dry        아두이노 없이 계산값만 화면에 출력
#   --port COM5 처럼 포트 직접 지정 가능
import argparse, math, random, sys, threading, time

from pythonosc import dispatcher, osc_server

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

# ============================ 네트워크 / 포트 ============================
PORT      = 'AUTO'        # 'COM5' 처럼 고정 가능. Uno(COM3)에서 Mega로 바뀌어 번호가 달라졌을 수 있음
BAUD      = 115200
OSC_IP    = '127.0.0.1'   # 같은 PC의 SC에서만 수신 (SC ~targetIP도 127.0.0.1)
OSC_PORT  = 9000
ADDRESS   = '/audio'
ID_TIMEOUT = 0.30         # 이 시간 동안 안 들어온 ID는 소리 0으로 취급 (SC 퇴장 timeout과 동일)

# ============================ 채널 묶음 ============================
GROUPS = [range(i, i + 1) for i in range(25)]   # CH(n) = ID(n-1), 1:1
GROUP_MODE = 'sum'   # 1:1이라 의미 없음 (여러 ID를 한 코일에 묶을 때만 사용)

# ===================== 앰플리튜드 시각 극대화 (핵심 조절부) =====================
GAIN        = 1.0    # [1] 입력 증폭. AUTO_NORM을 끄면 이게 주 조절값 (2~4)
AUTO_NORM   = True   # [2] 최근 최대값으로 나눠 0~1을 꽉 채움 (작은 음량차도 크게 벌어짐)
PEAK_FALL_S = 6.0    #     최근 최대값이 잊혀지는 시간(초). 짧을수록 조용한 구간도 크게 반응
PEAK_FLOOR  = 0.15   #     기준 최대값의 하한. 이보다 작은 소리는 끝까지 부풀리지 않음 (잡음·잔향 방지)
GLOBAL_LINK = 0.5    #     다른 채널 최대값도 이 비율만큼 기준에 반영. 0 = 채널별 완전 독립(조용한
                     #     채널도 풀 출력까지 올라감), 1 = 25채널이 같은 기준(실제 음량차 그대로)
GATE        = 0.06   # [3] 정규화 후 이 아래는 0 (잔향·잡음 컷)
EXPAND      = 1.8    # [4] 대비 곡선. 1 = 그대로, 1보다 크면 큰 소리와 작은 소리 차이가 벌어짐 (1.5~2.6)
ATTACK_S    = 0.015  # [5] 커질 때 따라가는 시간(초). 짧을수록 타격음이 날카롭게 솟음
RELEASE_S   = 0.14   #     작아질 때 빠지는 시간(초). 짧을수록 가시가 빨리 꺼져 깜빡임이 또렷
ONSET_TH    = 0.22   # [6] 소리가 이만큼 급상승하면 순간 최대 출력 (타격·마찰 시작점 강조)
PUNCH_S     = 0.07   #     순간 최대 출력 지속 시간(초). 0이면 끔
MIN_LEVEL   = 70     # [7] 소리가 있을 때 최소 출력(0~255). 페로플루이드가 막 움직이기 시작하는 값
                     #     (Uno·31kHz에서 50~60 실측 -> 지금은 248Hz라 --sweep로 다시 잴 것)
MAX_LEVEL   = 255    # [8] 최대 출력. 255 = SMPS 풀 출력
OFF_LEVEL   = 0.02   #     내부 세기가 이 아래면 완전히 OFF (MIN_LEVEL에 걸려 계속 켜져 있는 것 방지)

# ===================== tension -> 편차 가속 =====================
CHAOS_LO     = 0.50  # SC와 동일: tension 0.5부터 변조 시작
CHAOS_HI     = 0.99  # SC와 동일: 0.99에서 최대
RESOLVE_AT   = 1.00  # SC와 동일: 1.0 = 접촉·해소 -> 흔들림 0, 안정
CHAOS_SMOOTH_S = 0.40  # 혼돈량이 따라가는 시간(초). 점점 변해가는 느낌
WOBBLE_HZ_MIN  = 0.6   # 혼돈 시작 시 흔들림 속도(Hz)
WOBBLE_HZ_MAX  = 12.0  # 최대 혼돈 시 흔들림 속도(Hz). 페로플루이드가 따라오는 한계는 대략 10~15Hz
WOBBLE_DEPTH   = 0.65  # 최대 혼돈 시 흔들림 폭 (출력 범위 대비, 0~1)
WOBBLE_SHARP   = 2.5   # 파형 모서리. 0에 가까우면 부드러운 사인, 클수록 ON/OFF에 가까운 각진 파형
WOBBLE_RANDOM  = 0.45  # 반주기마다 폭을 무작위로 (0 = 규칙적, 1 = 불규칙)
FREQ_JITTER    = 0.30  # 반주기마다 속도를 ±15% 흔듦 (채널끼리 서로 맞물리지 않게)

# ============================ 열 보호 (기본 꺼짐) ============================
THERMAL_GUARD = False  # True: 최근 평균 출력이 THERMAL_LIMIT를 넘으면 자동으로 낮춤
THERMAL_TAU_S = 120.0  # 평균을 내는 시간(초)
THERMAL_LIMIT = 0.60   # 허용 평균 출력 (1.0 = 상시 풀 출력)

# ============================ 전체 전력 상한 ============================
# 25개 코일이 동시에 풀 출력이면 SMPS가 못 버틸 수 있음. 매 순간 25채널 출력 합을 이 비율 이하로 제한.
#   1.0 = 제한 없음.  계산법: (SMPS 정격 A x 0.8) / (코일 1개 전류 A x 25)
#   예) 24V 20A SMPS, 코일 1개 2A  ->  20 x 0.8 / (2 x 25) = 0.32
POWER_BUDGET = 1.0

# ============================ 기타 ============================
SEND_HZ   = 60         # 아두이노로 보내는 횟수/초 (흔들림 12Hz를 표현하려면 40 이상)
PRINT_HZ  = 10
DEBUG_RAW = False      # True면 들어오는 OSC 원본을 1.5초마다 출력
# =====================================================================

NCH = len(GROUPS)                                   # 25 (펌웨어 프로토콜 고정)
ID_TO_CH = {i: ch for ch, g in enumerate(GROUPS) for i in g}
ACTIVE = [ch for ch, g in enumerate(GROUPS) if len(g) > 0]

lock = threading.Lock()
ids = {}               # id -> [amp, tension, 마지막 수신 시각]
last_raw = [0.0]
rx = {'audio': 0, 'other': 0, 'bad': 0}   # 수신 카운터 (진단용)
seen_addr = set()


def parse_id(v):
    """SC 키: "0"~"24"(기본 idString), 정수, 또는 8비트 문자열 "00000101"."""
    if isinstance(v, (bytes, bytearray)):
        v = v.decode(errors='ignore')
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        i = v
    elif isinstance(v, float):
        if not math.isfinite(v) or not v.is_integer():
            return None
        i = int(v)
    else:
        s = str(v).strip()
        if len(s) == 8 and set(s) <= {'0', '1'}:
            i = int(s, 2)
        elif s.isdigit():
            i = int(s)
        else:
            return None
    return i if 0 <= i <= 24 else None


def parse_float(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def on_audio(addr, *args):
    now = time.time()
    rx['audio'] += 1
    if 'audio' not in seen_addr:
        seen_addr.add('audio')
        print(f"\n[OSC] 첫 /audio 수신: {args}")
    if DEBUG_RAW and now - last_raw[0] > 1.5:
        last_raw[0] = now
        print(f"\n[RAW] {addr} {args}")
    if len(args) < 2:
        rx['bad'] += 1
        return
    i = parse_id(args[0])
    amp = parse_float(args[1])
    if i is None or amp is None:
        rx['bad'] += 1
        if rx['bad'] <= 3:
            print(f"\n[OSC] 해석 못 한 /audio: {args}")
        return
    ten = parse_float(args[2]) if len(args) > 2 else 0.0
    if ten is None:
        ten = 0.0
    with lock:
        ids[i] = [min(1.0, max(0.0, amp)), min(1.0, max(0.0, ten)), now]


def chaos_of(t):
    if t >= RESOLVE_AT:
        return 0.0
    c = (t - CHAOS_LO) / (CHAOS_HI - CHAOS_LO)
    c = min(1.0, max(0.0, c))
    return c * c


def k_of(dt, tau):
    return 1.0 if tau <= 0 else 1.0 - math.exp(-dt / tau)


class Channel:
    def __init__(self, idx):
        self.idx = idx
        self.peak = PEAK_FLOOR
        self.env = 0.0
        self.slow = 0.0
        self.punch = 0.0
        self.chaos = 0.0
        self.phase = random.random() * 2 * math.pi
        self.half = 0 if self.phase < math.pi else 1
        self.sh = 1.0
        self.fmul = 1.0
        self.heat = 0.0
        self.level = 0

    def update_peak(self, raw, dt):
        x = raw * GAIN
        self.peak = max(x, PEAK_FLOOR, self.peak * math.exp(-dt / PEAK_FALL_S))
        return x

    def step(self, x, global_peak, chaos_target, dt):
        # ---- [1][2] 증폭 + 자동 정규화 (x는 update_peak에서 GAIN 적용됨) ----
        if AUTO_NORM:
            x = x / max(self.peak, GLOBAL_LINK * global_peak)
        x = min(1.0, max(0.0, x))
        # ---- [3] 게이트 ----
        x = 0.0 if x <= GATE else (x - GATE) / (1.0 - GATE)
        # ---- [4] 대비 곡선 ----
        x = x ** EXPAND
        # ---- [5] 어택 / 릴리즈 ----
        self.env += (x - self.env) * k_of(dt, ATTACK_S if x > self.env else RELEASE_S)
        # ---- [6] 급상승 강조 ----
        if PUNCH_S > 0 and x - self.slow > ONSET_TH and self.punch <= 0:
            self.punch = PUNCH_S
        self.slow += (x - self.slow) * k_of(dt, 0.25)
        lv = self.env
        if self.punch > 0:
            self.punch -= dt
            lv = 1.0

        # ---- tension -> 흔들림 ----
        self.chaos += (chaos_target - self.chaos) * k_of(dt, CHAOS_SMOOTH_S)
        c = self.chaos
        f = (WOBBLE_HZ_MIN + (WOBBLE_HZ_MAX - WOBBLE_HZ_MIN) * c) * self.fmul
        self.phase = (self.phase + 2 * math.pi * f * dt) % (2 * math.pi)
        half = 0 if self.phase < math.pi else 1
        if half != self.half:                       # 반주기마다 폭·속도 재추첨
            self.half = half
            self.sh = 1.0 - WOBBLE_RANDOM * random.random()
            self.fmul = 1.0 + FREQ_JITTER * (random.random() - 0.5)
        s = math.sin(self.phase)
        w = math.tanh(WOBBLE_SHARP * s) / math.tanh(WOBBLE_SHARP) if WOBBLE_SHARP > 0 else s
        w *= self.sh

        # ---- 0~255 ----
        if lv < OFF_LEVEL:
            out = 0
        else:
            o = min(1.0, max(0.0, lv + WOBBLE_DEPTH * c * w))
            out = 0 if o <= 0.0 else int(round(MIN_LEVEL + o * (MAX_LEVEL - MIN_LEVEL)))

        # ---- 열 보호 ----
        self.heat += (out / 255.0 - self.heat) * k_of(dt, THERMAL_TAU_S)
        if THERMAL_GUARD and self.heat > THERMAL_LIMIT and out > 0:
            out = int(out * THERMAL_LIMIT / self.heat)

        self.level = max(0, min(255, out))
        return self.level


# ============================ 시리얼 ============================
class Link:
    def __init__(self, port, dry):
        self.port_cfg = port
        self.dry = dry
        self.ser = None
        self.next_try = 0.0

    def find_port(self):
        if self.port_cfg != 'AUTO':
            return self.port_cfg
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            d = f"{p.description} {p.manufacturer}"
            if 'Mega' in d:
                return p.device
        for p in ports:
            d = f"{p.description} {p.manufacturer}"
            if p.vid == 0x2341 or any(k in d for k in ('Arduino', 'CH340', 'CH341', 'wch', 'USB-SERIAL')):
                return p.device
        usb = [p for p in ports if p.vid is not None]
        if len(usb) == 1:
            return usb[0].device
        print("\n[오류] Mega 포트를 못 찾음. --port COM5 처럼 직접 지정하세요. 현재 포트:")
        for p in ports:
            print("   ", p.device, p.description)
        return None

    def ensure(self):
        if self.dry or self.ser is not None:
            return
        now = time.time()
        if now < self.next_try:
            return
        self.next_try = now + 2.0
        if serial is None:
            print("[오류] pyserial 미설치: pip install pyserial")
            sys.exit(1)
        port = self.find_port()
        if port is None:
            return
        try:
            self.ser = serial.Serial(port, BAUD, timeout=0, write_timeout=0.2)
            print(f"\n[SERIAL] {port} 연결 — Mega 리셋 대기 2초")
            time.sleep(2.0)
            self.ser.reset_input_buffer()
        except Exception as e:
            print(f"\n[SERIAL] {port} 열기 실패: {e}")
            self.ser = None

    def send(self, levels):
        self.ensure()
        if self.dry or self.ser is None:
            return
        try:
            self.ser.write(frame(levels))
        except Exception as e:
            print(f"\n[SERIAL] 송신 실패, 재연결 시도: {e}")
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def off(self):
        if self.ser is not None:
            try:
                self.ser.write(b"X\n")
                self.ser.write(frame([0] * NCH))
                self.ser.flush()
                time.sleep(0.05)
                self.ser.close()
            except Exception:
                pass
            self.ser = None


def frame(levels):
    return ("H" + "".join(f"{max(0, min(255, int(v))):02X}" for v in levels) + "\n").encode()


SHADE = " .:-=+*#%@"


def board_view(levels):
    """25채널을 보드별 5글자로: 공백=0 ... @=255"""
    ch = [SHADE[min(9, (v * 10) // 256)] if v > 0 else '_' for v in levels]
    return " ".join(f"B{b + 1}[{''.join(ch[b * 5:b * 5 + 5])}]" for b in range(NCH // 5))


def bar(v, n=10):
    k = int(round(v / 255 * n))
    return '#' * k + '.' * (n - k)


# ============================ 모드 ============================
def run_bridge(link):
    disp = dispatcher.Dispatcher()
    disp.map(ADDRESS, on_audio)
    def on_other(addr, *a):
        rx['other'] += 1
        if addr not in seen_addr and len(seen_addr) < 20:
            seen_addr.add(addr)
            print(f"\n[OSC] 다른 주소 수신: {addr} {a}")
    disp.set_default_handler(on_other)
    server = osc_server.ThreadingOSCUDPServer((OSC_IP, OSC_PORT), disp)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print(f"OSC 수신 {OSC_IP}:{OSC_PORT} {ADDRESS}   ID 0~24 -> CH1~25 (D22~D46, 1:1)   전력상한 {POWER_BUDGET}")
    print(f"GAIN {GAIN}  AUTO_NORM {AUTO_NORM}  LINK {GLOBAL_LINK}  EXPAND {EXPAND}  MIN/MAX {MIN_LEVEL}/{MAX_LEVEL}  "
          f"흔들림 {WOBBLE_HZ_MIN}~{WOBBLE_HZ_MAX}Hz  열보호 {'ON' if THERMAL_GUARD else 'OFF'}   Ctrl+C 종료\n")

    chans = [Channel(i) for i in range(NCH)]
    period = 1.0 / SEND_HZ
    last = time.perf_counter()
    last_print = 0.0
    while True:
        now_p = time.perf_counter()
        dt = min(0.1, max(1e-4, now_p - last))
        last = now_p
        now = time.time()

        raw = [[] for _ in range(NCH)]
        cha = [0.0] * NCH
        with lock:
            for i, (amp, ten, t) in list(ids.items()):
                if now - t > ID_TIMEOUT:
                    del ids[i]
                    continue
                ch = ID_TO_CH[i]
                raw[ch].append(amp)
                cha[ch] = max(cha[ch], chaos_of(ten))
            nobj = len(ids)

        xs = []
        for ch in range(NCH):
            a = raw[ch]
            if not a:
                r = 0.0
            elif GROUP_MODE == 'max':
                r = max(a)
            else:
                r = min(1.0, sum(a))
            xs.append(chans[ch].update_peak(r, dt))
        gpeak = max(c.peak for c in chans)
        levels = [chans[ch].step(xs[ch], gpeak, cha[ch], dt) if ch in ACTIVE else 0
                  for ch in range(NCH)]
        total = sum(levels) / (255.0 * NCH)
        if POWER_BUDGET < 1.0 and total > POWER_BUDGET:
            k = POWER_BUDGET / total
            levels = [int(v * k) for v in levels]

        link.send(levels)

        if now - last_print > 1.0 / PRINT_HZ:
            last_print = now
            heat = max(c.heat for c in chans) * 100
            chaos = max(c.chaos for c in chans)
            pw = sum(levels) / (255.0 * NCH) * 100
            line = (f"rx {rx['audio']:6d} obj {nobj:2d} | {board_view(levels)} | "
                    f"전력 {pw:3.0f}% chaos {chaos:.2f} heat {heat:3.0f}%")
            print("\r" + line + "   ", end='', flush=True)

        sleep = period - (time.perf_counter() - now_p)
        if sleep > 0:
            time.sleep(sleep)


def hold(link, levels, seconds):
    end = time.time() + seconds
    while time.time() < end:
        link.send(levels)
        time.sleep(0.05)                     # 펌웨어 타임아웃(300ms)보다 자주 보냄


def run_test(link, duty):
    print(f"배선 확인: CH1~CH25 차례로 {duty}/255 0.6초씩 -> 보드별 5개 동시 1.5초")
    for ch in ACTIVE:
        lv = [0] * NCH
        lv[ch] = duty
        print(f"  보드{ch // 5 + 1}  CH{ch + 1:<2d} (D{22 + ch}) ON")
        hold(link, lv, 0.6)
        hold(link, [0] * NCH, 0.2)
    for b in range(NCH // 5):
        lv = [duty if b * 5 <= c < b * 5 + 5 else 0 for c in range(NCH)]
        print(f"  보드{b + 1} 5개 동시 ON (D{22 + b * 5}~D{26 + b * 5})")
        hold(link, lv, 1.5)
        hold(link, [0] * NCH, 0.5)
    print("완료")


def run_sweep(link, ch):
    print(f"CH{ch} 스윕: 0 -> 255 (5씩, 0.6초 간격). 페로플루이드가 처음 움직이는 값을 MIN_LEVEL에 적으세요. Ctrl+C 종료")
    for v in range(0, 256, 5):
        lv = [0] * NCH
        lv[ch - 1] = v
        print(f"\r  CH{ch} = {v:3d}  {bar(v, 30)}", end='', flush=True)
        hold(link, lv, 0.6)
    print()


def main():
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument('--port')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--test', action='store_true')
    ap.add_argument('--test-level', type=int, default=160)
    ap.add_argument('--sweep', type=int, choices=[c + 1 for c in ACTIVE], metavar='CH')
    ap.add_argument('--off', action='store_true')
    a = ap.parse_args()
    if a.port:
        PORT = a.port

    link = Link(PORT, a.dry)
    try:
        if a.off:
            link.ensure()
            hold(link, [0] * NCH, 0.2)
        elif a.test:
            link.ensure()
            run_test(link, max(0, min(255, a.test_level)))
        elif a.sweep:
            link.ensure()
            run_sweep(link, a.sweep)
        else:
            run_bridge(link)
    except KeyboardInterrupt:
        pass
    finally:
        link.off()
        print("\n종료 (전 채널 OFF)")


if __name__ == '__main__':
    main()
