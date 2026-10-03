"""
Show the wake-phrase direction on the 128x64 two-colour SSD1306 OLED.

    yellow band (top 16 px):  HELLO JAX  270°
    blue area:                big arrow + LEFT / RIGHT / FRONT / BEHIND

Used by server.py on "Hello Jax" (alongside the motors), so the test also works
with the motors detached. Only one program may draw on the display at a time.

Bench test / display wiring test without the mic:  python display_cue.py
"""

import threading

WIDTH, HEIGHT = 128, 64
YELLOW_H = 16        # fixed yellow band at the top of the glass
OLED_ADDR = 0x3C
HOLD_SEC = 4.0       # clear the cue after this long

# Same thresholds as haptics.py, so screen and motors agree.
FACING_DEG = 15
BEHIND_DEG = 120

_oled = None
_fonts = None
_lock = threading.Lock()
_token = 0


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
            _fonts = (
                ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 11),
                ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16),
            )
        except Exception:
            _fonts = (ImageFont.load_default(), ImageFont.load_default())
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


def _centered(draw, text, font, top, bottom):
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    x = (WIDTH - (x1 - x0)) // 2 - x0
    y = top + ((bottom - top) - (y1 - y0)) // 2 - y0
    draw.text((x, y), text, font=font, fill=255)


def _draw(angle, title):
    from PIL import Image, ImageDraw

    oled = _init()
    small, big = _fonts
    where = direction(angle)
    img = Image.new("1", (WIDTH, HEIGHT), 0)
    d = ImageDraw.Draw(img)
    _centered(d, f"{title}  {angle:.0f}°", small, 0, YELLOW_H)
    _arrow(d, where, WIDTH // 2, YELLOW_H + 15)
    _centered(d, where.upper(), big, YELLOW_H + 30, HEIGHT)
    oled.image(img)
    oled.show()


def _clear(token):
    with _lock:
        if token == _token and _oled is not None:
            _oled.fill(0)
            _oled.show()


def show(angle, title="HELLO JAX"):
    """Draw the cue and clear it after HOLD_SEC. Never raises (the buzz path must not break)."""
    global _token
    try:
        with _lock:
            _token += 1
            token = _token
            _draw(angle, title)
        timer = threading.Timer(HOLD_SEC, _clear, args=(token,))
        timer.daemon = True  # don't hold up Ctrl+C
        timer.start()
    except Exception as exc:
        print(f"WARNING display_cue: {exc!r}")


def show_async(angle, title="HELLO JAX"):
    """Non-blocking version for server.py's event loop (I2C draw takes ~30 ms)."""
    threading.Thread(target=show, args=(angle, title), daemon=True).start()


if __name__ == "__main__":
    import time

    for a, label in ((0, "front"), (90, "right"), (270, "left"), (180, "behind"), (40, "right"), (320, "left")):
        print(f"show({a})  expect {label}")
        show(a, "TEST")
        time.sleep(2.5)
    time.sleep(HOLD_SEC)
