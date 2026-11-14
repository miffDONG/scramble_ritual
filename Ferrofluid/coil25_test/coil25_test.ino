// coil25_test.ino — 25채널 배선 확인용 (Arduino Mega 2560). PC·OSC 없이 Mega 혼자 동작.
//
// 배선 (보드 5개 x 코일 5개, JST 6핀 = 신호 5 + GND 1)
//   보드 1: CH1~CH5   = D22 D23 D24 D25 D26
//   보드 2: CH6~CH10  = D27 D28 D29 D30 D31
//   보드 3: CH11~CH15 = D32 D33 D34 D35 D36
//   보드 4: CH16~CH20 = D37 D38 D39 D40 D41
//   보드 5: CH21~CH25 = D42 D43 D44 D45 D46
//
// 한 사이클 (약 30초, 무한 반복)
//   1) CH1 → CH25 하나씩 0.6초 켜기   : 코일이 1번부터 25번까지 순서대로 움직여야 정상
//   2) 보드1 → 보드5, 보드 하나씩 5개 동시 1.5초 : 보드 단위로 전원이 버티는지
//   3) 전부 끄고 3초 휴식              : 이때 움직이는 코일이 있으면 이상
// 25개 전체를 한꺼번에 켜는 단계는 일부러 뺐음 (SMPS 과부하 방지)
//
// 시리얼 모니터(115200)에 지금 켜진 채널 표시.

const uint8_t NCH = 25;
const uint8_t PINS[NCH] = {22,23,24,25,26, 27,28,29,30,31, 32,33,34,35,36,
                           37,38,39,40,41, 42,43,44,45,46};

const unsigned long ONE_ON_MS   = 600;
const unsigned long ONE_GAP_MS  = 200;
const unsigned long BOARD_ON_MS = 1500;
const unsigned long BOARD_GAP_MS = 500;
const unsigned long REST_MS     = 3000;

unsigned long cycleCount = 0;

void allOff() {
  for (uint8_t i = 0; i < NCH; i++) digitalWrite(PINS[i], LOW);
  digitalWrite(LED_BUILTIN, LOW);
}

void setup() {
  for (uint8_t i = 0; i < NCH; i++) {
    digitalWrite(PINS[i], LOW);
    pinMode(PINS[i], OUTPUT);
  }
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  Serial.println(F("coil25_test 시작 — D22~D46"));
}

void loop() {
  cycleCount++;
  Serial.print(F("\n=== 사이클 "));
  Serial.print(cycleCount);
  Serial.println(F(" ==="));

  // 1) 한 채널씩
  for (uint8_t i = 0; i < NCH; i++) {
    Serial.print(F("보드"));
    Serial.print(i / 5 + 1);
    Serial.print(F("  CH"));
    Serial.print(i + 1);
    Serial.print(F(" (D"));
    Serial.print(PINS[i]);
    Serial.println(F(") ON"));
    digitalWrite(PINS[i], HIGH);
    digitalWrite(LED_BUILTIN, HIGH);
    delay(ONE_ON_MS);
    allOff();
    delay(ONE_GAP_MS);
  }

  // 2) 보드 단위 5개 동시
  for (uint8_t b = 0; b < 5; b++) {
    Serial.print(F("보드"));
    Serial.print(b + 1);
    Serial.println(F(" 5개 동시 ON"));
    for (uint8_t k = 0; k < 5; k++) digitalWrite(PINS[b * 5 + k], HIGH);
    digitalWrite(LED_BUILTIN, HIGH);
    delay(BOARD_ON_MS);
    allOff();
    delay(BOARD_GAP_MS);
  }

  // 3) 휴식
  Serial.println(F("ALL OFF"));
  delay(REST_MS);
}
