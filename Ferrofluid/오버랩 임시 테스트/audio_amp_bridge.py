# audio_amp_bridge.py
# PC에서 재생되는 소리(유튜브 등) -> 아두이노 전자석 PWM   (Serial "0~255\n")
#
# MODE = 'dynamic' : 음절 어택 + 음높이 + 크기를 합친 뒤 대비를 극대화 (기본, 가장 역동적)
# MODE = 'pitch'   : 목소리 음높이만
# MODE = 'amp'     : 소리 크기만
#
# 실행:  python audio_amp_bridge.py          (오디오 반응)
#        python audio_amp_bridge.py sweep    (PWM 0->255 단계 테스트, MIN_PWM 찾기용)
import sys, time, threading
from collections import deque
import numpy as np
import serial, serial.tools.list_ports

# ---------------- 설정 ----------------
MODE     = 'dynamic'
PORT     = 'AUTO'     # 'COM3' 처럼 고정해도 됨
BAUD     = 115200

# 출력 (가장 중요)
MIN_PWM  = 70         # 페로플루이드가 '막 움직이기 시작하는' PWM -> sweep으로 찾아서 입력
MAX_PWM  = 255        # 최대 출력 (과열 시 낮추기)
FORCE_LINEAR = False  # True: 약한 값을 끌어올림(부드러움) / False: 강약 대비 극대화

# dynamic 모드
W_ONSET  = 0.55       # 음절 시작(어택) 비중  : 말 한 마디마다 '툭' 튀는 움직임
W_PITCH  = 0.30       # 음높이 비중           : 억양이 올라가면 세짐
W_AMP    = 0.15       # 크기 비중
ONSET_DECAY = 0.82    # 어택 펄스 지속 (클수록 길게, 0.7~0.9)
STRETCH_WIN = 250     # 최근 이 프레임 수(약 5초) 기준으로 값 범위를 0~1로 늘림
EXPAND   = 2.4        # 대비: 클수록 약한 건 더 약하게, 강한 것만 튐 (1.0~2.5)

# 악센트: 강한 음절 어택 순간 MAX_PWM으로 순간 펀치 -> 페로플루이드 스파이크
ACCENT     = True
ACCENT_TH  = 0.6      # 어택 세기가 이 이상이면 펀치 (낮출수록 자주)
ACCENT_MS  = 70       # 펀치 지속 시간(ms)
ACCENT_GAP = 0.18     # 펀치 최소 간격(초)

# 공통
SILENCE_DB = -65      # 이보다 작은 소리는 무음 (dBFS)
ATTACK   = 0.8        # 커질 때 따라가는 속도 (0~1)
RELEASE  = 0.25       # 작아질 때 빠지는 속도 (0~1)

# amp
RANGE_DB  = 24
PEAK_FALL = 0.03

# pitch
F0_MIN, F0_MAX = 70, 400
VOICED_TH  = 0.5
VOICED_MIN = 0.2
HOLD       = 0.12

BLOCK    = 1024
ANALYSIS = 2048
SEND_DT  = 0.02
SILENT_T = 0.15
# --------------------------------------


def find_port():
    if PORT != 'AUTO':
        return PORT
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        d = f"{p.description} {p.manufacturer}"
        if any(k in d for k in ('Arduino', 'CH340', 'CH341', 'wch', 'USB-SERIAL')):
            return p.device
    usb = [p for p in ports if p.vid is not None or 'USB' in (p.description or '')]
    if len(usb) == 1:
        return usb[0].device
    if len(ports) == 1:
        return ports[0].device
    print("[오류] 아두이노 포트를 못 찾음. PORT = 'COM3' 처럼 직접 지정하세요.")
    for p in ports:
        print("   ", p.device, p.description)
    sys.exit(1)


def detect_f0(x, fs):
    """자기상관 기반 음높이 검출. (f0, 신뢰도)"""
    ds = 4
    n = (len(x) // ds) * ds
    x = x[:n].reshape(-1, ds).mean(axis=1)
    fs2 = fs / ds
    x = x - x.mean()
    n = len(x)
    if n < 64:
        return None, 0.0
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    if ac[0] <= 1e-12:
        return None, 0.0
    ac = ac / ac[0] * n / (n - np.arange(n))
    lo = max(2, int(fs2 / F0_MAX))
    hi = min(n // 2, int(fs2 / F0_MIN))
    if hi <= lo + 2:
        return None, 0.0
    seg = ac[lo:hi]
    i = int(np.argmax(seg >= seg.max() * 0.9))
    while i + 1 < len(seg) and seg[i + 1] > seg[i]:
        i += 1
    lag = lo + i
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
    den = a - 2 * b + c
    if den != 0:
        lag = lag + 0.5 * (a - c) / den
    return fs2 / lag, float(b)


class Processor:
    """프레임마다 (dB, 오디오창) -> 0~1 세기"""
    def __init__(self):
        self.peak_db = SILENCE_DB + RANGE_DB
        self.db_slow = SILENCE_DB
        self.on_peak = 3.0
        self.onset = 0.0
        self.p_hist = deque(maxlen=400)
        self.v_hist = deque(maxlen=STRETCH_WIN)
        self.pv = 0.0
        self.last_voiced = -1e9
        self.f0 = 0.0
        self.env = 0.0
        self.imp = 0.0
        self.acc_until = -1e9
        self.acc_last = -1e9

    def amp(self, db):
        self.peak_db = max(db, self.peak_db - PEAK_FALL, SILENCE_DB + RANGE_DB)
        if db <= SILENCE_DB:
            return 0.0
        return min(1.0, max(0.0, (db - (self.peak_db - RANGE_DB)) / RANGE_DB))

    def pitch(self, db, x, fs, now):
        self.f0 = 0.0
        if x is not None and db > SILENCE_DB:
            f0, conf = detect_f0(x, fs)
            if f0 and conf > VOICED_TH and F0_MIN <= f0 <= F0_MAX:
                st = 12 * np.log2(f0 / 55.0)
                self.p_hist.append(st)
                if len(self.p_hist) >= 40:
                    lo, hi = np.percentile(self.p_hist, [10, 90])
                else:
                    lo, hi = 12 * np.log2(90 / 55.0), 12 * np.log2(260 / 55.0)
                hi = max(hi, lo + 2)
                self.pv = min(1.0, max(0.0, (st - lo) / (hi - lo)))
                self.last_voiced = now
                self.f0 = f0
        return now - self.last_voiced <= HOLD

    def onset_val(self, db):
        d = max(0.0, db - self.db_slow) if db > SILENCE_DB else 0.0
        self.db_slow += (db - self.db_slow) * 0.15
        self.on_peak = max(d, self.on_peak * 0.995, 3.0)
        self.imp = d / self.on_peak
        self.onset = max(self.imp, self.onset * ONSET_DECAY)
        return self.onset

    def step(self, db, x, fs, now):
        if MODE == 'amp':
            v = self.amp(db)
        elif MODE == 'pitch':
            voiced = self.pitch(db, x, fs, now)
            v = VOICED_MIN + (1 - VOICED_MIN) * self.pv if voiced else 0.0
        else:
            a = self.amp(db)
            voiced = self.pitch(db, x, fs, now)
            o = self.onset_val(db)
            raw = W_ONSET * o + W_PITCH * (self.pv if voiced else 0.0) + W_AMP * a
            if db > SILENCE_DB:
                self.v_hist.append(raw)
            if db <= SILENCE_DB or len(self.v_hist) < 20:
                v = 0.0 if db <= SILENCE_DB else raw
            else:
                lo, hi = np.percentile(self.v_hist, [15, 97])
                v = (raw - lo) / max(hi - lo, 1e-3)
                v = min(1.0, max(0.0, v)) ** EXPAND
            if ACCENT and self.imp > ACCENT_TH and now - self.acc_last > ACCENT_GAP and db > SILENCE_DB:
                self.acc_last = now
                self.acc_until = now + ACCENT_MS / 1000
            if now < self.acc_until:
                self.env = 1.0
                return 1.0
        self.env += (v - self.env) * (ATTACK if v > self.env else RELEASE)
        return self.env


def to_pwm(env):
    if env < 0.01:
        return 0
    e = np.sqrt(env) if FORCE_LINEAR else env
    return int(MIN_PWM + e * (MAX_PWM - MIN_PWM))


# ---------------- sweep ----------------
def sweep():
    port = find_port()
    ser = serial.Serial(port, BAUD, timeout=0)
    time.sleep(2)
    print(f"시리얼: {port}   PWM 0 -> 255 단계 테스트. 페로플루이드가 처음 움직이는 값을 MIN_PWM에 넣으세요.\n")
    try:
        for pwm in list(range(0, 256, 10)) + [255]:
            print(f"PWM {pwm:3d}  {'#' * (pwm // 8)}")
            t0 = time.time()
            while time.time() - t0 < 1.5:
                ser.write(f"{pwm}\n".encode())
                time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    ser.write(b"0\n"); time.sleep(0.05); ser.close()
    print("\n완료 (전자석 OFF)")


# ---------------- 오디오 ----------------
lock = threading.Lock()
raw_rms = 0.0
last_cb = 0.0
ring = np.zeros(ANALYSIS, dtype=np.float32)
CH = 2
PA_CONTINUE = 0


def audio_cb(in_data, frame_count, time_info, status):
    global raw_rms, last_cb
    x = np.frombuffer(in_data, dtype=np.float32)
    if CH > 1:
        x = x[: (len(x) // CH) * CH].reshape(-1, CH).mean(axis=1)
    if x.size:
        x = x[-ANALYSIS:]
        with lock:
            ring[:-len(x)] = ring[len(x):]
            ring[-len(x):] = x
            raw_rms = float(np.sqrt(np.mean(x * x)))
            last_cb = time.time()
    return (None, PA_CONTINUE)


def run():
    global CH, PA_CONTINUE
    import pyaudiowpatch as pyaudio
    PA_CONTINUE = pyaudio.paContinue
    port = find_port()
    ser = serial.Serial(port, BAUD, timeout=0)
    time.sleep(2)

    pa = pyaudio.PyAudio()
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError:
        print("[오류] WASAPI 사용 불가"); sys.exit(1)
    dev = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if not dev.get("isLoopbackDevice"):
        for lb in pa.get_loopback_device_info_generator():
            if dev["name"] in lb["name"]:
                dev = lb; break
        else:
            print(f"[오류] 루프백 장치 없음: {dev['name']}"); sys.exit(1)

    CH = max(1, int(dev["maxInputChannels"]))
    rate = int(dev["defaultSampleRate"])
    stream = pa.open(format=pyaudio.paFloat32, channels=CH, rate=rate,
                     input=True, input_device_index=dev["index"],
                     frames_per_buffer=BLOCK, stream_callback=audio_cb)
    stream.start_stream()

    print(f"시리얼: {port}   오디오: {dev['name']} ({rate}Hz, {CH}ch)")
    print(f"MODE {MODE}   MIN_PWM {MIN_PWM}  MAX_PWM {MAX_PWM}  EXPAND {EXPAND}   Ctrl+C 종료\n")

    proc = Processor()
    try:
        while True:
            now = time.time()
            with lock:
                alive = now - last_cb < SILENT_T
                r = raw_rms if alive else 0.0
                x = ring.copy() if (alive and MODE != 'amp') else None
            db = 20 * np.log10(r + 1e-12)
            env = proc.step(db, x, rate, now)
            pwm = to_pwm(env)
            ser.write(f"{pwm}\n".encode())

            bar = '#' * int(env * 40)
            print(f"\r{db:6.1f}dB  F0 {proc.f0:4.0f}  ON {proc.onset:4.2f}  PWM {pwm:3d} |{bar:<40}|",
                  end='', flush=True)
            time.sleep(SEND_DT)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            ser.write(b"0\n"); time.sleep(0.05); ser.close()
        except Exception:
            pass
        stream.stop_stream(); stream.close(); pa.terminate()
        print("\n종료 (전자석 OFF)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'sweep':
        sweep()
    else:
        run()
