"""
Scramble Ritual — 링크 레이어

SerialLink : pyserial 로 실제 아두이노와 통신 (ACK 대기, 1회 재전송)
MockLink   : 펌웨어의 패킷 처리 로직을 파이썬으로 재현한 가짜 보드.
             인코딩된 '실제 바이트'를 펌웨어와 같은 규칙으로 파싱하므로
             하드웨어 없이 프로토콜 전 구간을 검증할 수 있다.

두 클래스 모두 동일 인터페이스:
    send_frame(cells) -> Ack
    all_off() / ping() / test(n) / set_limit(n) -> Ack
"""

import time
from dataclasses import dataclass

from . import protocol as P


@dataclass
class Ack:
    ok: bool
    seq: int = 0
    on_count: int = 0
    flags: int = 0
    err: int = 0


class _Base:
    def __init__(self):
        self.seq = 0

    def _next_seq(self):
        self.seq = (self.seq + 1) & 0xFF
        return self.seq

    def send_frame(self, cells):
        return self._roundtrip(P.T_FRAME, P.pack_bits(cells))

    def all_off(self):
        return self._roundtrip(P.T_ALL_OFF)

    def ping(self):
        return self._roundtrip(P.T_PING)

    def test(self, pattern: int):
        return self._roundtrip(P.T_TEST, bytes([pattern]))

    def set_limit(self, max_on: int):
        return self._roundtrip(P.T_LIMIT, bytes([max_on]))


class MockLink(_Base):
    """펌웨어 동작의 파이썬 레퍼런스 구현 (relay_matrix.ino 와 의미 동일)."""

    def __init__(self, max_on=72):
        super().__init__()
        self.max_on = max_on
        self.latched = [False] * P.N_CELLS
        self._parser = P.Parser(P.SYNC_H2D)

    def _roundtrip(self, ptype, payload=b""):
        seq = self._next_seq()
        wire = P.encode(ptype, payload, seq)          # 실제 인코딩 경로 사용
        packets = self._parser.feed(wire)             # 실제 파싱 경로 사용
        if not packets:
            return Ack(ok=False, seq=seq, err=P.ERR_CRC)
        t, rseq, pl = packets[-1]
        return self._handle(t, rseq, pl)

    def _handle(self, t, seq, pl):
        if t == P.T_FRAME:
            if len(pl) != P.N_BYTES:
                return Ack(False, seq, err=P.ERR_LEN)
            cells = P.unpack_bits(pl)
            n_on = sum(cells)
            if n_on > self.max_on:
                return Ack(False, seq, err=P.ERR_OVER_LIMIT)
            self.latched = cells
            return Ack(True, seq, on_count=n_on)
        if t == P.T_ALL_OFF:
            self.latched = [False] * P.N_CELLS
            return Ack(True, seq, on_count=0)
        if t == P.T_PING:
            return Ack(True, seq, on_count=sum(self.latched))
        if t == P.T_TEST:
            return Ack(True, seq, on_count=sum(self.latched))
        if t == P.T_LIMIT:
            if len(pl) == 1 and 1 <= pl[0] <= P.N_CELLS:
                self.max_on = pl[0]
                return Ack(True, seq)
            return Ack(False, seq, err=P.ERR_LEN)
        return Ack(False, seq, err=P.ERR_UNKNOWN)


class SerialLink(_Base):
    """실보드 연결. pyserial 필요: pip install pyserial"""

    def __init__(self, port, baud=115200, ack_timeout=0.25, retries=1):
        super().__init__()
        import serial                                  # 지연 임포트 — 시뮬레이션은 불필요
        self.ser = serial.Serial(port, baud, timeout=0.02)
        self.ack_timeout = ack_timeout
        self.retries = retries
        self._parser = P.Parser(P.SYNC_D2H)
        time.sleep(2.0)                                # Uno 계열 자동 리셋 대기
        self.ser.reset_input_buffer()

    def _roundtrip(self, ptype, payload=b""):
        seq = self._next_seq()
        wire = P.encode(ptype, payload, seq)
        for _ in range(1 + self.retries):
            self.ser.write(wire)
            ack = self._wait_ack(seq)
            if ack is not None:
                return ack
        return Ack(ok=False, seq=seq, err=P.ERR_CRC)   # 응답 없음

    def _wait_ack(self, seq):
        deadline = time.monotonic() + self.ack_timeout
        while time.monotonic() < deadline:
            data = self.ser.read(64)
            if not data:
                continue
            for t, rseq, pl in self._parser.feed(data):
                if rseq != seq:
                    continue                           # 이전 패킷의 늦은 ACK 무시
                if t == P.T_ACK and len(pl) >= 1:
                    flags = pl[1] if len(pl) >= 2 else 0
                    return Ack(True, rseq, on_count=pl[0], flags=flags)
                if t == P.T_NACK and len(pl) >= 1:
                    return Ack(False, rseq, err=pl[0])
        return None

    def close(self):
        try:
            self.all_off()
        finally:
            self.ser.close()
