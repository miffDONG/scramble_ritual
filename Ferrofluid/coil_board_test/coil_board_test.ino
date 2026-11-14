// coil_board_test.ino — 드라이버 보드 · 전선 · 코일 세트 검증용 (Arduino Mega 2560)
//
// 핀: D22, D23, D24, D25, D26 (CH1~CH5) + GND 1개. PC·OSC 없이 Mega 혼자 동작.
//
// 한 사이클 (약 11초, 무한 반복)
//   1) 순서 확인 : CH1 → CH2 → CH3 → CH4 → CH5 를 하나씩 1초 켜고 0.3초 쉼
//                  (플라스크 1~5번이 순서대로 하나씩 움직여야 정상)
//   2) 동시 확인 : 5개 전부 2초 동시에 켬 (SMPS가 5개를 같이 버티는지)
//   3) 휴식      : 전부 2초 끔 (이때 움직이는 플라스크가 있으면 이상)
//
// 시리얼 모니터(115200)에 지금 어느 채널이 켜졌는지 표시.

const uint8_t PINS[5] = {22, 23, 24, 25, 26};   // CH1 ~ CH5

const unsigned long ONE_ON_MS  = 1000;   // 1) 한 채널씩 켜는 시간
const unsigned long ONE_GAP_MS = 300;    // 1) 채널 사이 쉬는 시간
const unsigned long ALL_ON_MS  = 2000;   // 2) 5개 동시에 켜는 시간
const unsigned long REST_MS    = 2000;   // 3) 전부 끄고 쉬는 시간

unsigned long cycleCount = 0;

void allOff() {
  for (uint8_t i = 0; i < 5; i++) digitalWrite(PINS[i], LOW);
  digitalWrite(LED_BUILTIN, LOW);
}

void setup() {
  for (uint8_t i = 0; i < 5; i++) {
    digitalWrite(PINS[i], LOW);    // 켜지기 전에 먼저 LOW
    pinMode(PINS[i], OUTPUT);
  }
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  Serial.println(F("coil_board_test 시작 — D22~D26"));
}

void loop() {
  cycleCount++;
  Serial.print(F("\n=== 사이클 "));
  Serial.print(cycleCount);
  Serial.println(F(" ==="));

  // 1) 한 채널씩
  for (uint8_t i = 0; i < 5; i++) {
    Serial.print(F("CH"));
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

  // 2) 5개 동시에
  Serial.println(F("ALL ON (CH1~CH5)"));
  for (uint8_t i = 0; i < 5; i++) digitalWrite(PINS[i], HIGH);
  digitalWrite(LED_BUILTIN, HIGH);
  delay(ALL_ON_MS);

  // 3) 휴식
  allOff();
  Serial.println(F("ALL OFF"));
  delay(REST_MS);
}
