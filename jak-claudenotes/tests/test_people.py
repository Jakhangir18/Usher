"""live_captions.People: direction-based speakers (?, ??) and the name rules."""

import pytest
from conftest import angle_near_zero

import live_captions


def test_people_are_added_in_order_and_matched_within_50_degrees():
    p = live_captions.People()
    assert p.nearest(90.0) is None
    assert p.add(90.0) == "?"
    assert p.add(270.0) == "??"
    assert p.nearest(100.0) == "?"
    assert p.nearest(140.0) == "?"       # exactly 50 deg still counts
    assert p.nearest(141.0) is None
    assert p.nearest(300.0) == "??"


def test_update_drifts_slowly_toward_the_new_angle():
    p = live_captions.People()
    p.add(90.0)
    p.update("?", 100.0)
    assert 90.0 < p.angles["?"] < 100.0
    assert p.angles["?"] == pytest.approx(92.0, abs=0.2)


def test_rule_names_only_unnamed_people_and_gemini_may_correct(capsys):
    p = live_captions.People()
    p.add(90.0)
    p.set_name("??", "Sam")               # unknown label: ignored
    assert p.names == {}
    p.set_name("?", "sajad")
    assert p.display("?") == "SAJAD"
    p.set_name("?", "Sajjad")             # the quick rule may not change a name
    assert p.display("?") == "SAJAD"
    p.set_name("?", "Sajjad", force=True)  # Gemini may
    assert p.display("?") == "SAJJAD"
    p.set_name("?", "Oliver", force=True)
    assert "name changed" in capsys.readouterr().out
    assert p.display("?") == "OLIVER"
    assert p.display("??") == "??"
    assert p.display(None) is None
    p.set_name("?", "Bartholomew", force=True)
    assert p.display("?") == "BARTHOLOME"  # capped at 10 characters for the 128 px display


def test_angle_gap_and_circular_mean_helpers():
    assert live_captions.angle_gap(350, 10) == 20
    assert angle_near_zero(live_captions.circular_mean([350, 10]))
