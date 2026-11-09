# osc_audio_bridge.py
# SC '/audio' [bitStr, amp, tension] -> 전자석 1기 (Serial "energy,prox\n")
#
# 추적 규칙
# - 고정 ID 없음. 수신되는 ID 중 가장 먼저 나타난 오브제에 락온
# - 추적 중인 ID가 LOST_TIMEOUT 동안 안 들어오면 해제
#   -> 그 시점에 살아 있는 다른 ID 중 가장 먼저 나타난 것으로 전환
#   -> 없으면 대기 (전자석은 감쇠해서 0으로)
import serial, time, threading
from pythonosc import dispatcher, osc_server

PORT     = 'COM3'
OSC_IP   = "127.0.0.1"
OSC_PORT = 9000
ADDRESS  = "/audio"

ID_INDEX   = 0            # ID 위치
AMP_INDEX  = 1            # 진폭 위치
DIST_INDEX = 2            # 거리 위치 (없으면 None)

LOST_TIMEOUT = 0.5        # 추적 ID가 이 시간(초) 동안 안 오면 사라진 것으로 판단
ALIVE_WINDOW = 0.5        # 전환 후보: 최근 이 시간(초) 안에 들어온 ID만

GAIN     = 8.0            # 진폭 증폭
DIST_MAX = 0.9            # 거리 최대값 (0~0.9 스펙트럼)
DIST_INV = False          # True면 거리 방향 반전
SMOOTH   = 0.45           # 진폭 상승 속도
DECAY    = 0.90           # 무음 감쇠
PROX_SM  = 0.15           # 근접도 평활 (느리게 변함)
SEND_DT  = 0.03           # 시리얼 송신 간격(초)

DEBUG = True

ser = serial.Serial(PORT, 115200, timeout=0)
time.sleep(2)

lock = threading.Lock()
energy = 0.0
prox   = 0.0
last_send = 0.0
last_dbg  = 0.0

target_id = None          # 현재 추적 중인 ID
seen = {}                 # id -> {"first": 첫 수신 시각, "last": 마지막 수신 시각}


def norm_id(v):
    if isinstance(v, (bytes, bytearray)):
        v = v.decode(errors='ignore')
    return str(v).strip()


def prune(now):
    """오래 안 들어온 ID는 목록에서 제거 (다시 나타나면 새 오브제로 취급)."""
    for k in [k for k, s in seen.items() if now - s["last"] > ALIVE_WINDOW]:
        del seen[k]


def pick_target(now):
    """추적 ID 유지 또는 전환. 바뀌면 로그 출력."""
    global target_id, prox
    if target_id is not None:
        s = seen.get(target_id)
        if s is not None and now - s["last"] <= LOST_TIMEOUT:
            return                                  # 유지
    prune(now)
    alive = [(s["first"], k) for k, s in seen.items() if k != target_id]
    new = min(alive)[1] if alive else None
    if new != target_id:
        old = target_id
        target_id = new
        prox = 0.0 if new is not None else prox     # 새 오브제는 근접도 새로 수렴
        if new is None:
            print(f"\n[TARGET] {old} 사라짐 -> 대기")
        elif old is None:
            print(f"\n[TARGET] 락온 {new}")
        else:
            print(f"\n[TARGET] {old} 사라짐 -> {new} 전환")


def send(now):
    global last_send
    if now - last_send > SEND_DT:
        last_send = now
        ser.write(f"{energy:.3f},{prox:.3f}\n".encode())
        bar  = '#' * int(energy * 30)
        pbar = '=' * int(prox * 20)
        tid  = target_id if target_id is not None else '-'
        print(f"\r[{tid:>8}] AMP {energy:.2f} |{bar:<30}|  PROX {prox:.2f} |{pbar:<20}|  ({len(seen)} obj)", end='')


def on_audio(addr, *args):
    global energy, prox, last_dbg

    now = time.time()
    if len(args) <= ID_INDEX:
        return
    oid = norm_id(args[ID_INDEX])

    with lock:
        # --- 수신 ID 기록 ---
        s = seen.get(oid)
        if s is None:
            seen[oid] = {"first": now, "last": now}
        else:
            s["last"] = now

        pick_target(now)
        if oid != target_id:
            return

        # --- 진폭 ---
        if len(args) <= AMP_INDEX:
            return
        try:
            amp = float(args[AMP_INDEX])
        except (TypeError, ValueError):
            return
        if amp < 1e-8:
            amp = 0.0

        lo = min(1.0, amp * GAIN)
        if lo > energy:
            energy += (lo - energy) * SMOOTH
        else:
            energy *= DECAY

        # --- 거리 → 근접도 ---
        if DIST_INDEX is not None and len(args) > DIST_INDEX:
            try:
                d = float(args[DIST_INDEX])
                p = max(0.0, min(1.0, d / DIST_MAX))
                if DIST_INV:
                    p = 1.0 - p
                prox += (p - prox) * PROX_SM
            except (TypeError, ValueError):
                pass

        if DEBUG and now - last_dbg > 1.5:
            last_dbg = now
            print(f"\n[RAW] {args}")

        send(now)


def watchdog():
    """메시지가 끊겨도 타깃 해제/전환과 감쇠가 진행되도록 주기적으로 점검."""
    global energy
    while True:
        time.sleep(SEND_DT)
        now = time.time()
        with lock:
            pick_target(now)
            s = seen.get(target_id) if target_id is not None else None
            if s is None or now - s["last"] > SEND_DT * 2:   # 타깃 신호 없음 -> 감쇠
                energy *= DECAY
                if energy < 1e-3:
                    energy = 0.0
                send(now)


disp = dispatcher.Dispatcher()
disp.map(ADDRESS, on_audio)
disp.set_default_handler(lambda addr, *a: None)

server = osc_server.ThreadingOSCUDPServer((OSC_IP, OSC_PORT), disp)
threading.Thread(target=watchdog, daemon=True).start()
print(f"OSC 수신: {OSC_PORT} / {ADDRESS}")
print(f"추적: 첫 번째 ID 락온, {LOST_TIMEOUT}s 미수신 시 다른 ID로 전환   GAIN {GAIN}   Ctrl+C 종료\n")
try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\n종료")
    ser.close()
