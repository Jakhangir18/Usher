"""
Gentle single-motor test: ramps power up slowly so you can find the lowest
level where the motor actually spins, without ever going high.

    python motor_test.py            # left motor  (GPIO4, physical pin 7,  P1 in the Touchpoint diagram)
    python motor_test.py right      # right motor (GPIO5, physical pin 29, P2 in the Touchpoint diagram)
    python motor_test.py left 0.3   # optional max level (default 0.35, hard cap 0.40)

Ctrl+C stops immediately. Note the first level where it spins: that's your KICK_LEVEL
in haptics.py; LEVEL can usually sit a bit lower once it's moving.
"""

import sys
import time

from gpiozero import PWMOutputDevice

PINS = {"left": 4, "right": 5}
FREQ = 200
STEP = 0.05
ON_SEC = 1.0
OFF_SEC = 1.0

side = sys.argv[1] if len(sys.argv) > 1 else "left"
if side not in PINS:
    raise SystemExit(f"Side must be one of: {', '.join(PINS)}")
max_level = float(sys.argv[2]) if len(sys.argv) > 2 else 0.35
max_level = min(max_level, 0.4)  # hard cap: 0.5 was too strong on the temples; 0.35 is our level

motor = PWMOutputDevice(PINS[side], frequency=FREQ)
print(f"Testing {side} motor on GPIO{PINS[side]}, up to {max_level:.2f}. Ctrl+C to stop.")

try:
    level = STEP
    while level <= max_level + 1e-9:
        print(f"  level {level:.2f} ... ", end="", flush=True)
        motor.value = level
        time.sleep(ON_SEC)
        motor.off()
        print("off")
        time.sleep(OFF_SEC)
        level += STEP
    print("Done.")
finally:
    motor.off()
    motor.close()
