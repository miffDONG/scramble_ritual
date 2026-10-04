// electromagnet_amp.ino
// Serial "0~255\n" 수신 -> 전자석 PWM 출력
// 신호가 끊기면 자동으로 OFF (과열 방지)

const int  MAG_PIN    = 9;     // MOSFET 게이트 / 드라이버 PWM(ENA) 핀  (9 또는 10 권장)
const int  IN1_PIN    = -1;    // L298N 같은 방향핀 드라이버면 번호 입력 (없으면 -1)
const int  IN2_PIN    = -1;
const bool FAST_PWM   = true;  // true: 31kHz (자석 '삐-' 소음 제거). L298N에서 힘이 약하면 false
const unsigned long TIMEOUT_MS = 300;

int  level = 0;
unsigned long lastRx = 0;
char buf[6];
byte len = 0;

void setup() {
  pinMode(MAG_PIN, OUTPUT);
  analogWrite(MAG_PIN, 0);

  if (IN1_PIN >= 0) { pinMode(IN1_PIN, OUTPUT); digitalWrite(IN1_PIN, HIGH); }
  if (IN2_PIN >= 0) { pinMode(IN2_PIN, OUTPUT); digitalWrite(IN2_PIN, LOW);  }

  if (FAST_PWM && (MAG_PIN == 9 || MAG_PIN == 10)) {
    TCCR1B = (TCCR1B & 0b11111000) | 0x01;   // Timer1 분주 1 -> 31.4kHz
  }

  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (len) {
        buf[len] = 0;
        level = constrain(atoi(buf), 0, 255);
        analogWrite(MAG_PIN, level);
        lastRx = millis();
        len = 0;
      }
    } else if (c >= '0' && c <= '9' && len < sizeof(buf) - 1) {
      buf[len++] = c;
    }
  }

  if (level && millis() - lastRx > TIMEOUT_MS) {   // PC 쪽 종료/끊김
    level = 0;
    analogWrite(MAG_PIN, 0);
  }

  digitalWrite(LED_BUILTIN, level > 20);          // 내장 LED로 수신 확인
}
