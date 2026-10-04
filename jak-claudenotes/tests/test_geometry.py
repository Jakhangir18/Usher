"""Angles, directions and the DOA history lookups shared by wake, catch-up and captions."""

import time

import numpy as np
import pytest
from conftest import angle_near_zero, set_doa_history

import display_cue
import haptics
import live_captions
import server


@pytest.mark.parametrize("raw", range(0, 360, 7))
def test_server_and_live_captions_agree_on_the_mic_calibration(raw):
    assert server._logical_azimuth_from_hardware_deg(raw) == pytest.approx(live_captions.logical_angle(raw))


def test_calibration_flip_then_offset(monkeypatch):
    monkeypatch.setattr(server, "DOA_FLIP_LEFT_RIGHT", True)
    monkeypatch.setattr(server, "DOA_OFFSET_DEG", 184.0)
    assert server._logical_azimuth_from_hardware_deg(184) == pytest.approx(352.0)  # (360-184) - 184
    monkeypatch.setattr(server, "DOA_FLIP_LEFT_RIGHT", False)
    monkeypatch.setattr(server, "DOA_OFFSET_DEG", 0.0)
    assert server._logical_azimuth_from_hardware_deg(45) == pytest.approx(45.0)


def test_circular_mean_wraps_and_cancels():
    assert angle_near_zero(server._circular_mean([350, 10]))
    assert server._circular_mean([80, 100]) == pytest.approx(90.0)
    assert server._circular_mean([90, 270]) is None
    assert server._circular_mean([]) is None


def test_angle_difference_is_shortest_way_round():
    assert server.angle_difference(10, 350) == 20
    assert server.angle_difference(0, 180) == 180
    assert server.angle_difference(90, 90) == 0


@pytest.mark.parametrize("angle,word", [
    (0, "front"), (22, "front"), (45, "front-right"), (90, "right"), (135, "back-right"),
    (180, "behind"), (225, "back-left"), (270, "left"), (315, "front-left"), (338, "front"), (None, "?"),
])
def test_direction_word_eight_sectors(angle, word):
    assert server.direction_word(angle) == word


@pytest.mark.parametrize("angle,word", [
    (0, "front"), (15, "front"), (16, "right"), (344, "left"), (345, "front"), (60, "right"),
    (119, "right"), (120, "behind"), (180, "behind"), (240, "behind"), (241, "left"), (300, "left"),
])
def test_display_direction_matches_haptics_thresholds(angle, word):
    assert display_cue.direction(angle) == word
    assert display_cue.FACING_DEG == haptics.FACING_DEG   # the arrow and the buzz agree on "facing"


def test_loud_spans_finds_the_loud_frame():
    audio = np.zeros(server.SAMPLE_RATE, dtype="float32")          # 1 s of silence...
    n = int(server.LOUD_FRAME_SEC * server.SAMPLE_RATE)
    audio[5 * n:6 * n] = 0.1                                          # ...with 0.1 s of sound at 0.5 s
    spans, peak = server._loud_spans(audio, end_time=100.0)
    assert peak == pytest.approx(0.1)
    assert len(spans) == 1
    assert spans[0] == pytest.approx((99.5, 99.6))
    assert server._loud_spans(np.zeros(100, dtype="float32"), 1.0) == ([], 0.0)


def test_angles_during_picks_readings_inside_spans_in_time_order():
    set_doa_history([(0.0, 10), (1.0, 20), (2.0, 30), (3.0, 40), (4.0, 50)])
    assert server._angles_during([(0.9, 2.1)], pad=0) == [20, 30]
    # Overlapping and unordered spans: each reading once, oldest first.
    assert server._angles_during([(2.9, 4.1), (0.9, 3.1)], pad=0) == [20, 30, 40, 50]
    # Padding reaches readings just outside the span (readings are sparser than 0.1 s frames).
    assert server._angles_during([(1.04, 1.96)], pad=0.05) == [20, 30]
    assert server._angles_during([(1.04, 1.96)], pad=0) == []
    assert server._angles_during([], pad=0) == []


def test_direction_during_falls_back_to_latest_reading():
    set_doa_history([])
    with server._doa_lock:
        server._doa_azimuth_deg = 123.0
    assert server._direction_during([(0.0, 1.0)]) == 123.0
    set_doa_history([(0.5, 80), (0.6, 100)])
    assert server._direction_during([(0.0, 1.0)]) == pytest.approx(90.0)


def test_speaker_directions_and_find_caller():
    turns = [["speaker_0", 0.0, 2.0, ["hey", "are", "you", "coming"]],
             ["speaker_1", 3.0, 4.0, ["yeah", "sure"]],
             ["speaker_0", 5.0, 7.0, ["hello", "Jax,", "over", "here"]]]
    clip_start = 1000.0
    set_doa_history([(1000.5, 268), (1001.5, 272), (1003.5, 90), (1005.5, 270), (1006.5, 270)])
    directions = server._speaker_directions(turns, clip_start)
    assert directions["speaker_0"] == pytest.approx(270.0)
    assert directions["speaker_1"] == pytest.approx(90.0)
    assert server._find_caller(turns, directions, wake_angle=0.0) == "speaker_0"   # said "Jax,"
    no_name = [t[:3] + [["nothing"]] for t in turns]
    assert server._find_caller(no_name, directions, wake_angle=80.0) == "speaker_1"  # nearest the arrow
    assert server._find_caller(no_name, {}, wake_angle=80.0) == "speaker_0"          # last resort: last turn


def test_track_speaker_only_follows_fresh_nearby_readings():
    now = time.monotonic()
    set_doa_history([(now - 0.1, 60.0), (now - 0.05, 62.0)])
    assert server._track_speaker(70.0) == pytest.approx(61.0)
    assert server._track_speaker(200.0) is None                     # someone else, >60 deg away
    set_doa_history([(now - 2.0, 60.0)])
    assert server._track_speaker(60.0) is None                      # stale (older than 0.4 s)
