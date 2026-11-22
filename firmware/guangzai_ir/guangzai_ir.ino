#include <Arduino.h>
#include "gesture.h"

// Example pinout for ESP32 Dev Module (classic ESP32), Arduino-ESP32 3.x.
constexpr uint8_t leftPin = 32;
constexpr uint8_t rightPin = 33;
constexpr uint8_t servoPin = 18;
constexpr uint8_t detectedLevel = LOW;

// Calibrate with the platform unloaded. Swap left/right values if reversed.
constexpr int centerUs = 1500;
constexpr int leftUs = 1300;
constexpr int rightUs = 1700;
constexpr int minimumUs = 1200;
constexpr int maximumUs = 1800;
constexpr int servoStepUs = 20;
constexpr uint32_t servoStepMs = 20;
constexpr uint32_t heartbeatMs = 200;
constexpr uint8_t resolution = 14;

int currentUs = centerUs;
uint32_t lastServoAt = 0;
uint32_t lastReportAt = 0;
bool pwmReady = false;

void writeServo(int pulseUs) {
  const uint32_t duty = (uint32_t(constrain(pulseUs, minimumUs, maximumUs)) *
                         (1UL << resolution)) / 20000UL;
  ledcWrite(servoPin, duty);
}

void setup() {
  Serial.begin(115200);
  pinMode(leftPin, INPUT_PULLUP);
  pinMode(rightPin, INPUT_PULLUP);
  pwmReady = ledcAttach(servoPin, 50, resolution);
  if (pwmReady) {
    writeServo(centerUs);
  } else {
    Serial.println("ERR:PWM");
  }
}

void loop() {
  const uint32_t now = millis();
  static Gesture gesture(now);
  if (!pwmReady) {
    delay(20);
    return;
  }

  const char before = gesture.zone;
  const bool accepted = gesture.update(digitalRead(leftPin) == detectedLevel,
                                       digitalRead(rightPin) == detectedLevel, now);
  const int targetUs = gesture.target == 'L' ? leftUs :
                       gesture.target == 'R' ? rightUs : centerUs;
  if (now - lastServoAt >= servoStepMs && currentUs != targetUs) {
    currentUs += constrain(targetUs - currentUs, -servoStepUs, servoStepUs);
    writeServo(currentUs);
    lastServoAt = now;
  }
  if (accepted || before != gesture.zone || now - lastReportAt >= heartbeatMs) {
    Serial.printf("GZ1,%lu,%c,%c\n", static_cast<unsigned long>(gesture.sequence),
                  gesture.zone, gesture.target);
    lastReportAt = now;
  }
  delay(5);
}
