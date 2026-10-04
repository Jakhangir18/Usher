"""display_cue.py without an OLED: layout helpers and the never-raises promise."""

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
