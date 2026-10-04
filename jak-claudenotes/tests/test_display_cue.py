"""display_cue.py without an OLED: layout helpers and the never-raises promise."""

import pytest
from PIL import Image, ImageDraw, ImageFont

import display_cue


def _scratch():
    return ImageDraw.Draw(Image.new("1", (display_cue.WIDTH, display_cue.HEIGHT)))


def test_wrap_keeps_every_line_inside_the_width():
    draw, font = _scratch(), ImageFont.load_default()
    text = "You know, what's my favorite phrase to say in the whole wide world? Hello, Jax."
    lines = display_cue._wrap(draw, text, font, display_cue.WIDTH - 2)
    assert " ".join(lines) == text
    assert all(draw.textlength(line, font=font) <= display_cue.WIDTH - 2 for line in lines)
    assert display_cue._wrap(draw, "", font, 100) == []


def test_layout_puts_tags_and_words_on_lines():
    draw, font = _scratch(), ImageFont.load_default()
    tokens = [(True, "?"), (False, "hello"), (False, "there"), (True, "??"), (False, "hi")]
    lines = display_cue._layout(draw, tokens, font, display_cue.WIDTH - 2)
    flat = [(is_tag, text) for line in lines for _, is_tag, text, _ in line]
    assert flat == tokens
    assert all(x + w <= display_cue.WIDTH - 2 + 1e-6 for line in lines for x, _, _, w in line)


def test_show_functions_never_raise_without_a_display(capsys):
    display_cue.show(90)
    display_cue.show_caption("?", "hello", "left", page_sec=0.01)
    display_cue.show_live("??", [("?", "hello")], "left")
    out = capsys.readouterr().out
    assert "WARNING display_cue" in out


def _capture_live_draws(monkeypatch):
    drawn = []
    monkeypatch.setattr(display_cue, "_init", lambda: None)
    monkeypatch.setattr(display_cue, "_fonts", (None, None, ImageFont.load_default()))
    monkeypatch.setattr(display_cue, "_draw_tokens", lambda who, where, lines: drawn.append(who))
    monkeypatch.setattr(display_cue, "_clear_later", lambda token: None)
    monkeypatch.setattr(display_cue, "_priority_until", 0.0)
    monkeypatch.setattr(display_cue, "_live_seq", 0)
    return drawn


def test_late_live_frames_within_one_session_are_skipped(monkeypatch):
    drawn = _capture_live_draws(monkeypatch)
    display_cue.show_live("a", [(None, "one")], "left", 2)
    display_cue.show_live("b", [(None, "late")], "left", 1)   # older frame arriving late
    display_cue.show_live("c", [(None, "three")], "left", 3)
    assert drawn == ["a", "c"]


@pytest.mark.xfail(strict=True, reason="Finding R2: _live_seq never resets, but live_captions.run() restarts its frame counter at 0 on every reconnect")
def test_live_frames_after_a_caption_reconnect_are_drawn(monkeypatch):
    drawn = _capture_live_draws(monkeypatch)
    for seq in (1, 2, 3):                          # session 1 (live_captions.run, state["seq"] from 0)
        display_cue.show_live("s1", [(None, "hello")], "left", seq)
    for seq in (1, 2, 3):                          # session 2 after a reconnect: counter restarts at 0
        display_cue.show_live("s2", [(None, "again")], "left", seq)
    assert drawn == ["s1", "s1", "s1", "s2", "s2", "s2"]
