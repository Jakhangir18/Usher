"""haptics.py with the dummy motors (no GPIO here): levels, sides, timing and cancellation."""

import time

import pytest

import haptics


def test_motors_are_dummies_in_this_harness():
    assert isinstance(haptics.left, haptics._NoMotor) and isinstance(haptics.right, haptics._NoMotor)


def test_wrap_and_level():
    assert haptics._wrap(350) == -10
    assert haptics._wrap(180) == -180          # dead behind counts as left
    assert haptics._wrap(90) == 90
    assert haptics._level(180) == pytest.approx(haptics.SOFT_MAX)
    assert haptics._level(haptics.FACING_DEG) == pytest.approx(haptics.SOFT_MIN)
    assert haptics.SOFT_MIN < haptics._level(97.5) < haptics.SOFT_MAX
    assert haptics.SOFT_MAX <= 0.25, "soft by design: pulses at 0.25 felt too strong"


def _wait_off(timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if haptics.left.value == 0 and haptics.right.value == 0:
            return True
        time.sleep(0.02)
    return False


def test_guide_right_buzzes_only_the_right_temple_and_stops():
    haptics.guide(90.0)
    time.sleep(0.15)
    assert haptics.right.value > 0 and haptics.left.value == 0
    assert haptics.right.value <= haptics.NUDGE_LEVEL
    assert _wait_off(haptics.TIMEOUT_SEC + 1.0)


def test_guide_behind_counts_as_left():
    haptics.guide(180.0)
    time.sleep(0.15)
    assert haptics.left.value > 0 and haptics.right.value == 0
    haptics.stop()
    assert haptics.left.value == 0


def test_already_facing_gives_one_tap_on_both():
    haptics.guide(5.0)
    time.sleep(0.1)
    assert haptics.left.value > 0 and haptics.right.value > 0
    assert _wait_off(1.0)


def test_new_guide_cancels_the_old_one():
    haptics.guide(120.0)
    time.sleep(0.2)
    assert haptics.right.value > 0
    haptics.guide(300.0)
    time.sleep(0.2)
    assert haptics.left.value > 0 and haptics.right.value == 0
    haptics.stop()


def test_closed_loop_tracking_ends_the_buzz_when_facing():
    t0 = time.monotonic()
    haptics.guide(170.0, track=lambda expected: 5.0)   # the mic says: already in front
    assert _wait_off(2.0)
    assert time.monotonic() - t0 < 2.0                 # far below the 5 s open-loop timeout
