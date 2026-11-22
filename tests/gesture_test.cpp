#include <cassert>
#include "../firmware/guangzai_ir/gesture.h"

int main() {
  Gesture g(0);
  assert(!g.update(false, false, 650));
  assert(!g.update(true, false, 660));
  assert(!g.update(true, false, 739));
  assert(g.update(true, false, 740));
  assert(g.target == 'L' && g.sequence == 1);
  assert(!g.update(true, false, 1000));
  assert(!g.update(false, true, 1100));
  assert(!g.update(false, true, 2000));
  assert(g.target == 'L' && g.sequence == 1);
  assert(!g.update(false, false, 2010));
  assert(!g.update(false, false, 2160));
  assert(!g.update(true, true, 2170));
  assert(!g.update(true, true, 2250));
  assert(g.zone == 'B' && g.target == 'L');
  assert(!g.update(false, true, 2260));
  assert(!g.update(false, true, 2400));
  assert(!g.update(false, false, 2410));
  assert(!g.update(false, false, 2560));
  assert(!g.update(false, true, 2570));
  assert(g.update(false, true, 2650));
  assert(g.target == 'R' && g.sequence == 2);
}
