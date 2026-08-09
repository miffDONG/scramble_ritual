"""
Scramble Ritual — 시리얼 프로토콜 (호스트 <-> 아두이노 공용 정의)

패킷 구조 (양방향 동일):
    [SYNC][TYPE][SEQ][LEN][PAYLOAD ...][CRC8]
    - SYNC : 호스트->보드 0xA5, 보드->호스트 0x5A
    - CRC8 : TYPE부터 PAYLOAD 끝까지 (poly 0x31, init 0x00)

호스트 -> 보드:
    T_FRAME   (0x01) payload 18바이트 = 144비트 릴레이 상태 (cell i -> byte i>>3, bit i&7)
    T_ALL_OFF (0x02) payload 없음 — 전 채널 즉시 OFF
    T_PING    (0x03) payload 없음 — 생존 확인
    T_TEST    (0x04) payload 1바이트 = 패턴 번호 (0=off,1=행 스윕,2=열 스윕,3=체커,4=1칸 워크)
    T_LIMIT   (0x05) payload 1바이트 = 동시 ON 최대 개수 (1~144)

보드 -> 호스트:
    T_ACK  (0x81) 헤더 SEQ=요청 seq 에코, payload [on_count][flags]
                  flags bit0: 직전에 워치독 안전정지가 발생했음
    T_NACK (0x82) 헤더 SEQ=요청 seq 에코, payload [err]

펌웨어(firmware/relay_matrix/relay_matrix.ino)와 반드시 1:1로 일치해야 한다.
"""

GRID = 12
N_CELLS = GRID * GRID          # 144
N_BYTES = N_CELLS // 8         # 18

SYNC_H2D = 0xA5
SYNC_D2H = 0x5A

T_FRAME = 0x01
T_ALL_OFF = 0x02
T_PING = 0x03
T_TEST = 0x04
T_LIMIT = 0x05
T_ACK = 0x81
T_NACK = 0x82

ERR_CRC = 1
ERR_LEN = 2
ERR_OVER_LIMIT = 3
ERR_UNKNOWN = 4

MAX_PAYLOAD = 32


def crc8(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x31) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def pack_bits(cells) -> bytes:
    """144개 bool -> 18바이트. cell i 는 byte i>>3 의 bit i&7."""
    if len(cells) != N_CELLS:
        raise ValueError(f"need {N_CELLS} cells, got {len(cells)}")
    out = bytearray(N_BYTES)
    for i, on in enumerate(cells):
        if on:
            out[i >> 3] |= 1 << (i & 7)
    return bytes(out)


def unpack_bits(payload: bytes):
    if len(payload) != N_BYTES:
        raise ValueError(f"need {N_BYTES} bytes, got {len(payload)}")
    return [bool((payload[i >> 3] >> (i & 7)) & 1) for i in range(N_CELLS)]


def encode(ptype: int, payload: bytes = b"", seq: int = 0, sync: int = SYNC_H2D) -> bytes:
    body = bytes([ptype, seq & 0xFF, len(payload)]) + payload
    return bytes([sync]) + body + bytes([crc8(body)])


class Parser:
    """수신 바이트 스트림에서 패킷을 점진적으로 복원한다 (호스트/MockLink 공용)."""

    def __init__(self, sync: int):
        self.sync = sync
        self.buf = bytearray()
        self.crc_errors = 0

    def feed(self, data: bytes):
        """수신 바이트를 넣고 완성된 패킷 리스트 [(type, seq, payload)] 를 돌려준다."""
        self.buf.extend(data)
        packets = []
        while True:
            # SYNC 정렬
            while self.buf and self.buf[0] != self.sync:
                self.buf.pop(0)
            if len(self.buf) < 5:
                return packets
            plen = self.buf[3]
            if plen > MAX_PAYLOAD:
                self.buf.pop(0)          # 비정상 길이 — SYNC 오인, 재정렬
                continue
            total = 4 + plen + 1
            if len(self.buf) < total:
                return packets
            body = bytes(self.buf[1:4 + plen])
            crc = self.buf[4 + plen]
            if crc8(body) == crc:
                packets.append((body[0], body[1], body[3:3 + plen]))
                del self.buf[:total]
            else:
                self.crc_errors += 1
                self.buf.pop(0)          # 한 바이트 버리고 재동기화
