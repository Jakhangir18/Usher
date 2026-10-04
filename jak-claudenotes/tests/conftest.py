"""
Offline harness for the Usher headset code: stubs for everything that needs the Pi
(mic, USB mic control, GPIO motors, I2C display) so server.py and friends import anywhere.

Run from the repo root:  .venv-vps/bin/pytest jak-claudenotes/tests
"""

import os
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# The settings the tests assume (server.py and live_captions.py read them at import time). Set
# outright, not setdefault: on the Pi the real .env (loaded by server.py for any key not yet set)
# and the shell must not change the rule under test, e.g. WAKE_MAX_GAP=0 applied for finding R1.
for key, val in {
    "DOA_SOURCE": "usb", "LIVE_CAPTIONS": "0", "USER_NAME": "Jax", "USER_NAME_ALIASES": "jacks,jaxx",
    "WAKE_EXTRA_NAMES": "jack", "WAKE_GREETINGS": "hello,helo,hallo,hullo,hi,hey,hiya",
    "WAKE_MAX_GAP": "1", "WAKE_COOLDOWN_SEC": "4.5",
    "AUDIO_INPUT_CHANNELS": "6", "AUDIO_CHANNEL": "2", "CAPTION_AUDIO_CHANNEL": "0",
    "DOA_FLIP_LEFT_RIGHT": "1", "DOA_OFFSET_DEG": "184", "VOLUME_HEARTBEAT_SEC": "0",
}.items():
    os.environ[key] = val


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


class _NoStream:
    """sounddevice.InputStream stand-in: tests never open the mic."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("no audio device in the offline harness")


# sounddevice needs libportaudio; server.py and live_captions.py import it at module level.
_stub_module(
    "sounddevice",
    query_devices=lambda *a, **k: [],
    default=types.SimpleNamespace(device=(-1, -1)),
    InputStream=_NoStream,
    _terminate=lambda: None,
    _initialize=lambda: None,
)

# gpiozero missing -> haptics.py falls back to its dummy motors (the documented behaviour).
# pyusb missing -> doa_reader.UsbDoa raises; open_doa is patched below anyway.
for name in ("gpiozero", "usb", "usb.core", "usb.util", "board", "busio", "adafruit_ssd1306"):
    sys.modules[name] = None

import doa_reader  # noqa: E402  (after the stubs)


class FakeDoa:
    """Direction reader that never reports anything; tests fill server._doa_history directly."""

    name = "fake (offline harness)"

    def read(self):
        return None

    def close(self):
        pass


# server.py starts its DOA poll thread at import; with the real reader it would retry USB
# every second (or spawn sudo xvf_host 20 times a second with DOA_SOURCE=auto).
doa_reader.open_doa = lambda source, xvf_host_path: FakeDoa()


def set_doa_history(entries):
    """Replace server._doa_history with [(monotonic_time, angle), ...] under its lock."""
    import server

    with server._doa_lock:
        server._doa_history.clear()
        server._doa_history.extend(entries)


def angle_near_zero(angle, tol=1e-6):
    """0 and 360 (float rounding of a negative epsilon) are the same direction."""
    return min(angle % 360, 360 - angle % 360) < tol
