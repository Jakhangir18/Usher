"""
Two-motor temple haptics: a soft, continuous buzz that leads the wearer toward the speaker.

    target to the right  -> soft continuous buzz on the RIGHT temple
    target to the left   -> soft continuous buzz on the LEFT temple
    getting closer       -> the buzz gets softer, and fades out as you face them
    overshoot            -> the buzz moves to the other temple
    already in front     -> one short, gentle tap on both temples

No IMU, so how far you've turned is estimated two ways:
  - closed loop: guide(angle, track=...) gets fresh head-relative directions of the speaker
    while they keep talking (the mic is on the head, so its angle changes as you turn);
  - open loop: when there's no fresh reading, assume a natural head turn of TURN_RATE_DEG_S.

Angle convention matches SPOOT: 0 = front, 90 = right, 270 = left.

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
# Soft by design: pulses at 0.25 felt too strong, so the continuous buzz stays well below.
SOFT_MAX = 0.20         # level when the speaker is behind you
SOFT_MIN = 0.10         # level just before you face them (then it fades to 0)
NUDGE_LEVEL = 0.35      # one brief start-up nudge so a micro motor spins up from standstill
NUDGE_SEC = 0.03
FRONT_TAP = 0.15        # already facing them: one gentle tap on both temples
FRONT_TAP_SEC = 0.25

FACING_DEG = 15         # "you're facing them" window, +/- degrees
TURN_RATE_DEG_S = 90.0  # assumed head-turn speed when there's no fresh direction reading
REACTION_SEC = 0.4      # people need a moment to start turning; the open-loop countdown waits this long
TIMEOUT_SEC = 5.0       # never buzz longer than this
FADE_SEC = 0.25         # fade-out at the end
STEP_SEC = 0.05         # how often the level/side is updated


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
# so a cancelled guide can never touch the motors after guide()/stop() returns.
_lock = threading.Lock()
_cancel = threading.Event()


def _wrap(a):
    """Wrap to [-180, 180). Positive = target is to the right."""
    return (a + 180.0) % 360.0 - 180.0


def _off():
    left.off()
    right.off()


def stop():
    """Cancel any running guide and turn both motors off."""
    with _lock:
        _cancel.set()
        _off()


def guide(rel_angle, track=None):
    """
    Lead the wearer toward a sound at rel_angle (degrees, SPOOT convention).

    track: optional callable(expected_angle) -> current head-relative angle of the same speaker
    (0..360) or None when there's no fresh reading. Lets the buzz follow the real turn.
    Non-blocking; a new call cancels the previous guide immediately.
    """
    global _cancel
    with _lock:
        _cancel.set()
        _off()
        _cancel = cancel = threading.Event()
    threading.Thread(target=_run, args=(rel_angle, track, cancel), daemon=True).start()


def _set(motor_values, cancel):
    """Set several motors at once ({motor: level}). Returns False if cancelled."""
    with _lock:
        if cancel.is_set():
            return False
        for m, v in motor_values.items():
            m.value = v
        return True


def _level(err):
    """Softer as you get closer: SOFT_MAX when behind, SOFT_MIN near the facing window."""
    frac = (min(abs(err), 180.0) - FACING_DEG) / (180.0 - FACING_DEG)
    return SOFT_MIN + (SOFT_MAX - SOFT_MIN) * max(0.0, frac)


def _run(rel_angle, track, cancel):
    try:
        err = _wrap(rel_angle)

        if abs(err) <= FACING_DEG:
            # Already facing them: one gentle tap on both temples.
            if _set({left: NUDGE_LEVEL, right: NUDGE_LEVEL}, cancel) and not cancel.wait(NUDGE_SEC):
                if _set({left: FRONT_TAP, right: FRONT_TAP}, cancel):
                    cancel.wait(FRONT_TAP_SEC - NUDGE_SEC)
            return

        start = last = time.monotonic()
        side = None
        while time.monotonic() - start < TIMEOUT_SEC:
            now = time.monotonic()
            dt, last = now - last, now

            # Estimate how far is left to turn: assume a natural head turn (after a reaction delay)...
            if side is not None and now - start > REACTION_SEC:
                step = TURN_RATE_DEG_S * dt
                err = err - step if err > 0 else err + step
            # ...unless the mic just heard the speaker again (closed loop).
            if track is not None:
                fresh = track(err % 360.0)
                if fresh is not None:
                    err = _wrap(fresh)

            if abs(err) <= FACING_DEG:
                break  # facing them: fade out

            motor = right if err > 0 else left
            other = left if motor is right else right
            if motor is not side:
                # Starting, or overshot to the other side: brief nudge to spin the motor up.
                if not _set({motor: NUDGE_LEVEL, other: 0}, cancel) or cancel.wait(NUDGE_SEC):
                    return
                side = motor
            if not _set({motor: _level(err)}, cancel) or cancel.wait(STEP_SEC):
                return

        # Gentle fade-out instead of a hard stop.
        if side is not None:
            begin = _level(FACING_DEG)
            steps = max(1, int(FADE_SEC / STEP_SEC))
            for i in range(steps, 0, -1):
                if not _set({side: begin * i / steps}, cancel) or cancel.wait(STEP_SEC):
                    return
    finally:
        with _lock:
            if not cancel.is_set():
                _off()


if __name__ == "__main__":
    # Bench test (no speaker tracking, so it uses the open-loop turn estimate).
    try:
        for a, label in ((0, "front: one gentle tap on both"), (40, "slight right: short soft buzz"),
                         (90, "right: ~1 s soft buzz"), (270, "left: ~1 s soft buzz"),
                         (180, "behind: ~2 s, softens as it goes")):
            print(f"guide({a})  {label}")
            guide(a)
            time.sleep(3.0)
        print("guide(120) cancelled after 0.5 s by guide(300)")
        guide(120)
        time.sleep(0.5)
        guide(300)
        time.sleep(2.5)
    finally:
        stop()
        left.close()
        right.close()
