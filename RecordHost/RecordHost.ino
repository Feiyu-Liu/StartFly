
// 全局与常量定义（片段）
#include <Arduino.h>
#include <avr/interrupt.h>
#include <avr/io.h>
#define SYNC_TTL_PIN 6
#define MAGNET_TTL_PIN 4 // 磁铁触发输出改为此引脚，避免与输入冲突

#define TRIGGER_THR_MIN 200
#define TRIGGER_THR_MAX 500
#define DEFAULT_PRINT_HZ 2500

#define SYNC_TTL_DUR 10 // ms
#define TRIAL_DUR 12000 // ms

const unsigned long FIRE_DURATION = 200; // ms

// 环形缓冲区配置
#define BUFFER_SIZE 256
volatile int dataBuffer[BUFFER_SIZE];
volatile uint32_t timeBuffer[BUFFER_SIZE]; // 每帧完整32位微秒时间戳
volatile uint16_t writeIndex = 0;
volatile uint16_t readIndex = 0;

// 无事件哨值：使用32位，占位值（避免与0xFFFFFFFF混淆）
#define DEFAULT_TIMESTAMP 0xFFFFFFFEul
// 事件标志位（bit0: Sync, bit1: Magnet）
#define FLAG_SYNC 0x01
#define FLAG_MAGNET 0x02
volatile uint32_t syncTimestamp = DEFAULT_TIMESTAMP;
volatile uint32_t magnetTimestamp = DEFAULT_TIMESTAMP;
volatile bool hasNewSync = false;
volatile bool hasNewMagnet = false;

enum MagnetState { IDLE, FIRING };
MagnetState magnetState = IDLE;
unsigned long fireStart = 0;

unsigned long startTrialTime = 0;
bool isSyncPinHigh = false;
unsigned long syncPinHighStartTime = 0;
bool isStart = false;

// ------------------ ADC 完成中断 ------------------
// ISR(ADC_vect)
ISR(ADC_vect) {
  uint16_t adcValue = ADC;        // 直接读取寄存器（比 analogRead 快）
  uint32_t frameAbsUs = micros(); // 在中断中捕获完整32位微秒

  uint16_t next = (writeIndex + 1) % BUFFER_SIZE;
  if (next != readIndex) { // 缓冲区未满
    dataBuffer[writeIndex] = adcValue;
    timeBuffer[writeIndex] = frameAbsUs; // 存入时间戳
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
}

// ------------------ 设置 ------------------
void setup() {
  Serial.begin(921600);
  pinMode(SYNC_TTL_PIN, OUTPUT);
  pinMode(MAGNET_TTL_PIN, OUTPUT); // 磁铁触发输出
  digitalWrite(MAGNET_TTL_PIN, LOW);
  digitalWrite(SYNC_TTL_PIN, LOW);

  // ---- ADC 初始化 ----
  ADMUX = (1 << REFS0);                 // AVcc 参考电压
  ADCSRA = (1 << ADEN) |                // 启用 ADC
           (1 << ADIE) |                // 启用中断
           (1 << ADPS2) | (1 << ADPS1); // 64 分频 → ~19.2kHz ADC 时钟
  ADCSRB = 0;
  DIDR0 = (1 << ADC0D); // 禁用数字输入 A0

  // ---- 配置 Timer1 为 CTC 模式 ----
  noInterrupts();
  TCCR1A = 0;
  TCCR1B = 0;
  unsigned long period_us = 1000000ul / DEFAULT_PRINT_HZ;
  unsigned int compareValue = (period_us / 4) - 1; // 64 分频 → 4 µs/tick
  if (compareValue > 65535)
    compareValue = 65535;
  OCR1A = compareValue;
  TCCR1B |= (1 << CS11) | (1 << CS10); // 64 分频
  TIMSK1 |= (1 << OCIE1A);             // 启用比较中断
  interrupts();
}

// ------------------ 主循环 ------------------
// loop() - 启动信号与事件记录片段
// loop() - 电磁铁触发逻辑（片段）
void loop() {
  // 等待串口启动信号
  if (!isStart && Serial.available()) {
    while (Serial.available())
      Serial.read();
    isStart = true;
    startTrialTime = millis();
    digitalWrite(SYNC_TTL_PIN, HIGH);
    syncTimestamp = micros(); // 改为完整32位时间戳，仅下一帧发送一次
    hasNewSync = true;
    isSyncPinHigh = true;
    syncPinHighStartTime = millis();
    // delay(10);
    // digitalWrite(SYNC_TTL_PIN, LOW);
  } else if (isStart && (millis() - startTrialTime) > TRIAL_DUR) {
    isStart = false;
  }

  // 从环形缓冲区读取数据并发送
  while (readIndex != writeIndex) {
    int val = dataBuffer[readIndex];
    uint32_t frameAbsUs = timeBuffer[readIndex]; // 从缓冲区读取完整32位时间戳
    readIndex = (readIndex + 1) % BUFFER_SIZE;

    uint32_t syncTsToSend = 0;
    uint32_t magnetTsToSend = 0;
    uint8_t flags = 0;

    noInterrupts();
    bool syncFlagLocal = hasNewSync;
    bool magnetFlagLocal = hasNewMagnet;
    if (syncFlagLocal) {
      syncTsToSend = syncTimestamp; // 事件完整时间戳
      hasNewSync = false;
    }
    if (magnetFlagLocal) {
      magnetTsToSend = magnetTimestamp; // 事件完整时间戳
      hasNewMagnet = false;
    }
    interrupts();

    if (syncFlagLocal)
      flags |= FLAG_SYNC;
    if (magnetFlagLocal)
      flags |= FLAG_MAGNET;

    Serial.write(0xAA);
    Serial.write(val & 0xFF);
    Serial.write((val >> 8) & 0xFF);

    // 发送 Sync 32位（小端）
    Serial.write(syncTsToSend & 0xFF);
    Serial.write((syncTsToSend >> 8) & 0xFF);
    Serial.write((syncTsToSend >> 16) & 0xFF);
    Serial.write((syncTsToSend >> 24) & 0xFF);

    // 发送 Magnet 32位（小端）
    Serial.write(magnetTsToSend & 0xFF);
    Serial.write((magnetTsToSend >> 8) & 0xFF);
    Serial.write((magnetTsToSend >> 16) & 0xFF);
    Serial.write((magnetTsToSend >> 24) & 0xFF);

    // 发送帧时间 32位（小端）
    Serial.write(frameAbsUs & 0xFF);
    Serial.write((frameAbsUs >> 8) & 0xFF);
    Serial.write((frameAbsUs >> 16) & 0xFF);
    Serial.write((frameAbsUs >> 24) & 0xFF);

    Serial.write(flags);

    // 电磁铁触发逻辑
    if (magnetState == IDLE && isStart) {
      if (val > TRIGGER_THR_MAX || val < TRIGGER_THR_MIN) {
        digitalWrite(MAGNET_TTL_PIN, HIGH);
        magnetTimestamp = micros(); // 改为完整32位时间戳，仅下一帧发送一次
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

  // Sync TTL 引脚状态机
  if (isSyncPinHigh && (millis() - syncPinHighStartTime) >= SYNC_TTL_DUR) {
    digitalWrite(SYNC_TTL_PIN, LOW);
    isSyncPinHigh = false;
  }
}
