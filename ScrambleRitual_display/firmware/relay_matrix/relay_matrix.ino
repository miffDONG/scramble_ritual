/*
 * Scramble Ritual — 144채널 릴레이 매트릭스 펌웨어
 * 대상: Arduino Uno/Nano (ATmega328P), 74HC595 x18 데이지체인
 *
 * 배선 (Uno 기준):
 *   D11 (MOSI) -> 74HC595 #1 DS(SER, 14번 핀)
 *   D13 (SCK)  -> 모든 74HC595 SHCP(11번 핀) 공통
 *   D10        -> 모든 74HC595 STCP(RCLK, 12번 핀) 공통  [래치]
 *   D9         -> 모든 74HC595 OE(13번 핀) 공통          [LOW=출력 활성]
 *   각 595 출력 -> ULN2803 입력 -> 릴레이 코일 (코일 전원은 SMPS 5V 레일)
 *   MR(10번 핀)은 VCC 고정. 코일 레일과 로직 GND는 반드시 공통 접지.
 *
 * 전송 순서: buf[17]부터 먼저 보낸다 -> buf[0]이 MCU에서 가장 가까운 칩에 남는다.
 *   MSBFIRST 기준 buf[k]의 bit7 -> 해당 칩 QH, bit0 -> QA.
 *   실제 배선과 논리 셀(cell i = byte i>>3, bit i&7)의 대응은
 *   테스트 패턴 4(1칸 워크)를 돌려 확인하고, 어긋나면 REMAP 테이블을 채울 것.
 *
 * 프로토콜: conductor/protocol.py 와 1:1 동일 (변경 시 양쪽 함께 수정)
 *   수신 [0xA5][TYPE][SEQ][LEN][PAYLOAD...][CRC8]
 *   송신 [0x5A][TYPE][SEQ][LEN][PAYLOAD...][CRC8]
 *
 * 안전 장치:
 *   - 부팅 직후: 0을 시프트해 래치한 뒤에야 OE 활성 (기동 글리치 방지)
 *   - 프레임 워치독: FRAME_TIMEOUT_MS 동안 유효 패킷 없으면 전 채널 OFF
 *   - 동시 ON 상한: maxOn 초과 프레임은 래치하지 않고 NACK
 *   - AVR 하드웨어 워치독 2초: MCU 행 자체를 리셋으로 복구
 */

#include <SPI.h>
#include <avr/wdt.h>

const uint8_t PIN_LATCH = 10;
const uint8_t PIN_OE    = 9;

const uint8_t  N_BYTES = 18;
const uint16_t N_CELLS = 144;
const uint32_t BAUD = 115200;
const uint32_t FRAME_TIMEOUT_MS = 1500;
const uint16_t TEST_STEP_MS = 150;

// 프로토콜 상수 — conductor/protocol.py 와 동일
const uint8_t SYNC_H2D = 0xA5, SYNC_D2H = 0x5A;
const uint8_t T_FRAME = 0x01, T_ALL_OFF = 0x02, T_PING = 0x03,
              T_TEST = 0x04, T_LIMIT = 0x05, T_ACK = 0x81, T_NACK = 0x82;
const uint8_t ERR_CRC = 1, ERR_LEN = 2, ERR_OVER_LIMIT = 3, ERR_UNKNOWN = 4;
const uint8_t MAX_PAYLOAD = 32;

uint8_t  frameBuf[N_BYTES];        // 현재 래치된 출력 이미지
uint8_t  maxOn = 72;               // 동시 ON 상한 (T_LIMIT으로 조정)
uint32_t lastValidMs = 0;          // 워치독 기준 시각
bool     safeTripped = false;      // 워치독 발동 이력 (ACK flags bit0로 보고)
uint8_t  testMode = 0;             // 0=비활성, 1~4=내장 패턴
uint16_t testStep = 0;
uint32_t testLastMs = 0;

// ── CRC8 (poly 0x31) — 호스트와 동일 ─────────────────────────────
uint8_t crc8(const uint8_t *d, uint8_t n) {
  uint8_t c = 0;
  while (n--) {
    c ^= *d++;
    for (uint8_t i = 0; i < 8; i++)
      c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x31) : (uint8_t)(c << 1);
  }
  return c;
}

// ── 시프트 아웃: buf[17] 먼저, 마지막에 일괄 래치 ─────────────────
void shiftOutAll() {
  SPI.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
  for (int8_t k = N_BYTES - 1; k >= 0; k--) SPI.transfer(frameBuf[k]);
  SPI.endTransaction();
  digitalWrite(PIN_LATCH, HIGH);   // 144채널 동시 래치 — 글리치 없는 전환
  digitalWrite(PIN_LATCH, LOW);
}

void allOff() {
  memset(frameBuf, 0, N_BYTES);
  shiftOutAll();
}

uint8_t countOn(const uint8_t *p) {
  uint8_t n = 0;
  for (uint8_t k = 0; k < N_BYTES; k++) {
    uint8_t b = p[k];
    while (b) { n += b & 1; b >>= 1; }
  }
  return n;
}

// ── 응답 송신 ────────────────────────────────────────────────────
void reply(uint8_t type, uint8_t seq, const uint8_t *pl, uint8_t plen) {
  uint8_t body[3 + 4];
  body[0] = type; body[1] = seq; body[2] = plen;
  for (uint8_t i = 0; i < plen; i++) body[3 + i] = pl[i];
  Serial.write(SYNC_D2H);
  Serial.write(body, 3 + plen);
  Serial.write(crc8(body, 3 + plen));
}

void ack(uint8_t seq) {
  uint8_t pl[2] = { countOn(frameBuf), (uint8_t)(safeTripped ? 1 : 0) };
  safeTripped = false;             // 보고했으면 플래그 해제
  reply(T_ACK, seq, pl, 2);
}

void nack(uint8_t seq, uint8_t err) { reply(T_NACK, seq, &err, 1); }

// ── 명령 처리 ────────────────────────────────────────────────────
void handle(uint8_t type, uint8_t seq, const uint8_t *pl, uint8_t plen) {
  switch (type) {
    case T_FRAME: {
      if (plen != N_BYTES) { nack(seq, ERR_LEN); return; }
      if (countOn(pl) > maxOn) { nack(seq, ERR_OVER_LIMIT); return; }
      testMode = 0;
      memcpy(frameBuf, pl, N_BYTES);
      shiftOutAll();
      lastValidMs = millis();
      ack(seq);
      return;
    }
    case T_ALL_OFF:
      testMode = 0;
      allOff();
      lastValidMs = millis();
      ack(seq);
      return;
    case T_PING:
      lastValidMs = millis();
      ack(seq);
      return;
    case T_TEST:
      if (plen != 1 || pl[0] > 4) { nack(seq, ERR_LEN); return; }
      testMode = pl[0];
      testStep = 0;
      testLastMs = 0;
      lastValidMs = millis();
      if (testMode == 0) allOff();
      ack(seq);
      return;
    case T_LIMIT:
      if (plen != 1 || pl[0] < 1) { nack(seq, ERR_LEN); return; }
      maxOn = pl[0];
      lastValidMs = millis();
      ack(seq);
      return;
    default:
      nack(seq, ERR_UNKNOWN);
  }
}

// ── 수신 파서 (상태기계) ─────────────────────────────────────────
enum RxState { RX_SYNC, RX_TYPE, RX_SEQ, RX_LEN, RX_PAYLOAD, RX_CRC };
RxState rxState = RX_SYNC;
uint8_t rxType, rxSeq, rxLen, rxIdx;
uint8_t rxPl[MAX_PAYLOAD];

void feed(uint8_t b) {
  switch (rxState) {
    case RX_SYNC:    if (b == SYNC_H2D) rxState = RX_TYPE; break;
    case RX_TYPE:    rxType = b; rxState = RX_SEQ; break;
    case RX_SEQ:     rxSeq = b; rxState = RX_LEN; break;
    case RX_LEN:
      if (b > MAX_PAYLOAD) { rxState = RX_SYNC; break; }
      rxLen = b; rxIdx = 0;
      rxState = rxLen ? RX_PAYLOAD : RX_CRC;
      break;
    case RX_PAYLOAD:
      rxPl[rxIdx++] = b;
      if (rxIdx >= rxLen) rxState = RX_CRC;
      break;
    case RX_CRC: {
      uint8_t body[3 + MAX_PAYLOAD];
      body[0] = rxType; body[1] = rxSeq; body[2] = rxLen;
      memcpy(body + 3, rxPl, rxLen);
      if (crc8(body, 3 + rxLen) == b) handle(rxType, rxSeq, rxPl, rxLen);
      else nack(rxSeq, ERR_CRC);
      rxState = RX_SYNC;
      break;
    }
  }
}

// ── 내장 테스트 패턴 (호스트 없이 배선 검증용) ────────────────────
void runTestPattern() {
  uint32_t now = millis();
  if (now - testLastMs < TEST_STEP_MS) return;
  testLastMs = now;
  memset(frameBuf, 0, N_BYTES);
  switch (testMode) {
    case 1: {                                  // 행 스윕 (12셀씩)
      uint8_t row = testStep % 12;
      for (uint8_t c = 0; c < 12; c++) {
        uint16_t i = row * 12 + c;
        frameBuf[i >> 3] |= 1 << (i & 7);
      }
      break;
    }
    case 2: {                                  // 열 스윕 (12셀씩)
      uint8_t col = testStep % 12;
      for (uint8_t r = 0; r < 12; r++) {
        uint16_t i = r * 12 + col;
        frameBuf[i >> 3] |= 1 << (i & 7);
      }
      break;
    }
    case 3: {                                  // 체커보드 토글 (72셀)
      uint8_t phase = testStep & 1;
      for (uint16_t i = 0; i < N_CELLS; i++) {
        uint8_t r = i / 12, c = i % 12;
        if (((r + c) & 1) == phase) frameBuf[i >> 3] |= 1 << (i & 7);
      }
      break;
    }
    case 4: {                                  // 1칸 워크 (매핑 확인용)
      uint16_t i = testStep % N_CELLS;
      frameBuf[i >> 3] |= 1 << (i & 7);
      break;
    }
  }
  if (countOn(frameBuf) > maxOn) memset(frameBuf, 0, N_BYTES);
  shiftOutAll();
  testStep++;
  lastValidMs = now;                           // 테스트 중엔 워치독 유예
}

// ── 셋업 / 루프 ──────────────────────────────────────────────────
void setup() {
  wdt_disable();
  pinMode(PIN_OE, OUTPUT);
  digitalWrite(PIN_OE, HIGH);                  // 출력 비활성 상태로 시작
  pinMode(PIN_LATCH, OUTPUT);
  digitalWrite(PIN_LATCH, LOW);
  SPI.begin();
  allOff();                                    // 0을 래치한 뒤
  digitalWrite(PIN_OE, LOW);                   // 출력 활성 — 글리치 없음
  Serial.begin(BAUD);
  lastValidMs = millis();
  wdt_enable(WDTO_2S);
}

void loop() {
  wdt_reset();
  while (Serial.available()) feed(Serial.read());
  if (testMode) runTestPattern();
  // 프레임 워치독: 호스트 침묵 시 안전 정지
  if (millis() - lastValidMs > FRAME_TIMEOUT_MS) {
    if (countOn(frameBuf) > 0) { allOff(); safeTripped = true; }
    lastValidMs = millis() - FRAME_TIMEOUT_MS;  // 오버플로 방지용 고정
  }
}
