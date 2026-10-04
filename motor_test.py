"""
Simple single-motor test at a fixed, low power: 3 buzzes of 1 s, 1 s apart.

    python motor_test.py            # left motor  (GPIO4, physical pin 7,  P1 in the Touchpoint diagram)
    python motor_test.py right      # right motor (GPIO5, physical pin 29, P2 in the Touchpoint diagram)
    python motor_test.py left 0.3   # optional power (default 0.25, hard cap 0.40)

Ctrl+C stops immediately.
"""

import sys
import time

from gpiozero import PWMOutputDevice

PINS = {"left": 4, "right": 5}
FREQ = 200
BUZZES = 3
ON_SEC = 1.0
OFF_SEC = 1.0

side = sys.argv[1] if len(sys.argv) > 1 else "left"
if side not in PINS:
    raise SystemExit(f"Side must be one of: {', '.join(PINS)}")
level = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
level = min(level, 0.4)  # hard cap: 0.5 was too strong on the temples

motor = PWMOutputDevice(PINS[side], frequency=FREQ)
print(f"Testing {side} motor on GPIO{PINS[side]} at {level:.2f}. Ctrl+C to stop.")

try:
    for i in range(BUZZES):
        print(f"  buzz {i + 1} ... ", end="", flush=True)
        motor.value = level
        time.sleep(ON_SEC)
        motor.off()
        print("off")
        time.sleep(OFF_SEC)
    print("Done.")
finally:
    motor.off()
    motor.close()
