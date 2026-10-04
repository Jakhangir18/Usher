"""
Drive the 128x64 two-colour SSD1306 OLED (yellow band on top, blue below).

Direction cue, on "Hello Jax":
    yellow band (top 16 px):  HELLO JAX  270°
    blue area:                big arrow + LEFT / RIGHT / FRONT / BEHIND

Caption, for transcripts (e.g. from ElevenLabs Scribe):
    yellow band:  small arrow + who is speaking ("?", "??", later a name)
    blue area:    what they said, word-wrapped, 3 lines at a time, paging through

A newer show()/show_caption() always replaces whatever is on screen. Only one program
may draw on the display at a time.

Bench test / display wiring test without the mic:  python display_cue.py
"""

import threading
import traceback
import time

WIDTH, HEIGHT = 128, 64
YELLOW_H = 16        # fixed yellow band at the top of the glass
OLED_ADDR = 0x3C
HOLD_SEC = 4.0       # clear the cue / last caption page after this long

CAPTION_LINES = 3    # body lines that fit in the blue area
CAPTION_LINE_H = 16
PAGE_SEC = 2.5       # how long each caption page stays up

# Same thresholds as haptics.py, so screen and motors agree.
FACING_DEG = 15
BEHIND_DEG = 120

_oled = None
_fonts = None        # (title, big word, caption body)
_lock = threading.Lock()
_token = 0

# Priority: the "Hello Jax" arrow and catch-up captions own the screen until this time;
# live caption updates (show_live) are skipped meanwhile instead of overwriting them.
CUE_PRIORITY_SEC = 3.0
_priority_until = 0.0


def _init():
    global _oled, _fonts
    if _oled is None:
        import board
        import busio
        import adafruit_ssd1306
        from PIL import ImageFont

        i2c = busio.I2C(board.SCL, board.SDA)
        _oled = adafruit_ssd1306.SSD1306_I2C(WIDTH, HEIGHT, i2c, addr=OLED_ADDR)
        try:
            bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            _fonts = (ImageFont.truetype(bold, 11), ImageFont.truetype(bold, 16), ImageFont.truetype(regular, 12))
        except Exception:
            default = ImageFont.load_default()
            _fonts = (default, default, default)
    return _oled


def direction(angle):
    """'front' / 'right' / 'left' / 'behind' for a SPOOT angle (0 front, 90 right)."""
    err = (angle + 180.0) % 360.0 - 180.0
    if abs(err) <= FACING_DEG:
        return "front"
    if abs(err) >= BEHIND_DEG:
        return "behind"
    return "right" if err > 0 else "left"


def _arrow(draw, where, cx, cy):
    if where == "right":
        draw.rectangle((cx - 20, cy - 4, cx + 4, cy + 4), fill=255)
        draw.polygon([(cx + 4, cy - 11), (cx + 20, cy), (cx + 4, cy + 11)], fill=255)
    elif where == "left":
        draw.rectangle((cx - 4, cy - 4, cx + 20, cy + 4), fill=255)
        draw.polygon([(cx - 4, cy - 11), (cx - 20, cy), (cx - 4, cy + 11)], fill=255)
    elif where == "front":
        draw.polygon([(cx - 11, cy - 1), (cx, cy - 12), (cx + 11, cy - 1)], fill=255)
        draw.rectangle((cx - 4, cy - 1, cx + 4, cy + 11), fill=255)
    else:  # behind
        draw.rectangle((cx - 4, cy - 11, cx + 4, cy + 1), fill=255)
        draw.polygon([(cx - 11, cy + 1), (cx, cy + 12), (cx + 11, cy + 1)], fill=255)


def _small_arrow(draw, where, cx, cy):
    """Arrow that fits in the yellow band (about 12 px)."""
    if where == "right":
        draw.polygon([(cx - 5, cy - 5), (cx + 5, cy), (cx - 5, cy + 5)], fill=255)
    elif where == "left":
        draw.polygon([(cx + 5, cy - 5), (cx - 5, cy), (cx + 5, cy + 5)], fill=255)
    elif where == "front":
        draw.polygon([(cx - 5, cy + 4), (cx, cy - 5), (cx + 5, cy + 4)], fill=255)
    elif where == "behind":
        draw.polygon([(cx - 5, cy - 4), (cx, cy + 5), (cx + 5, cy - 4)], fill=255)


def _centered(draw, text, font, top, bottom):
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    x = (WIDTH - (x1 - x0)) // 2 - x0
    y = top + ((bottom - top) - (y1 - y0)) // 2 - y0
    draw.text((x, y), text, font=font, fill=255)


def _wrap(draw, text, font, width):
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if not current or draw.textlength(trial, font=font) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw(angle, title):
    from PIL import Image, ImageDraw

    oled = _init()
    small, big, _ = _fonts
    where = direction(angle)
    img = Image.new("1", (WIDTH, HEIGHT), 0)
    d = ImageDraw.Draw(img)
    _centered(d, f"{title}  {angle:.0f}°", small, 0, YELLOW_H)
    _arrow(d, where, WIDTH // 2, YELLOW_H + 15)
    _centered(d, where.upper(), big, YELLOW_H + 30, HEIGHT)
    oled.image(img)
    oled.show()


def _draw_caption(who, where, lines):
    from PIL import Image, ImageDraw

    oled = _init()
    small, _, body = _fonts
    img = Image.new("1", (WIDTH, HEIGHT), 0)
    d = ImageDraw.Draw(img)
    if where == "right":
        _small_arrow(d, where, WIDTH - 8, YELLOW_H // 2)
    else:
        _small_arrow(d, where, 8, YELLOW_H // 2)
    # Centre the label vertically in the yellow band using its real glyph box (a fixed y clipped the "?" top).
    x0, y0, x1, y1 = d.textbbox((0, 0), who, font=small)
    d.text((18 - x0, (YELLOW_H - (y1 - y0)) // 2 - y0), who, font=small, fill=255)
    for i, line in enumerate(lines):
        d.text((1, YELLOW_H + 1 + i * CAPTION_LINE_H), line, font=body, fill=255)
    oled.image(img)
    oled.show()


def _clear(token):
    with _lock:
        if token == _token and _oled is not None:
            _oled.fill(0)
            _oled.show()


def _clear_later(token):
    timer = threading.Timer(HOLD_SEC, _clear, args=(token,))
    timer.daemon = True  # don't hold up Ctrl+C
    timer.start()


def show(angle, title="HELLO JAX"):
    """Draw the direction cue and clear it after HOLD_SEC. Never raises (the buzz path must not break)."""
    global _token, _priority_until
    try:
        with _lock:
            _token += 1
            token = _token
            _priority_until = time.monotonic() + CUE_PRIORITY_SEC
            _draw(angle, title)
        _clear_later(token)
    except Exception as exc:
        print(f"WARNING display_cue: {exc!r}")
        traceback.print_exc()


def show_async(angle, title="HELLO JAX"):
    """Non-blocking version for server.py's event loop (I2C draw takes ~30 ms)."""
    threading.Thread(target=show, args=(angle, title), daemon=True).start()


def show_caption(who, text, where=None, page_sec=PAGE_SEC):
    """
    Show `text` from speaker `who`, paging CAPTION_LINES lines at a time, page_sec per page.
    Blocks until the last page has had its time (use show_caption_async from an event loop),
    then the screen clears HOLD_SEC later unless something new is shown. Stops early if a
    newer show()/show_caption() takes over the screen. Never raises.
    """
    global _token, _priority_until
    try:
        from PIL import Image, ImageDraw

        with _lock:
            _token += 1
            token = _token
            # Claim priority now, so a waiting live-caption draw can't slip in before page 1.
            _priority_until = time.monotonic() + page_sec + 0.5
            _init()
            scratch = ImageDraw.Draw(Image.new("1", (WIDTH, HEIGHT)))
            lines = _wrap(scratch, text, _fonts[2], WIDTH - 2) or [""]

        for i in range(0, len(lines), CAPTION_LINES):
            with _lock:
                if token != _token:
                    return
                _priority_until = time.monotonic() + page_sec + 0.5  # live captions wait
                _draw_caption(who, where, lines[i:i + CAPTION_LINES])
            time.sleep(page_sec)  # every page, including the last, gets page_sec to be read
        _clear_later(token)
    except Exception as exc:
        print(f"WARNING display_cue: {exc!r}")
        traceback.print_exc()


TAG_PAD = 3   # px of filled box either side of an inverted speaker tag


def _layout(draw, tokens, font, width):
    """Wrap (is_tag, text) tokens into lines of (x, is_tag, text, w)."""
    space = draw.textlength(" ", font=font)
    lines, line, x = [], [], 0.0
    for is_tag, text in tokens:
        w = draw.textlength(text, font=font) + (2 * TAG_PAD if is_tag else 0)
        if line and x + w > width:
            lines.append(line)
            line, x = [], 0.0
        line.append((x, is_tag, text, w))
        x += w + space
    if line:
        lines.append(line)
    return lines


def _draw_tokens(who, where, lines):
    from PIL import Image, ImageDraw

    oled = _init()
    small, _, body = _fonts
    img = Image.new("1", (WIDTH, HEIGHT), 0)
    d = ImageDraw.Draw(img)
    if where == "right":
        _small_arrow(d, where, WIDTH - 8, YELLOW_H // 2)
    else:
        _small_arrow(d, where, 8, YELLOW_H // 2)
    x0, y0, x1, y1 = d.textbbox((0, 0), who, font=small)
    d.text((18 - x0, (YELLOW_H - (y1 - y0)) // 2 - y0), who, font=small, fill=255)

    for row, line in enumerate(lines):
        top = YELLOW_H + 1 + row * CAPTION_LINE_H
        for x, is_tag, text, w in line:
            x, w = int(round(x)), int(round(w))  # whole pixels only
            if is_tag:
                # Inverted tag: filled box, text cut out of it.
                d.rectangle((x + 1, top + 1, x + w, top + CAPTION_LINE_H - 2), fill=255)
                d.text((x + 1 + TAG_PAD, top), text, font=body, fill=0)
            else:
                d.text((x + 1, top), text, font=body, fill=255)
    oled.image(img)
    oled.show()


_live_seq = 0   # newest live frame drawn; older frames arriving late are skipped


def show_live(who, segments, where=None, seq=None):
    """
    Live caption for text that keeps growing: show the LAST CAPTION_LINES lines right away
    (no paging). `segments` is a list of (speaker_label, text); an inverted speaker tag is
    drawn wherever the speaker changes. A plain string also works. Clears HOLD_SEC after the
    last update. Skipped while the arrow or a catch-up caption has priority, and skipped if a
    newer frame (higher `seq`) was already drawn. Never raises.
    """
    global _token, _live_seq
    try:
        from PIL import Image, ImageDraw

        if isinstance(segments, str):
            segments = [(None, segments)]
        tokens, previous = [], object()
        for label, text in segments:
            if label and label != previous:
                tokens.append((True, label))
            previous = label
            tokens.extend((False, word) for word in text.split())

        with _lock:
            # Checked under the lock: a draw that was waiting while the arrow/catch-up took the
            # screen must not paint over it afterwards.
            if time.monotonic() < _priority_until:
                return
            if seq is not None:
                if seq <= _live_seq:
                    return
                _live_seq = seq
            _token += 1
            token = _token
            _init()
            scratch = ImageDraw.Draw(Image.new("1", (WIDTH, HEIGHT)))
            lines = _layout(scratch, tokens, _fonts[2], WIDTH - 2) or [[]]
            _draw_tokens(who, where, lines[-CAPTION_LINES:])
        _clear_later(token)
    except Exception as exc:
        print(f"WARNING display_cue: {exc!r}")
        traceback.print_exc()


def show_caption_async(who, text, where=None):
    threading.Thread(target=show_caption, args=(who, text, where), daemon=True).start()


if __name__ == "__main__":
    for a, label in ((0, "front"), (90, "right"), (270, "left"), (180, "behind"), (40, "right"), (320, "left")):
        print(f"show({a})  expect {label}")
        show(a, "TEST")
        time.sleep(2.5)

    print("caption test")
    show_caption("?", "You know, what's my favorite phrase to say in the world? Hello, Jax.", "left")
    show_caption("??", "It's, uh... I don't know. What, what is it?", "right")

    print("live caption test (inverted speaker tags)")
    show_live("??", [("?", "Hello, my name is Oliver."), ("??", "I'm testing this from the right."),
                     ("?", "Wow, this is cool.")], "left")
    time.sleep(HOLD_SEC)
