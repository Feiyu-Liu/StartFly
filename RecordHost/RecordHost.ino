
#define SYNC_TTL_PIN 6
#define TRIGGER_TTL_PIN 2   // 用于外部触发输入（Pin Change Interrupt）
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
volatile uint16_t lastTriggerStamp = DEFAULT_TIMESTAMP;
volatile bool hasNewTrigger = false;     // 是否有新触发待打印
volatile uint8_t triggerPrevState = 0;   // 上次引脚状态（用于检测上升沿）

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
  // 每次定时触发 ADC 转换
  ADCSRA |= (1 << ADSC);
}

// ------------------ Pin Change 中断（检测 TRIGGER_TTL_PIN 上升沿） ------------------
ISR(PCINT2_vect) {
  // 端口D的引脚变化中断
  uint8_t pind = PIND;
  uint8_t currentState = (pind & (1 << PD4)) ? 1 : 0; // TRIGGER_TTL_PIN = D4
  // 检测上升沿：从0变为1
  if (currentState && !triggerPrevState) {
    lastTriggerStamp = TCNT1;  // 读取Timer1当前计数（16位）
    hasNewTrigger = true;
  }
  triggerPrevState = currentState;
}

// ------------------ 设置 ------------------
void setup() {
  Serial.begin(230400);
  pinMode(TRIGGER_TTL_PIN, INPUT);  // 用于外部触发输入
  pinMode(SYNC_TTL_PIN, OUTPUT);
  pinMode(MAGNET_TTL_PIN, OUTPUT);  // 磁铁触发输出
  digitalWrite(MAGNET_TTL_PIN, LOW);
  digitalWrite(SYNC_TTL_PIN, LOW);

  // ---- 配置 Pin Change Interrupt 用于 TRIGGER_TTL_PIN(D4) ----
  noInterrupts();
  PCICR |= (1 << PCIE2);        // 使能 PORTD 的 Pin Change 中断
  PCMSK2 |= (1 << PD4);         // 使能 D4 的中断监视（对应 PCINT20）
  interrupts();

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
  TCCR1B |= (1 << WGM12);              // CTC 模式
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
    delay(10);
    digitalWrite(SYNC_TTL_PIN, LOW);
  } else if (isStart && (millis() - startTrialTime) > 30000) {
    isStart = false;
  }

  // 从环形缓冲区读取数据并发送
  while (readIndex != writeIndex) {
    int val = dataBuffer[readIndex];
    readIndex = (readIndex + 1) % BUFFER_SIZE;

    // 读取并消费一次触发时间戳（若无触发则使用固定值）
    uint16_t stampToSend;
    bool consumed = false;
    noInterrupts();
    if (hasNewTrigger) {
      stampToSend = lastTriggerStamp;
      hasNewTrigger = false; // 消费本次触发
      consumed = true;
    }
    interrupts();
    if (!consumed) {
      stampToSend = DEFAULT_TIMESTAMP;
    }

    Serial.write(0xAA);
    Serial.write(val & 0xFF);
    Serial.write((val >> 8) & 0xFF);
    Serial.write(stampToSend & 0xFF);
    Serial.write((stampToSend >> 8) & 0xFF);

    // 电磁铁触发逻辑
    if (magnetState == IDLE && isStart) {
      if (val > TRIGGER_THR_MAX || val < TRIGGER_THR_MIN) {
        digitalWrite(MAGNET_TTL_PIN, HIGH);
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
