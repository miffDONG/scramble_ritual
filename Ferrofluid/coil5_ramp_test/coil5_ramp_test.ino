// coil5_ramp_test.ino — 전자석 5개 동시: 5초 동안 0→100%, 5초 동안 100→0%, 무한 반복
// 핀 D22~D26 그대로. PC·브리지 없이 Mega 단독으로 동작.
// 시리얼 모니터(115200)에 현재 % 표시.

const uint8_t PINS[5] = {22, 23, 24, 25, 26};
const unsigned long RAMP_MS   = 5000;   // 한 방향 5초
const unsigned long HOLD_MS   = 0;      // 100%에서 머무는 시간. 멀티미터로 잴 땐 3000 등으로
const unsigned long PERIOD_US = 4000;   // PWM 주기 4ms (250Hz)

void setAll(uint8_t v) {
  for (uint8_t i = 0; i < 5; i++) digitalWrite(PINS[i], v);
}

void setup() {
  for (uint8_t i = 0; i < 5; i++) {
    digitalWrite(PINS[i], LOW);
    pinMode(PINS[i], OUTPUT);
  }
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}

void loop() {
  static unsigned long lastPrint = 0;
  const unsigned long cycle = 2 * RAMP_MS + HOLD_MS;
  unsigned long t = millis() % cycle;

  unsigned int pct;
  if (t < RAMP_MS)                pct = t * 100UL / RAMP_MS;                      // 올라감
  else if (t < RAMP_MS + HOLD_MS) pct = 100;                                      // 유지
  else                            pct = (cycle - t) * 100UL / RAMP_MS;            // 내려옴
  if (pct > 100) pct = 100;

  unsigned long onUs = PERIOD_US * pct / 100;
  unsigned long start = micros();
  if (onUs > 0) setAll(HIGH);
  while (micros() - start < onUs) {}
  if (pct < 100) setAll(LOW);
  while (micros() - start < PERIOD_US) {}

  digitalWrite(LED_BUILTIN, pct > 0 ? HIGH : LOW);

  if (millis() - lastPrint > 250) {
    lastPrint = millis();
    Serial.println(pct);
  }
}
