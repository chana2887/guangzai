#pragma once

#include <stdint.h>

class Gesture {
 public:
  static constexpr uint32_t debounceMs = 80;
  static constexpr uint32_t settleMs = 650;
  static constexpr uint32_t clearMs = 140;

  explicit Gesture(uint32_t startMs)
      : rawSince_(startMs), movedAt_(startMs), clearSince_(startMs) {}

  bool update(bool left, bool right, uint32_t now) {
    const char sample = left ? (right ? 'B' : 'L') : (right ? 'R' : 'N');
    if (sample != raw_) {
      raw_ = sample;
      rawSince_ = now;
    }

    if (sample != 'N') {
      clearSince_ = now;
    }
    if (raw_ != zone && now - rawSince_ >= debounceMs) {
      zone = raw_;
    }
    if (zone == 'B') {
      armed_ = false;
    }
    if (now - movedAt_ >= settleMs && zone == 'N' &&
        now - clearSince_ >= clearMs) {
      armed_ = true;
    }
    if (!armed_ || (zone != 'L' && zone != 'R') || zone != sample) {
      return false;
    }

    target = zone;
    ++sequence;
    armed_ = false;
    movedAt_ = now;
    return true;
  }

  char zone = 'N';
  char target = 'C';
  uint32_t sequence = 0;

 private:
  char raw_ = 'N';
  uint32_t rawSince_;
  uint32_t movedAt_;
  uint32_t clearSince_;
  bool armed_ = false;
};
