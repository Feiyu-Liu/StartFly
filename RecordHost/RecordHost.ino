
#define SYNC_TTL_PIN 6
#define MAGNET_TTL_PIN 4    // 磁铁触发输出改为此引脚，避免与输入冲突


#define TRIGGER_THR_MIN 200
#define TRIGGER_THR_MAX 500
#define DEFAULT_PRINT_HZ 2500

// 环形缓冲区配置
#define BUFFER_SIZE 512
volatile int dataBuffer[BUFFER_SIZE];
volatile uint16_t writeIndex = 0;
volatile uint16_t readIndex = 0;

// 触发时间戳（Timer1计数器的16位值）
#define DEFAULT_TIMESTAMP 0xFFFF
volatile unsigned long syncTimestamp = 0;
volatile unsigned long magnetTimestamp = 0;
volatile bool hasNewSync = false;
volatile bool hasNewMagnet = false;

enum MagnetState { IDLE, FIRING };
MagnetState magnetState = IDLE;
unsigned long fireStart = 0;
const unsigned long FIRE_DURATION = 200; // ms

unsigned long startTrialTime = 0;
bool isStart = false;

// ------------------ ADC 完成中断 ------------------
ISR(ADC_vect) {
  uint16_t adcValue = ADC;  // 直接读取寄存器（比 analogRead 快）

  uint16_t next = (writeIndex + 1) % BUFFER_SIZE;
  if (next != readIndex) { // 缓冲区未满
    dataBuffer[writeIndex] = adcValue;
    writeIndex = next;
  }
}

// ------------------ 定时器中断 ------------------
ISR(TIMER1_COMPA_vect) {
  // 为保持采样率，手动更新下一次比较匹配的值。
  // 这使得 TCNT1 可以自由运行（用于时间戳），同时我们仍能获得周期性中断。
  OCR1A += (1000000ul / DEFAULT_PRINT_HZ) / 4; // 增加一个周期的滴答数

  // 每次定时触发 ADC 转换
  ADCSRA |= (1 << ADSC);

  // The following logic is based on the user's request to use micros() in the ISR.
  // Note: digitalRead() can be slow inside a high-frequency ISR and may miss brief pulses.
  unsigned long currentMicros = micros();
  if (digitalRead(SYNC_TTL_PIN) == HIGH && !hasNewSync) {
    syncTimestamp = currentMicros;
    hasNewSync = true;
  }
  if (digitalRead(MAGNET_TTL_PIN) == HIGH && !hasNewMagnet) {
    magnetTimestamp = currentMicros;
    hasNewMagnet = true;
  }
}



// ------------------ 设置 ------------------
void setup() {
  Serial.begin(230400);
  pinMode(SYNC_TTL_PIN, OUTPUT);
  pinMode(MAGNET_TTL_PIN, OUTPUT);  // 磁铁触发输出
  digitalWrite(MAGNET_TTL_PIN, LOW);
  digitalWrite(SYNC_TTL_PIN, LOW);



  // ---- ADC 初始化 ----
  ADMUX = (1 << REFS0);          // AVcc 参考电压
  ADCSRA = (1 << ADEN)  |        // 启用 ADC
           (1 << ADIE)  |        // 启用中断
           (1 << ADPS2) | (1 << ADPS1); // 64 分频 → ~19.2kHz ADC 时钟
  ADCSRB = 0;
  DIDR0 = (1 << ADC0D);          // 禁用数字输入 A0

  // ---- 配置 Timer1 为 CTC 模式 ----
  noInterrupts();
  TCCR1A = 0;
  TCCR1B = 0;
  unsigned long period_us = 1000000ul / DEFAULT_PRINT_HZ;
  unsigned int compareValue = (period_us / 4) - 1;  // 64 分频 → 4 µs/tick
  if (compareValue > 65535) compareValue = 65535;
  OCR1A = compareValue;
  TCCR1B |= (1 << CS11) | (1 << CS10); // 64 分频
  TIMSK1 |= (1 << OCIE1A);             // 启用比较中断
  interrupts();
}

// ------------------ 主循环 ------------------
void loop() {
  // 等待串口启动信号
  if (!isStart && Serial.available()) {
    while (Serial.available()) Serial.read();
    isStart = true;
    startTrialTime = millis();
    digitalWrite(SYNC_TTL_PIN, HIGH);
    syncTimestamp = micros();
    hasNewSync = true;
    delay(10);
    digitalWrite(SYNC_TTL_PIN, LOW);
  } else if (isStart && (millis() - startTrialTime) > 12000) {
    isStart = false;
  }



  // 从环形缓冲区读取数据并发送
  while (readIndex != writeIndex) {
    int val = dataBuffer[readIndex];
    readIndex = (readIndex + 1) % BUFFER_SIZE;

    unsigned long syncTsToSend = 0;
    unsigned long magnetTsToSend = 0;

    noInterrupts();
    if (hasNewSync) {
      syncTsToSend = syncTimestamp;
      hasNewSync = false;
    }
    if (hasNewMagnet) {
      magnetTsToSend = magnetTimestamp;
      hasNewMagnet = false;
    }
    interrupts();

    Serial.write(0xAA);
    Serial.write((uint8_t *)&val, sizeof(val));
    Serial.write((uint8_t *)&syncTsToSend, sizeof(syncTsToSend));
    Serial.write((uint8_t *)&magnetTsToSend, sizeof(magnetTsToSend));

    // 电磁铁触发逻辑
    if (magnetState == IDLE && isStart) {
      if (val > TRIGGER_THR_MAX || val < TRIGGER_THR_MIN) {
        digitalWrite(MAGNET_TTL_PIN, HIGH);
        magnetTimestamp = micros();
        hasNewMagnet = true;
        fireStart = millis();
        magnetState = FIRING;
      }
    }
  }

  // 电磁铁状态机
  if (magnetState == FIRING && (millis() - fireStart) >= FIRE_DURATION) {
    digitalWrite(MAGNET_TTL_PIN, LOW);
    magnetState = IDLE;
  }
}
