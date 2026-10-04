"""
Two-motor temple haptics for turn-toward-speaker guidance (open-loop, no IMU).
Drop next to SPOOT's server.py.

Angle convention matches SPOOT: 0 = front, 90 = right, 270 = left.
Motor code style borrowed from Touchpoint's output/motors.py (gpiozero PWM, kick-start).

Patterns (each played REPEATS times):
    in front (+/-15 deg)   1 pulse on both temples
    slightly off (<60)     1 pulse on that side
    to the side (<120)     2 pulses
    behind                 3 pulses   (dead behind, exactly 180, counts as left)

On a Pi 5, run with GPIOZERO_PIN_FACTORY=lgpio. In a venv, create it with
--system-site-packages so the Pi OS lgpio package is visible.
If the motors can't be opened (no GPIO, missing pin library) this module falls
back to dummy motors and prints a warning, so server.py still starts.
"""

import threading
import time

# --- Wiring (BCM numbering) -------------------------------------------------
LEFT_GPIO = 4   # P1 in the Touchpoint/Quackhack diagram, physical pin 7
RIGHT_GPIO = 5  # P2 in the Touchpoint/Quackhack diagram, physical pin 29
FREQ = 200

# --- Tuning -----------------------------------------------------------------
# Micro motors are fragile: change in 0.05 steps. Raised from 0.15/0.30 so the cue is
# easy to feel; lower LEVEL if it's too strong on the temples or the motors get warm.
LEVEL = 0.35          # running power (Touchpoint ran these motors at 0.35 with a 1.0 kick)
KICK_LEVEL = 0.60     # brief start-up boost to get past stall torque
KICK_SEC = 0.04
PULSE_SEC = 0.12      # total pulse length, including the kick
GAP_SEC = 0.15        # between pulses in a group (long enough to count)
REPEAT_GAP_SEC = 0.4  # between repeats of the whole pattern
REPEATS = 2

FACING_DEG = 15
SIDE_DEG = 60
BEHIND_DEG = 120

class _NoMotor:
    """Stand-in when GPIO isn't available, so the rest of the server keeps running."""

    def __init__(self, pin):
        self.pin = pin
        self.value = 0

    def off(self):
        self.value = 0

    def close(self):
        pass


try:
    from gpiozero import PWMOutputDevice
    left = PWMOutputDevice(LEFT_GPIO, frequency=FREQ)
    right = PWMOutputDevice(RIGHT_GPIO, frequency=FREQ)
except Exception as exc:
    print(f"WARNING haptics: motors unavailable ({exc!r}); running with dummy motors")
    left, right = _NoMotor(LEFT_GPIO), _NoMotor(RIGHT_GPIO)

# All motor writes happen under _lock and only while the job's event is unset,
# so a cancelled pattern can never touch the motors after guide()/stop() returns.
_lock = threading.Lock()
_cancel = threading.Event()


def _wrap(a):
    """Wrap to [-180, 180). Positive = target is to the right."""
    return (a + 180.0) % 360.0 - 180.0


def pattern(rel_angle):
    """Return (motors, pulse_count) for a SPOOT angle."""
    err = _wrap(rel_angle)
    if abs(err) <= FACING_DEG:
        return (left, right), 1
    motor = right if err > 0 else left
    if abs(err) < SIDE_DEG:
        return (motor,), 1
    if abs(err) < BEHIND_DEG:
        return (motor,), 2
    return (motor,), 3


def _off():
    left.off()
    right.off()


def stop():
    """Cancel any running pattern and turn both motors off."""
    with _lock:
        _cancel.set()
        _off()


def guide(rel_angle):
    """
    Point the wearer toward a sound at rel_angle (degrees, SPOOT convention).
    Non-blocking; a new call cancels the previous pattern immediately.
    """
    global _cancel
    with _lock:
        _cancel.set()
        _off()
        _cancel = cancel = threading.Event()
    threading.Thread(target=_run, args=(rel_angle, cancel), daemon=True).start()


def _set(motors, value, cancel):
    with _lock:
        if cancel.is_set():
            return False
        for m in motors:
            m.value = value
        return True


def _pulse(motors, cancel):
    """One pulse. Returns False if cancelled."""
    if not _set(motors, KICK_LEVEL, cancel):
        return False
    if cancel.wait(KICK_SEC) or not _set(motors, LEVEL, cancel):
        return False
    stopped = cancel.wait(PULSE_SEC - KICK_SEC)
    _set(motors, 0, cancel)
    return not stopped


def _run(rel_angle, cancel):
    motors, count = pattern(rel_angle)
    for r in range(REPEATS):
        if r and cancel.wait(REPEAT_GAP_SEC):
            return
        for i in range(count):
            if i and cancel.wait(GAP_SEC):
                return
            if not _pulse(motors, cancel):
                return


if __name__ == "__main__":
    # Bench test: one of each pattern, then a mid-pattern cancel.
    try:
        for a, label in ((0, "front"), (30, "slight right"), (90, "right"),
                         (150, "behind right"), (300, "slight left"),
                         (270, "left"), (200, "behind left")):
            print(f"guide({a})  {label}")
            guide(a)
            time.sleep(2.5)
        print("guide(150) cancelled after 0.3 s by guide(270)")
        guide(150)
        time.sleep(0.3)
        guide(270)
        time.sleep(2.5)
    finally:
        stop()
        left.close()
        right.close()
