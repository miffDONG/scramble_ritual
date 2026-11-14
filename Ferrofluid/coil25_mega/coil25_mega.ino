// coil25_mega.ino  —  Scramble Ritual · 전자석 25채널 드라이버 (Arduino Mega 2560)
//
// 수신 (USB Serial 115200):
//   "H" + 16진수 2글자 x 25 + "\n"   예) H00FF80...  = CH1~CH25 세기 (00~FF = 0~255)
//   "X\n"   전 채널 즉시 OFF
//   "?\n"   상태 출력
//
// 배선 (드라이버 보드 5개 x 코일 5개). 각 보드 JST 6핀 = 신호 5 + GND 1
//   보드 1:  CH1 =D22  CH2 =D23  CH3 =D24  CH4 =D25  CH5 =D26
//   보드 2:  CH6 =D27  CH7 =D28  CH8 =D29  CH9 =D30  CH10=D31
//   보드 3:  CH11=D32  CH12=D33  CH13=D34  CH14=D35  CH15=D36
//   보드 4:  CH16=D37  CH17=D38  CH18=D39  CH19=D40  CH20=D41
//   보드 5:  CH21=D42  CH22=D43  CH23=D44  CH24=D45  CH25=D46
//   GND: 보드 5개 GND를 모두 Mega GND에 연결
//
// D22~D46은 하드웨어 PWM 핀이 아니므로 Timer2 인터럽트로 직접 PWM을 만든다.
//   - 약 250Hz, 64단계
//   - 25채널이 켜지는 순간을 1/25씩 어긋나게 해서 SMPS 순간 전류 피크를 분산
//
// 안전:
//   - TIMEOUT_MS 동안 유효 프레임이 없으면 전 채널 OFF
//   - 리셋·업로드 중에는 핀이 입력 상태 -> 게이트 10kΩ 풀다운이 OFF 유지

#include <Arduino.h>

// ================= 설정 =================
const uint8_t  HW_MAX_LEVEL = 255;            // 펌웨어 단 최대 출력 (0~255)
const uint32_t CH_ENABLE    = 0x1FFFFFFUL;    // 사용할 채널 (비트0=CH1 ... 비트24=CH25). 전부 사용
const unsigned long TIMEOUT_MS = 300;
// =======================================

const uint8_t NCH = 25;
const uint8_t PINS[NCH] = {22,23,24,25,26, 27,28,29,30,31, 32,33,34,35,36, 37,38,39,40,41, 42,43,44,45,46};

volatile uint8_t swDuty[NCH];      // 0~64 (64 = 상시 ON)
uint8_t level[NCH];                // 마지막으로 적용한 0~255

unsigned long lastFrameMs = 0;
bool timedOut = true;
unsigned long frames = 0, badFrames = 0;

char line[64];
uint8_t lineLen = 0;
bool lineOverflow = false;

// Timer2 CTC: 16MHz / 8 / (124+1) = 16kHz 틱, 64틱 = 1주기 -> 250Hz
ISR(TIMER2_COMPA_vect) {
  static uint8_t ph = 0;
  ph = (ph + 1) & 63;
  uint8_t a = 0, c = 0, d = 0, g = 0, l = 0;
  if (((uint8_t)(ph +  0) & 63) < swDuty[ 0]) a |= 0x01;   // CH1  D22
  if (((uint8_t)(ph +  3) & 63) < swDuty[ 1]) a |= 0x02;   // CH2  D23
  if (((uint8_t)(ph +  5) & 63) < swDuty[ 2]) a |= 0x04;   // CH3  D24
  if (((uint8_t)(ph +  8) & 63) < swDuty[ 3]) a |= 0x08;   // CH4  D25
  if (((uint8_t)(ph + 10) & 63) < swDuty[ 4]) a |= 0x10;   // CH5  D26
  if (((uint8_t)(ph + 13) & 63) < swDuty[ 5]) a |= 0x20;   // CH6  D27
  if (((uint8_t)(ph + 15) & 63) < swDuty[ 6]) a |= 0x40;   // CH7  D28
  if (((uint8_t)(ph + 18) & 63) < swDuty[ 7]) a |= 0x80;   // CH8  D29
  if (((uint8_t)(ph + 20) & 63) < swDuty[ 8]) c |= 0x80;   // CH9  D30
  if (((uint8_t)(ph + 23) & 63) < swDuty[ 9]) c |= 0x40;   // CH10 D31
  if (((uint8_t)(ph + 26) & 63) < swDuty[10]) c |= 0x20;   // CH11 D32
  if (((uint8_t)(ph + 28) & 63) < swDuty[11]) c |= 0x10;   // CH12 D33
  if (((uint8_t)(ph + 31) & 63) < swDuty[12]) c |= 0x08;   // CH13 D34
  if (((uint8_t)(ph + 33) & 63) < swDuty[13]) c |= 0x04;   // CH14 D35
  if (((uint8_t)(ph + 36) & 63) < swDuty[14]) c |= 0x02;   // CH15 D36
  if (((uint8_t)(ph + 38) & 63) < swDuty[15]) c |= 0x01;   // CH16 D37
  if (((uint8_t)(ph + 41) & 63) < swDuty[16]) d |= 0x80;   // CH17 D38
  if (((uint8_t)(ph + 44) & 63) < swDuty[17]) g |= 0x04;   // CH18 D39
  if (((uint8_t)(ph + 46) & 63) < swDuty[18]) g |= 0x02;   // CH19 D40
  if (((uint8_t)(ph + 49) & 63) < swDuty[19]) g |= 0x01;   // CH20 D41
  if (((uint8_t)(ph + 51) & 63) < swDuty[20]) l |= 0x80;   // CH21 D42
  if (((uint8_t)(ph + 54) & 63) < swDuty[21]) l |= 0x40;   // CH22 D43
  if (((uint8_t)(ph + 56) & 63) < swDuty[22]) l |= 0x20;   // CH23 D44
  if (((uint8_t)(ph + 59) & 63) < swDuty[23]) l |= 0x10;   // CH24 D45
  if (((uint8_t)(ph + 61) & 63) < swDuty[24]) l |= 0x08;   // CH25 D46
  PORTA = a;                                  // D22~D29 (PA0~PA7 전부 사용)
  PORTC = c;                                  // D30~D37 (PC7~PC0 전부 사용)
  PORTD = (PORTD & 0x7F) | d;                 // D38 = PD7
  PORTG = (PORTG & 0xF8) | g;                 // D39~D41 = PG2~PG0
  PORTL = (PORTL & 0x07) | l;                 // D42~D46 = PL7~PL3
}

void applyLevel(uint8_t ch, uint8_t v) {
  if (!((CH_ENABLE >> ch) & 1UL)) v = 0;
  if (v > HW_MAX_LEVEL) v = HW_MAX_LEVEL;
  level[ch] = v;
  swDuty[ch] = (uint8_t)(((uint16_t)v * 64 + 127) / 255);   // 0~255 -> 0~64
}

void allOff() {
  for (uint8_t i = 0; i < NCH; i++) applyLevel(i, 0);
}

int8_t hexVal(char ch) {
  if (ch >= '0' && ch <= '9') return ch - '0';
  if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
  if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
  return -1;
}

void printStatus() {
  Serial.print(F("OK coil25_mega D22-D46 250Hz max="));
  Serial.print(HW_MAX_LEVEL);
  Serial.print(F(" frames="));  Serial.print(frames);
  Serial.print(F(" bad="));     Serial.print(badFrames);
  Serial.print(F(" timeout=")); Serial.println(timedOut ? 1 : 0);
  for (uint8_t b = 0; b < 5; b++) {
    Serial.print(F("  board")); Serial.print(b + 1); Serial.print(':');
    for (uint8_t k = 0; k < 5; k++) { Serial.print(' '); Serial.print(level[b * 5 + k]); }
    Serial.println();
  }
}

void handleLine(const char *s, uint8_t n) {
  if (n == 1 && s[0] == '?') { printStatus(); return; }
  if (n == 1 && s[0] == 'X') { allOff(); timedOut = true; return; }
  if (n != 1 + 2 * NCH || s[0] != 'H') { badFrames++; return; }
  uint8_t vals[NCH];
  for (uint8_t i = 0; i < NCH; i++) {
    int8_t hi = hexVal(s[1 + 2 * i]), lo = hexVal(s[2 + 2 * i]);
    if (hi < 0 || lo < 0) { badFrames++; return; }       // 한 글자라도 틀리면 프레임 전체 버림
    vals[i] = (uint8_t)((hi << 4) | lo);
  }
  for (uint8_t i = 0; i < NCH; i++) applyLevel(i, vals[i]);
  lastFrameMs = millis();
  timedOut = false;
  frames++;
}

void setup() {
  for (uint8_t i = 0; i < NCH; i++) {
    digitalWrite(PINS[i], LOW);       // 출력으로 바꾸기 전에 LOW부터
    pinMode(PINS[i], OUTPUT);
  }
  allOff();
  cli();
  TCCR2A = _BV(WGM21);                // CTC
  TCCR2B = _BV(CS21);                 // 분주 8
  OCR2A  = 124;                       // 16kHz
  TCNT2  = 0;
  TIMSK2 = _BV(OCIE2A);
  sei();
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}

void loop() {
  while (Serial.available() > 0) {
    char ch = (char)Serial.read();
    if (ch == '\n') {
      if (!lineOverflow && lineLen > 0) handleLine(line, lineLen);
      lineLen = 0;
      lineOverflow = false;
    } else if (ch == '\r') {
      // 무시
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = ch;
    } else if (!lineOverflow) {
      lineOverflow = true;
      badFrames++;
    }
  }

  if (!timedOut && millis() - lastFrameMs > TIMEOUT_MS) {
    allOff();
    timedOut = true;
  }

  bool any = false;
  for (uint8_t i = 0; i < NCH; i++) if (level[i]) { any = true; break; }
  digitalWrite(LED_BUILTIN, any ? HIGH : LOW);     // D13 LED = 출력 중
}
