// coil5_mega.ino  —  Scramble Ritual · 전자석 5채널 드라이버 (Arduino Mega 2560)
//
// 수신 (USB Serial 115200):
//   "M,v1,v2,v3,v4,v5\n"   v = 0~255 (CH1~CH5 출력 세기)
//   "X\n"                   전 채널 즉시 OFF
//   "?\n"                   상태 출력
//
// 배선 (현재 상태 그대로):
//   CH1 = D22, CH2 = D23, CH3 = D24, CH4 = D25, CH5 = D26  -> IRLZ44N 게이트 (10kΩ 풀다운)
//
// [중요] D22~D26은 Mega의 하드웨어 PWM 핀이 아님.
//   analogWrite()를 쓰면 128 미만은 OFF, 128 이상은 ON이 되는 2단계 동작만 나옴.
//   그래서 PWM_MODE 0 에서는 Timer2 인터럽트로 직접 PWM을 만든다 (D22~D26 = PORTA 비트 0~4).
//   - PWM 주파수 약 248Hz, 128단계 해상도
//   - 5채널 위상을 1/5씩 어긋나게 켜서, 다섯 코일이 동시에 켜질 때 생기는 SMPS 전류 피크를 분산
//
// PWM_MODE 1 은 신호선을 하드웨어 PWM 핀 D2, D3, D5, D6, D7로 옮겼을 때만 사용.
//
// 안전:
//   - TIMEOUT_MS 동안 유효 프레임이 안 오면 전 채널 OFF (브리지 종료·USB 분리 대비)
//   - 리셋·부트로더 구간에는 핀이 입력(High-Z) 상태 -> 10kΩ 풀다운이 게이트를 LOW로 붙잡아 OFF 유지

#include <Arduino.h>
#include <stdlib.h>

// ================= 설정 =================
#define PWM_MODE 0                 // 0 = 소프트웨어 PWM (D22~D26, 현재 배선)
                                   // 1 = 하드웨어 PWM (D2, D3, D5, D6, D7로 재배선했을 때)

const uint8_t  HW_MAX_LEVEL = 255; // 펌웨어 단 최대 출력 상한 (0~255). 255 = SMPS 풀 출력

// 사용할 채널 (비트0 = CH1/D22 ... 비트4 = CH5/D26). 꺼진 채널은 무슨 값이 와도 OFF.
// [임시 3채널] D23·D24·D25만 사용. 5채널로 되돌릴 때 0b11111
const uint8_t  CH_ENABLE_MASK = 0b01110;
const unsigned long TIMEOUT_MS = 300;

// PWM_MODE 1 전용: 1 = 31.4kHz (비권장: 게이트 드라이버 없이 IRLZ44N 스위칭 발열 큼)
//                  2 = 3.92kHz (권장)   3 = 490Hz (아두이노 기본값)
#define HW_PWM_PRESCALE 2
const uint8_t HW_PINS[5] = {2, 3, 5, 6, 7};
// =======================================

const uint8_t NCH = 5;
const uint8_t PORTA_MASK = 0x1F;          // PA0~PA4 = D22~D26

volatile uint8_t swDuty[NCH] = {0, 0, 0, 0, 0};   // 0~128 (128 = 상시 ON)
uint8_t level[NCH] = {0, 0, 0, 0, 0};              // 마지막으로 적용한 0~255 값

unsigned long lastFrameMs = 0;
bool timedOut = true;
unsigned long frames = 0, badFrames = 0;

char line[48];
uint8_t lineLen = 0;
bool lineOverflow = false;

#if PWM_MODE == 0
// Timer2 CTC: 16MHz / 8 / (62+1) = 31.746kHz 틱, 128틱 = 1주기 -> 약 248Hz
ISR(TIMER2_COMPA_vect) {
  static uint8_t ph = 0;
  ph = (ph + 1) & 0x7F;
  uint8_t out = 0;
  if (((uint8_t)(ph      ) & 0x7F) < swDuty[0]) out |= 0x01;   // D22
  if (((uint8_t)(ph +  26) & 0x7F) < swDuty[1]) out |= 0x02;   // D23
  if (((uint8_t)(ph +  51) & 0x7F) < swDuty[2]) out |= 0x04;   // D24
  if (((uint8_t)(ph +  77) & 0x7F) < swDuty[3]) out |= 0x08;   // D25
  if (((uint8_t)(ph + 102) & 0x7F) < swDuty[4]) out |= 0x10;   // D26
  PORTA = (PORTA & (uint8_t)~PORTA_MASK) | out;
}
#endif

void applyLevel(uint8_t ch, uint8_t v) {
  if (!((CH_ENABLE_MASK >> ch) & 1)) v = 0;
  if (v > HW_MAX_LEVEL) v = HW_MAX_LEVEL;
  level[ch] = v;
#if PWM_MODE == 0
  swDuty[ch] = (uint8_t)(((uint16_t)v * 128 + 127) / 255);  // 0~255 -> 0~128
#else
  analogWrite(HW_PINS[ch], v);
#endif
}

void allOff() {
  for (uint8_t i = 0; i < NCH; i++) applyLevel(i, 0);
}

void printStatus() {
  Serial.print(F("OK coil5_mega mode="));
#if PWM_MODE == 0
  Serial.print(F("SW(D22-26,248Hz)"));
#else
  Serial.print(F("HW(D2,3,5,6,7)"));
#endif
  Serial.print(F(" max="));      Serial.print(HW_MAX_LEVEL);
  Serial.print(F(" enable=0b"));
  for (int8_t i = NCH - 1; i >= 0; i--) Serial.print((CH_ENABLE_MASK >> i) & 1);
  Serial.print(F(" level="));
  for (uint8_t i = 0; i < NCH; i++) {
    Serial.print(level[i]);
    if (i < NCH - 1) Serial.print(',');
  }
  Serial.print(F(" frames="));   Serial.print(frames);
  Serial.print(F(" bad="));      Serial.print(badFrames);
  Serial.print(F(" timeout="));  Serial.println(timedOut ? 1 : 0);
}

// "M,v1,v2,v3,v4,v5" 파싱. 형식이 하나라도 틀리면 프레임 전체를 버림.
bool parseFrame(const char *s, uint8_t *vals) {
  if (s[0] != 'M' || s[1] != ',') return false;
  const char *p = s + 2;
  for (uint8_t i = 0; i < NCH; i++) {
    if (*p < '0' || *p > '9') return false;
    char *end;
    long v = strtol(p, &end, 10);
    if (end == p || v < 0 || v > 255) return false;
    vals[i] = (uint8_t)v;
    p = end;
    if (i < NCH - 1) {
      if (*p != ',') return false;
      p++;
    }
  }
  return *p == '\0';
}

void handleLine(const char *s) {
  if (s[0] == '?' && s[1] == '\0') { printStatus(); return; }
  if (s[0] == 'X' && s[1] == '\0') { allOff(); timedOut = true; return; }
  uint8_t vals[NCH];
  if (parseFrame(s, vals)) {
    for (uint8_t i = 0; i < NCH; i++) applyLevel(i, vals[i]);
    lastFrameMs = millis();
    timedOut = false;
    frames++;
  } else {
    badFrames++;
  }
}

void setup() {
#if PWM_MODE == 0
  PORTA &= (uint8_t)~PORTA_MASK;   // 출력으로 바꾸기 전에 LOW부터
  DDRA  |= PORTA_MASK;             // D22~D26 OUTPUT
  cli();
  TCCR2A = _BV(WGM21);             // CTC
  TCCR2B = _BV(CS21);              // 분주 8
  OCR2A  = 62;                     // 31.746kHz
  TCNT2  = 0;
  TIMSK2 = _BV(OCIE2A);
  sei();
#else
  for (uint8_t i = 0; i < NCH; i++) {
    digitalWrite(HW_PINS[i], LOW);
    pinMode(HW_PINS[i], OUTPUT);
  }
  TCCR3B = (TCCR3B & 0xF8) | HW_PWM_PRESCALE;   // D2, D3, D5
  TCCR4B = (TCCR4B & 0xF8) | HW_PWM_PRESCALE;   // D6, D7
#endif
  allOff();
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      if (!lineOverflow && lineLen > 0) {
        line[lineLen] = '\0';
        handleLine(line);
      }
      lineLen = 0;
      lineOverflow = false;
    } else if (c == '\r') {
      // 무시
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    } else if (!lineOverflow) {
      lineOverflow = true;          // 너무 긴 줄 -> 다음 '\n'까지 버림
      badFrames++;
    }
  }

  if (!timedOut && millis() - lastFrameMs > TIMEOUT_MS) {
    allOff();
    timedOut = true;
  }

  bool any = false;
  for (uint8_t i = 0; i < NCH; i++) if (level[i]) any = true;
  digitalWrite(LED_BUILTIN, any ? HIGH : LOW);     // D13 LED = 출력 중
}
