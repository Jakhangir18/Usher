import asyncio
import collections
import difflib
import faulthandler
import signal
import json
import math
import os
import re
import threading
import time
import traceback
import urllib.error
import urllib.request

import numpy as np
import sounddevice as sd
import websockets
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

import display_cue
import doa_reader
import haptics
import scribe


# =========================
# .env auto-loader
# =========================

def _load_dotenv(path=".env"):
    """
    Tiny .env loader. Populates os.environ from a KEY=VALUE file before any
    config reads happen. Avoids needing python-dotenv as a hard dependency.
    Existing environment variables take precedence over .env values.
    """

    if not os.path.exists(path):
        return 0

    loaded = 0

    try:
        with open(path) as f:
            for line in f:
                line = line.strip()

                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()

                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]

                if key and key not in os.environ:
                    os.environ[key] = val
                    loaded += 1
    except Exception as exc:
        print(f"warning: couldn't load {path}: {exc}")

    return loaded


# Load .env from next to this file, not the current directory, so it's found
# however the server is started.
# If the server ever freezes, `pkill -USR1 -f server.py` (from another terminal) prints
# every thread's current line, showing exactly where it's stuck.
if hasattr(signal, "SIGUSR1"):
    faulthandler.register(signal.SIGUSR1, all_threads=True)

_DOTENV_LOADED = _load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


# =========================
# Hardware / device config
# =========================

XVF_HOST = os.environ.get(
    "XVF_HOST",
    os.path.expanduser("~/Documents/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/rpi_64bit/xvf_host"),
).strip()

# When the mic array is mounted upside down, hardware left/right are reversed.
# We mirror azimuth: logical_angle = (360 - raw) % 360. Set DOA_FLIP_LEFT_RIGHT=0 if yours is correct.
DOA_FLIP_LEFT_RIGHT = os.environ.get("DOA_FLIP_LEFT_RIGHT", "1").strip().lower() in (
    "1", "true", "yes", "on",
)


# Rotation between the mic's 0 deg and the wearer's front, applied after the flip.
# Measure both with `python3 doa_calibrate.py`.
DOA_OFFSET_DEG = float(os.environ.get("DOA_OFFSET_DEG", "184"))  # calibrated 2026-10-03, USB source


def _logical_azimuth_from_hardware_deg(raw_deg):
    a = float(raw_deg) % 360.0
    if DOA_FLIP_LEFT_RIGHT:
        a = (360.0 - a) % 360.0
    return (a - DOA_OFFSET_DEG) % 360.0


CLIENTS = set()

_ROOT = os.path.dirname(os.path.abspath(__file__))

# =========================
# Flask PWA + continuous DOA (GET /state, static HUD)
# =========================

RMS_MIN = 0.003
RMS_MAX = 0.05
DANGER_THRESHOLD = 0.7

_doa_azimuth_deg = 0.0
_doa_lock = threading.Lock()


def angle_to_direction_hud(angle):
    """Labels for `index.html` DIRS map (matches former app.py naming)."""

    boundaries = [
        (22.5,  'front'),
        (67.5,  'front-right'),
        (112.5, 'right'),
        (157.5, 'back-right'),
        (202.5, 'back'),
        (247.5, 'back-left'),
        (292.5, 'left'),
        (337.5, 'front-left'),
        (360.0, 'front'),
    ]
    for threshold, name in boundaries:
        if angle < threshold:
            return name
    return 'front'


# Where direction comes from (see doa_reader.py): "usb" = DOA_VALUE with the chip's own
# speech flag, "xvf_host" = the vendor binary, "auto" = usb, falling back to xvf_host.
# Recalibrate (doa_calibrate.py) after switching: the two may not share the same zero angle.
DOA_SOURCE = os.environ.get("DOA_SOURCE", "auto").strip().lower()

# How often to read the direction. USB reads are cheap; xvf_host spawns a process each time.
DOA_POLL_SEC = float(os.environ.get("DOA_POLL_SEC", "0.05"))

# Recent (monotonic time, angle) readings taken WHILE THE CHIP HEARD SPEECH, so a wake
# uses the average direction of the voice instead of one jumpy (or noise) reading.
_doa_history = collections.deque(maxlen=1500)  # ~75 s of speech at 20 readings/s (catch-up needs 30 s)


def _doa_poll_loop():
    global _doa_azimuth_deg

    reader = None
    while True:
        try:
            if reader is None:
                reader = doa_reader.open_doa(DOA_SOURCE, XVF_HOST)
                print(f"    DOA source      = {reader.name}")
            sample = reader.read()
            if sample:
                raw, speech = sample
                angle = _logical_azimuth_from_hardware_deg(raw)
                with _doa_lock:
                    _doa_azimuth_deg = angle
                    if speech:
                        _doa_history.append((time.monotonic(), angle))
        except Exception as exc:
            # e.g. mic unplugged: report, wait, then reopen.
            print(f"WARNING doa: {exc!r}; retrying in 1 s")
            reader = None
            time.sleep(1.0)

        time.sleep(DOA_POLL_SEC)


threading.Thread(target=_doa_poll_loop, daemon=True).start()

app = Flask(__name__)
CORS(app)


@app.route('/state')
def flask_state():
    with _doa_lock:
        angle = _doa_azimuth_deg
    vol_raw = get_volume_level()
    vol_norm = max(0.0, min(1.0, (vol_raw - RMS_MIN) / (RMS_MAX - RMS_MIN)))
    direction = angle_to_direction_hud(angle) if vol_norm > 0.02 else None

    with _hud_mirror_lock:
        mirror = dict(_hud_mirror) if _hud_mirror is not None else {}

    stage = mirror.get("stage")
    raw_msg = (mirror.get("message") or "").strip()
    # Match oled_hud_2: directional frames clear the context message.
    if stage == "directional":
        hud_message = ""
        importance = "low"
        transcript = ""
    else:
        hud_message = raw_msg
        importance = mirror.get("importance") or "low"
        if importance not in ("low", "medium", "high"):
            importance = "low"
        transcript = mirror.get("transcript") or ""

    return jsonify({
        'direction': direction,
        'angle': round(angle, 1),
        'volume': round(vol_norm, 3),
        'danger': vol_norm >= DANGER_THRESHOLD,
        'stage': stage,
        'hud_message': hud_message,
        'importance': importance,
        'transcript': transcript,
        'event_type': mirror.get("event_type"),
        'directed_at_user': bool(mirror.get("directed_at_user", False)),
        'hud_ts': mirror.get("ts"),
    })


@app.route('/')
def flask_index():
    return send_from_directory(_ROOT, 'index.html')


@app.route('/manifest.json')
def flask_manifest():
    return send_from_directory(_ROOT, 'manifest.json')


# Last WebSocket HUD payload (Gemini/local context) — mirrored on GET /state for
# the AR PWA when no WS client is connected, or for simpler same-origin polling.
_hud_mirror_lock = threading.Lock()
_hud_mirror = None


# =========================
# Detection tuning
# =========================

# A window is only transcribed if some 0.1 s of it is at least this loud (RMS).
# Set it a bit above the room's background (see the "listening… peak=" log lines).
VOLUME_THRESHOLD = float(os.environ.get("VOLUME_THRESHOLD", "0.015"))
LOUD_FRAME_SEC = 0.1

# Print the current peak volume this often while idle, to tune VOLUME_THRESHOLD and
# to show the mic stream is still alive (0 disables).
VOLUME_HEARTBEAT_SEC = float(os.environ.get("VOLUME_HEARTBEAT_SEC", "3"))

# Rolling wake window: every WAKE_HOP_SEC, transcribe the last WAKE_WINDOW_SEC of audio.
# The window must cover hop + Whisper time + the phrase, so every "Hello Jax" lands whole
# in at least one window. faster-whisper pads input to 30 s, so a longer window costs ~nothing.
WAKE_WINDOW_SEC = float(os.environ.get("WAKE_WINDOW_SEC", "4.0"))

# Whisper (tiny.en) is only the "Hello Jax" detector; its rough transcripts are never shown on the
# display (captions come from ElevenLabs). Hidden by default so they don't drown out the real
# captions in the terminal. WAKE_LOG=1 shows them (useful when tuning the wake phrase).
WAKE_LOG = os.environ.get("WAKE_LOG", "0").strip() == "1"
WAKE_HOP_SEC = float(os.environ.get("WAKE_HOP_SEC", "1.0"))

SAMPLE_RATE = 16000

# Block size of the continuous mic stream (~64 ms at 16 kHz).
BLOCKSIZE = 1024

# Optional audio device override. Accepts:
#   - integer index (e.g. AUDIO_DEVICE=1)
#   - device name substring (e.g. AUDIO_DEVICE="XVF3800")
# Leave blank to auto-pick the first input-capable device.
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", "").strip()

# The XVF3800 sends 6 channels. Opening it as 1 channel averages all of them,
# which sounds echoey (the four mics hear each voice slightly apart). Open every
# channel and keep one: on our firmware ch0 is the processed beam (auto-gain,
# was clipping in tests), ch2 is a single raw mic (quieter but clean).
AUDIO_INPUT_CHANNELS = int(os.environ.get("AUDIO_INPUT_CHANNELS", "1"))
AUDIO_CHANNEL = int(os.environ.get("AUDIO_CHANNEL", "0"))

# How many samples of recent audio to RMS-average for the /state volume probe.
VOLUME_WINDOW_SEC = 0.2

# Similarity threshold (0–1) above which a new transcript is considered a
# duplicate of the last one and suppressed. 0.85 = "nearly identical".
TRANSCRIPT_DEDUP_THRESHOLD = float(os.environ.get("TRANSCRIPT_DEDUP_THRESHOLD", "0.85"))


# =========================
# Whisper STT config
# =========================

# Which faster-whisper model to use. "tiny.en" is fastest on Pi 4;
# "base.en" is meaningfully more accurate if latency allows.
#   WHISPER_MODEL=tiny.en
#   WHISPER_MODEL=base.en
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "tiny.en").strip()

# Beam size for Whisper decoding. Higher = more accurate, slower.
WHISPER_BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "1"))

# Set to "1" to enable Whisper's built-in VAD filter (silences silent
# padding before decoding). Recommended on; saves latency on short captures.
WHISPER_VAD_FILTER = os.environ.get("WHISPER_VAD_FILTER", "1").strip() == "1"

# Lazy-initialised singleton so we pay the model-load cost only once.
_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper():
    """Return the faster-whisper model, loading it on first call."""

    global _whisper_model

    if _whisper_model is not None:
        return _whisper_model

    with _whisper_lock:
        if _whisper_model is not None:
            return _whisper_model

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Run: pip install faster-whisper"
            )

        print(f"    Loading Whisper model '{WHISPER_MODEL}' (first call) …")
        _whisper_model = WhisperModel(
            WHISPER_MODEL,
            device="cpu",
            compute_type="int8",
            # Leave a core for the audio callback, live captions and display.
            cpu_threads=int(os.environ.get("WHISPER_CPU_THREADS", "3")),
        )
        print(f"    Whisper model ready.")

    return _whisper_model


# =========================
# Silero VAD config
# =========================

# Set to "0" to disable the Silero VAD gate (every trigger goes to STT).
# Off by default: with the rolling window, torch Silero + Whisper stalled the Pi. Whisper's own VAD is used instead.
USE_SILERO_VAD = os.environ.get("USE_SILERO_VAD", "0").strip() == "1"

_silero_model = None
_silero_utils = None
_silero_lock = threading.Lock()


def _get_silero():
    """Return (model, utils) for Silero VAD, loading on first call."""

    global _silero_model, _silero_utils

    if _silero_model is not None:
        return _silero_model, _silero_utils

    with _silero_lock:
        if _silero_model is not None:
            return _silero_model, _silero_utils

        try:
            import torch
        except ImportError:
            raise RuntimeError(
                "PyTorch is required for Silero VAD. "
                "Run: pip install torch --index-url https://download.pytorch.org/whl/cpu"
            )

        # Silero's own docs recommend one thread; otherwise torch and Whisper fight over the cores.
        torch.set_num_threads(1)

        print("    Loading Silero VAD model (first call) …")
        model, utils = torch.hub.load(
            'snakers4/silero-vad',
            'silero_vad',
            force_reload=False,
            trust_repo=True,
        )
        _silero_model = model
        _silero_utils = utils
        print("    Silero VAD ready.")

    return _silero_model, _silero_utils


def is_speech(audio_array):
    """
    Return True if Silero VAD detects at least one speech segment in the
    given mono float32 array (16 kHz). Falls back to True on any error so
    we never silently drop audio when the model misbehaves.
    """

    if not USE_SILERO_VAD:
        return True

    try:
        import torch
        model, utils = _get_silero()
        get_speech_timestamps = utils[0]

        tensor = torch.from_numpy(audio_array.copy())
        timestamps = get_speech_timestamps(
            tensor,
            model,
            sampling_rate=SAMPLE_RATE,
            threshold=0.4,          # lower = more sensitive
            min_speech_duration_ms=150,
        )
        return len(timestamps) > 0

    except Exception as exc:
        print(f"Silero VAD error (passing through): {exc}")
        return True


# =========================
# User / Gemini config
# =========================

USER_NAME = os.environ.get("USER_NAME", "Jax")

USER_NAME_ALIASES = tuple(
    a.strip().lower()
    for a in os.environ.get("USER_NAME_ALIASES", "").split(",")
    if a.strip()
)

NAME_FUZZY_THRESHOLD = float(os.environ.get("NAME_FUZZY_THRESHOLD", "0.72"))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# "Catch me up": on a wake, send the last CATCHUP_SEC of audio to ElevenLabs Scribe (batch, with
# speaker labels), find who said the name, and show their recent words on the display after the
# arrow. Needs internet + ELEVENLABS_API_KEY; the arrow/motors never wait for it. 0 disables.
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
CATCHUP_SEC = float(os.environ.get("CATCHUP_SEC", "30"))
CATCHUP_WORDS = int(os.environ.get("CATCHUP_WORDS", "45"))   # most recent words of the caller shown
CATCHUP_ARROW_SEC = 2.5   # let the arrow stay up at least this long before the text replaces it

# Live captions (live_captions.run, ElevenLabs Scribe Realtime) alongside "Hello Jax", from the
# same mic stream: Whisper/catch-up read AUDIO_CHANNEL (ch2), captions read CAPTION_AUDIO_CHANNEL
# (ch0, the chip's processed beam). Motors only ever fire for "Hello Jax", never for captions.
LIVE_CAPTIONS = os.environ.get("LIVE_CAPTIONS", "1").strip() == "1"
CAPTION_AUDIO_CHANNEL = int(os.environ.get("CAPTION_AUDIO_CHANNEL", "0"))

_caption_audio = None   # live_captions.CaptionAudio, fed from _audio_callback when captions are on
_live_people = None     # live_captions.People shared with catch-up so both use the same ?/?? labels
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_TIMEOUT_SEC = float(os.environ.get("GEMINI_TIMEOUT_SEC", "3.0"))
GEMINI_MIN_INTERVAL_SEC = float(os.environ.get("GEMINI_MIN_INTERVAL_SEC", "4.0"))
GEMINI_BACKOFF_BASE_SEC = float(os.environ.get("GEMINI_BACKOFF_BASE_SEC", "30.0"))
GEMINI_BACKOFF_CAP_SEC = float(os.environ.get("GEMINI_BACKOFF_CAP_SEC", "300.0"))

URGENT_PHRASES = ("watch out", "look out", "careful", "heads up", "move")

# Circuit-breaker state for Gemini rate limiting.
_gemini_pause_until = 0.0
_gemini_last_call = 0.0
_gemini_consecutive_429 = 0

# Last transcript seen, used for deduplication across enrichment calls.
_last_transcript = ""


# =========================
# DOA + audio capture
# =========================

def rms(audio_array):
    if len(audio_array) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio_array ** 2)))


_BUFFER_TOTAL_SEC = max(WAKE_WINDOW_SEC, CATCHUP_SEC) + 1.0
_MAX_BLOCKS = int(_BUFFER_TOTAL_SEC * SAMPLE_RATE / BLOCKSIZE) + 4

_audio_blocks = collections.deque(maxlen=_MAX_BLOCKS)
_audio_lock = threading.Lock()
_audio_stream = None

# Audio watchdog: if the mic drops off USB (loose cable, power dip), the stream silently stops
# calling back and the ring buffer freezes (Whisper then re-transcribes the same 4 s forever).
# wake_loop() reopens the stream when no audio has arrived for AUDIO_STALL_SEC.
AUDIO_STALL_SEC = 2.0
_last_audio_time = 0.0


def _audio_callback(indata, frames, time_info, status):
    global _last_audio_time
    _last_audio_time = time.monotonic()
    if status:
        print(f"audio stream: {status}")  # e.g. input overflow when the CPU is saturated
    block = indata[:, AUDIO_CHANNEL].copy()
    with _audio_lock:
        _audio_blocks.append(block)
    if _caption_audio is not None:
        # Guarded: an exception in a sounddevice callback stops the stream, which would also
        # silently stop "Hello Jax" (the ring buffer above would freeze).
        try:
            caption = np.clip(indata[:, CAPTION_AUDIO_CHANNEL], -1.0, 1.0)
            _caption_audio.feed((caption * 32767).astype(np.int16))
        except Exception as exc:
            print(f"WARNING caption feed: {exc!r}")


def _list_input_devices():
    try:
        devices = sd.query_devices()
    except Exception as exc:
        print(f"warning: couldn't query audio devices: {exc}")
        return []

    out = []
    for idx, info in enumerate(devices):
        if info.get("max_input_channels", 0) > 0:
            out.append((idx, info))
    return out


def _resolve_audio_device():
    inputs = _list_input_devices()

    if AUDIO_DEVICE:
        try:
            idx = int(AUDIO_DEVICE)
            return idx
        except ValueError:
            pass

        needle = AUDIO_DEVICE.lower()
        for idx, info in inputs:
            if needle in info.get("name", "").lower():
                return idx

        print(f"warning: AUDIO_DEVICE={AUDIO_DEVICE!r} did not match any input device")

    try:
        default = sd.default.device
        default_in = default[0] if isinstance(default, (list, tuple)) else default
        if isinstance(default_in, int) and default_in >= 0:
            return default_in
    except Exception:
        pass

    if inputs:
        return inputs[0][0]

    return None


def _print_device_list():
    inputs = _list_input_devices()
    if not inputs:
        print("  (no input-capable devices found)")
        return
    for idx, info in inputs:
        name = info.get("name", "?")
        ch = info.get("max_input_channels", 0)
        rate = int(info.get("default_samplerate", 0))
        print(f"  [{idx}] {name}  ({ch} ch, default {rate} Hz)")


def start_audio_stream():
    global _audio_stream, _last_audio_time

    if _audio_stream is not None:
        return

    device = _resolve_audio_device()

    # A bad channel index makes the callback raise, which silently freezes the ring buffer.
    if not 0 <= AUDIO_CHANNEL < AUDIO_INPUT_CHANNELS:
        raise ValueError(
            f"AUDIO_CHANNEL={AUDIO_CHANNEL} must be below AUDIO_INPUT_CHANNELS={AUDIO_INPUT_CHANNELS}"
        )

    try:
        _audio_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=AUDIO_INPUT_CHANNELS,
            dtype='float32',
            blocksize=BLOCKSIZE,
            device=device,
            callback=_audio_callback,
            # Bigger host buffer so a CPU spike (Whisper) doesn't drop audio ("input overflow").
            latency='high',
        )
        _audio_stream.start()
        _last_audio_time = time.monotonic()  # watchdog counts from now, not from program start
    except Exception as exc:
        _audio_stream = None  # so a later retry actually tries again
        print()
        print(f"ERROR: couldn't open audio stream on device {device!r}: {exc}")
        print("Available input devices:")
        _print_device_list()
        print()
        print("Fix: set AUDIO_DEVICE=<index or name substring> in your .env")
        print("     e.g.  AUDIO_DEVICE=1")
        print("     or    AUDIO_DEVICE=XVF3800")
        raise

    try:
        info = sd.query_devices(device) if device is not None else sd.query_devices(kind="input")
        name = info.get("name", str(device))
    except Exception:
        name = str(device)

    print(f"    AUDIO_DEVICE   = [{device}] {name}")


def _reopen_mic():
    """Watchdog helper (runs in a thread): abort the dead stream, re-scan devices, reopen. True if reopened."""
    global _audio_stream
    stream, _audio_stream = _audio_stream, None
    if stream is not None:
        try:
            stream.abort()  # abort, not stop: stop() waits for buffers that will never drain
            stream.close()
        except Exception as exc:
            print(f"    (closing old stream: {exc!r})")
    try:
        # PortAudio only lists devices at start-up; re-scan so the re-plugged mic is found.
        sd._terminate()
        sd._initialize()
    except Exception as exc:
        print(f"    (re-scanning audio devices: {exc!r})")
    try:
        start_audio_stream()
        print("    audio: mic reopened")
        return True
    except Exception as exc:
        print(f"    audio: couldn't reopen yet ({exc!r}); retrying")
        return False


def stop_audio_stream():
    global _audio_stream

    if _audio_stream is None:
        return

    try:
        _audio_stream.stop()
        _audio_stream.close()
    finally:
        _audio_stream = None


def _snapshot_buffer(seconds):
    with _audio_lock:
        blocks = list(_audio_blocks)

    if not blocks:
        return np.zeros(0, dtype='float32')

    audio = np.concatenate(blocks)
    needed = int(seconds * SAMPLE_RATE)

    if len(audio) > needed:
        audio = audio[-needed:]

    return audio


def get_volume_level():
    return rms(_snapshot_buffer(VOLUME_WINDOW_SEC))


def _loud_spans(audio, end_time):
    """Monotonic (start, end) times of the LOUD_FRAME_SEC frames at or above VOLUME_THRESHOLD, and the peak RMS."""

    n = int(LOUD_FRAME_SEC * SAMPLE_RATE)
    start_time = end_time - len(audio) / SAMPLE_RATE
    spans, peak = [], 0.0

    for i in range(0, len(audio) - n + 1, n):
        level = rms(audio[i:i + n])
        peak = max(peak, level)
        if level >= VOLUME_THRESHOLD:
            t = start_time + i / SAMPLE_RATE
            spans.append((t, t + LOUD_FRAME_SEC))

    return spans, peak


def _circular_mean(angles):
    """Mean of angles on a circle (359 and 1 average to 0, not 180), or None."""
    if not angles:
        return None
    x = sum(math.cos(math.radians(a)) for a in angles)
    y = sum(math.sin(math.radians(a)) for a in angles)
    if math.hypot(x, y) < 1e-9:
        return None
    return math.degrees(math.atan2(y, x)) % 360


def _angles_during(spans, pad=None):
    """Speech DOA readings whose monotonic time falls inside any (start, end) span."""
    pad = DOA_POLL_SEC if pad is None else pad  # readings are sparser than the 0.1 s frames
    with _doa_lock:
        history = list(_doa_history)
    return [a for t, a in history if any(s - pad <= t <= e + pad for s, e in spans)]


def _direction_during(spans):
    """Circular mean of the DOA readings taken during the loud parts of the window (latest reading as fallback)."""
    mean = _circular_mean(_angles_during(spans))
    if mean is None:
        with _doa_lock:
            return _doa_azimuth_deg
    return mean


def angle_difference(a, b):
    diff = abs(a - b) % 360
    return min(diff, 360 - diff)


def direction_word(angle):
    if angle is None:
        return "?"

    a = angle % 360

    if a >= 337.5 or a < 22.5:
        return "front"
    if a < 67.5:
        return "front-right"
    if a < 112.5:
        return "right"
    if a < 157.5:
        return "back-right"
    if a < 202.5:
        return "behind"
    if a < 247.5:
        return "back-left"
    if a < 292.5:
        return "left"
    return "front-left"


# =========================
# Transcription — Whisper
# =========================

def _whisper_initial_prompt():
    """
    Seed the Whisper decoder with vocabulary it should prefer. Including
    USER_NAME and aliases here biases the token probabilities toward our
    unusual name even when the acoustic signal is ambiguous.
    """
    parts = [USER_NAME]
    parts.extend(USER_NAME_ALIASES)
    parts += [
        # No "Hello/Hey {name}" phrases here: tiny.en can echo its prompt on unclear
        # audio, which would fake the wake phrase and buzz.
        "watch out", "look out", "careful", "heads up",
        "excuse me", "move",
    ]
    return ", ".join(dict.fromkeys(parts))  # deduplicated, order-preserving


def transcribe_audio(audio_array, gate=True):
    """
    gate=False skips both VAD gates (used for the start-up warm-up, so Whisper really runs).

    Transcribe a short mono float32 chunk using faster-whisper.

    Pipeline:
      1. Silero VAD gate — if no speech detected, return early.
      2. Whisper decode with USER_NAME-biased initial_prompt.
      3. Return (display_string, [alternatives]) — single-element list
         because Whisper doesn't produce ranked alternatives the way
         Google Web Speech does. The list wrapper keeps the downstream
         API identical.

    Never raises. Returns ("", []) on empty input or any failure.
    """

    if len(audio_array) == 0:
        return "", []

    t0 = time.monotonic()

    # VAD gate: skip STT entirely if no speech detected in the chunk.
    if gate and not is_speech(audio_array):
        return "", []

    t1 = time.monotonic()

    try:
        model = _get_whisper()
    except RuntimeError as exc:
        print(f"Whisper unavailable: {exc}")
        return "", []

    try:
        segments, _info = model.transcribe(
            audio_array,
            language="en",
            initial_prompt=_whisper_initial_prompt(),
            beam_size=WHISPER_BEAM_SIZE,
            vad_filter=WHISPER_VAD_FILTER and gate,
            # One decoding pass only. By default Whisper re-decodes up to 5 more times when the
            # text looks repetitive ("hello jax, hello jax, ..."), which took 10-36 s on the Pi.
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            # A 4 s window is ~10-15 words; cap runaway repetition loops.
            max_new_tokens=60,
            # word_timestamps adds latency — skip unless you need word timing
        )
        text = " ".join(s.text.strip() for s in segments).strip()
    except Exception as exc:
        print(f"Whisper transcription error: {exc}")
        return "", []

    if gate and WAKE_LOG:
        print(f"    timing: silero {t1 - t0:.2f}s, whisper {time.monotonic() - t1:.2f}s")

    if not text:
        return "", []

    return text, [text]


# =========================
# Transcript deduplication
# =========================

def is_new_transcript(text):
    """
    Return True if `text` is meaningfully different from the last seen
    transcript. Suppresses near-duplicate enrichment calls that arise
    when two loud events overlap and produce almost identical STT output.

    Updates the global _last_transcript on every non-empty, non-duplicate.
    """

    global _last_transcript

    if not text:
        return False

    ratio = difflib.SequenceMatcher(
        None, text.lower(), _last_transcript.lower()
    ).ratio()

    if ratio > TRANSCRIPT_DEDUP_THRESHOLD:
        return False

    _last_transcript = text
    return True


# =========================
# Local rule-based reasoning
# =========================

_WORD_RE = re.compile(r"[a-zA-Z']+")


def _name_targets():
    targets = [USER_NAME.lower()]
    targets.extend(a for a in USER_NAME_ALIASES if a)
    return [t for t in targets if t]


# ---------------------------------------------------------------------------
# Phonetic matching (jellyfish)
# ---------------------------------------------------------------------------

def _phonetic_name_match(word):
    """
    Return True if `word` sounds like USER_NAME or any alias according to
    Metaphone or Soundex. Catches STT errors that are acoustically plausible
    but differ in spelling — e.g. "spook" vs "spoot".

    Falls back to False (no match) if jellyfish is not installed, so the
    rest of the pipeline still works without it.
    """

    try:
        import jellyfish
    except ImportError:
        return False

    word_l = word.lower()
    targets = _name_targets()

    for target in targets:
        try:
            if jellyfish.metaphone(word_l) == jellyfish.metaphone(target):
                return True
        except Exception:
            pass
        try:
            if jellyfish.soundex(word_l) == jellyfish.soundex(target):
                return True
        except Exception:
            pass

    return False


def _match_one_alternative(alt, targets):
    """
    Score a single STT alternative against every name target.

    Match priority:
      1. Substring (exact containment) — ratio 1.0, kind "substring"
      2. Phonetic (Metaphone / Soundex) — ratio 0.95, kind "phonetic"
      3. Fuzzy (SequenceMatcher)        — ratio varies, kind "fuzzy"

    Returns a match dict or None.
    """

    if not alt:
        return None

    text = alt.lower()
    words = _WORD_RE.findall(text)

    # 1. Substring
    for target in targets:
        if target in text:
            matched_word = next(
                (w for w in words if target in w),
                target,
            )
            return {
                "alternative": alt,
                "target": target,
                "word": matched_word,
                "ratio": 1.0,
                "kind": "substring",
            }

    # 2. Phonetic
    for word in words:
        if _phonetic_name_match(word):
            # Find which target it sounded like.
            target_hit = next(
                (t for t in targets if _phonetic_name_match_pair(word, t)),
                targets[0],
            )
            return {
                "alternative": alt,
                "target": target_hit,
                "word": word,
                "ratio": 0.95,
                "kind": "phonetic",
            }

    # 3. Fuzzy
    if NAME_FUZZY_THRESHOLD <= 0:
        return None

    best = None
    for word in words:
        for target in targets:
            ratio = difflib.SequenceMatcher(None, word, target).ratio()
            if ratio < NAME_FUZZY_THRESHOLD:
                continue
            if best is None or ratio > best["ratio"]:
                best = {
                    "alternative": alt,
                    "target": target,
                    "word": word,
                    "ratio": ratio,
                    "kind": "fuzzy",
                }

    return best


def _phonetic_name_match_pair(word, target):
    """Return True if `word` sounds like `target` via Metaphone or Soundex."""

    try:
        import jellyfish
        word_l = word.lower()
        target_l = target.lower()
        return (
            jellyfish.metaphone(word_l) == jellyfish.metaphone(target_l) or
            jellyfish.soundex(word_l) == jellyfish.soundex(target_l)
        )
    except Exception:
        return False


def name_match_details(text_or_alts):
    """
    Score the user's name across one or many STT hypotheses.

    Returns a match dict or None.
    """

    if not text_or_alts:
        return None

    alternatives = [text_or_alts] if isinstance(text_or_alts, str) else list(text_or_alts)
    targets = _name_targets()

    if not targets:
        return None

    best = None
    for alt in alternatives:
        match = _match_one_alternative(alt, targets)
        if match is None:
            continue
        if match["kind"] in ("substring", "phonetic"):
            return match
        if best is None or match["ratio"] > best["ratio"]:
            best = match

    return best


def detect_name(text_or_alts):
    return name_match_details(text_or_alts) is not None


# Wake phrase: a greeting followed by the name ("Hello Jax", "hi Jacks", "hello there Jax").
# Only this triggers the motors; the name on its own ("Jax said...") does not.
WAKE_GREETINGS = tuple(
    g.strip().lower()
    for g in os.environ.get("WAKE_GREETINGS", "hello,helo,hallo,hullo,hi,hey,hiya").split(",")
    if g.strip()
)

# How many words may sit between the greeting and the name ("hello THERE jax").
WAKE_MAX_GAP = max(0, int(os.environ.get("WAKE_MAX_GAP", "1")))

# Extra spellings accepted only right after a greeting. "jack" is Whisper's most likely
# spelling of Jax; it's safe here because a greeting is required ("hello jack").
WAKE_EXTRA_NAMES = tuple(
    n.strip().lower()
    for n in os.environ.get("WAKE_EXTRA_NAMES", "jack").split(",")
    if n.strip()
)

# Overlapping rolling windows contain the same "Hello Jax" for up to WAKE_WINDOW_SEC;
# ignore repeat wakes for longer than that so it isn't announced twice.
WAKE_COOLDOWN_SEC = float(os.environ.get("WAKE_COOLDOWN_SEC", "4.5"))

_last_wake = 0.0


def _is_wake_name(word, targets):
    """
    Stricter than name_match_details(): exact word or same Metaphone code only.
    Soundex and fuzzy matching are too loose for a short name like Jax
    ("hi Josh", "hey, come back" would buzz).
    """

    if word in targets:
        return True

    try:
        import jellyfish
        code = jellyfish.metaphone(word)
        return any(code == jellyfish.metaphone(t) for t in targets)
    except Exception:
        return False


def wake_phrase_detected(text_or_alts):
    """Return True if any STT hypothesis contains a greeting followed by the user's name."""

    if not text_or_alts:
        return False

    alternatives = [text_or_alts] if isinstance(text_or_alts, str) else list(text_or_alts)
    targets = _name_targets() + list(WAKE_EXTRA_NAMES)

    for alt in alternatives:
        # Strip apostrophes so "Jack's" -> "jacks".
        words = _WORD_RE.findall((alt or "").lower().replace("'", ""))
        for i, word in enumerate(words):
            if word in WAKE_GREETINGS or not _is_wake_name(word, targets):
                continue
            before = words[max(0, i - 1 - WAKE_MAX_GAP):i]
            if any(b in WAKE_GREETINGS for b in before):
                return True

    return False


def local_reason(transcript, angle, volume, name_detected):
    text = (transcript or "").lower()
    where = direction_word(angle)

    if name_detected:
        return {
            "event_type": "speech",
            "directed_at_user": True,
            "importance": "high",
            "message": f"Your name was called from {where}",
        }

    if any(phrase in text for phrase in URGENT_PHRASES):
        return {
            "event_type": "warning",
            "directed_at_user": True,
            "importance": "high",
            "message": f"Warning from {where}",
        }

    if text:
        return {
            "event_type": "speech",
            "directed_at_user": False,
            "importance": "low",
            "message": f"Voice from {where}",
        }

    return {
        "event_type": "unknown",
        "directed_at_user": False,
        "importance": "low",
        "message": f"Sound from {where}",
    }


# =========================
# Gemini reasoning layer
# =========================

GEMINI_PROMPT_TEMPLATE = """You are helping a user who is deaf in one ear understand whether a sound is worth looking at.

User name: {user_name}
Transcript: {transcript}
Sound angle in degrees: {angle}
Volume RMS: {volume}

Decide if this sound is likely directed at the user or important enough to alert them.

Return ONLY valid JSON with this exact shape:
{{
  "directed_at_user": true,
  "importance": "low|medium|high",
  "event_type": "speech|warning|background|unknown",
  "message": "short HUD-friendly message"
}}

Rules:
- If the transcript contains the user's name, directed_at_user should be true and importance should be high.
- If the transcript includes urgent phrases like 'watch out', 'look out', 'careful', or 'move', importance should be high.
- If it seems like background conversation, importance should be low.
- Keep message under 8 words.
- Do not include markdown, explanation, or extra keys.
"""


def _coerce_gemini_result(result):
    if not isinstance(result, dict):
        return None

    importance = result.get("importance", "low")
    if importance not in ("low", "medium", "high"):
        importance = "low"

    event_type = result.get("event_type", "unknown")
    if event_type not in ("speech", "warning", "background", "unknown"):
        event_type = "unknown"

    return {
        "event_type": event_type,
        "directed_at_user": bool(result.get("directed_at_user", False)),
        "importance": importance,
        "message": str(result.get("message", "Sound detected"))[:80],
    }


def _parse_retry_after(headers):
    if not headers:
        return None

    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return None

    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def _event_tag(angle, volume):
    angle_str = f"{angle:.1f}°" if angle is not None else "?°"
    return f"[a={angle_str}/v={volume:.4f}]"


def _fmt_classification(reasoning):
    return (
        f"importance={reasoning.get('importance', '?'):<6} "
        f"event={reasoning.get('event_type', '?'):<10} "
        f"directed={reasoning.get('directed_at_user', False)!s:<5} "
        f"msg=\"{reasoning.get('message', '')}\""
    )


def _gemini_skip_reason(transcript, alternatives=None):
    if not GEMINI_API_KEY:
        return "no API key"

    if not transcript:
        return "no transcript"

    text = transcript.lower()

    name_target = alternatives if alternatives else transcript

    if detect_name(name_target):
        pass
    else:
        social_phrases = (
            "watch out",
            "look out",
            "careful",
            "heads up",
            "move",
            "excuse me",
            "hey",
        )
        if not any(phrase in text for phrase in social_phrases):
            return "no important keywords"

    now = time.monotonic()

    if now < _gemini_pause_until:
        remaining = _gemini_pause_until - now
        return f"paused {remaining:.0f}s after 429"

    since_last = now - _gemini_last_call
    if since_last < GEMINI_MIN_INTERVAL_SEC:
        wait = GEMINI_MIN_INTERVAL_SEC - since_last
        return f"throttled, wait {wait:.1f}s"

    return ""


def call_gemini_blocking(transcript, angle, volume, alternatives=None):
    global _gemini_pause_until, _gemini_last_call, _gemini_consecutive_429

    tag = _event_tag(angle, volume)

    skip = _gemini_skip_reason(transcript, alternatives=alternatives)
    if skip:
        print(f"{tag} gemini: SKIPPED ({skip})")
        return None

    print(f"{tag} gemini: ATTEMPTING transcript=\"{transcript}\"")

    prompt = GEMINI_PROMPT_TEMPLATE.format(
        user_name=USER_NAME,
        transcript=transcript or "",
        angle=f"{angle:.1f}" if angle is not None else "?",
        volume=f"{volume:.4f}",
    )

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )

    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    _gemini_last_call = time.monotonic()

    try:
        with urllib.request.urlopen(request, timeout=GEMINI_TIMEOUT_SEC) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            retry_after = _parse_retry_after(exc.headers)
            _gemini_consecutive_429 += 1
            backoff = GEMINI_BACKOFF_BASE_SEC * (2 ** (_gemini_consecutive_429 - 1))
            pause = max(retry_after or 0.0, backoff)
            pause = min(pause, GEMINI_BACKOFF_CAP_SEC)
            _gemini_pause_until = time.monotonic() + pause
            print(f"{tag} gemini: 429 rate-limited, pausing {pause:.0f}s")
        else:
            print(f"{tag} gemini: HTTP {exc.code} {exc.reason}")
        return None
    except urllib.error.URLError as exc:
        print(f"{tag} gemini: HTTP error {exc}")
        return None
    except Exception as exc:
        print(f"{tag} gemini: error {exc}")
        return None

    _gemini_consecutive_429 = 0

    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        print(f"{tag} gemini: bad JSON ({exc})")
        return None

    result = _coerce_gemini_result(parsed)
    if result is None:
        print(f"{tag} gemini: bad payload, ignoring")
    else:
        print(f"{tag} gemini: returned {_fmt_classification(result)}")

    return result


async def call_gemini(transcript, angle, volume, alternatives=None):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                call_gemini_blocking,
                transcript, angle, volume, alternatives,
            ),
            timeout=GEMINI_TIMEOUT_SEC + 0.5,
        )
    except asyncio.TimeoutError:
        print(f"{_event_tag(angle, volume)} gemini: TIMEOUT")
        return None


# =========================
# Payload + broadcasting
# =========================

def make_payload(angle, volume, *, stage, transcript="",
                 name_detected=False, reasoning=None):
    payload = {
        "stage": stage,
        "ts": time.time(),
        "angle": angle,
        "volume": volume,
        "transcript": transcript,
        "name_detected": name_detected,
        "event_type": "unknown",
        "directed_at_user": False,
        "importance": "low",
        "message": f"Sound from {direction_word(angle)}",
    }

    if reasoning:
        payload.update(reasoning)

    return payload


def _record_hud_mirror(payload):
    """Keep a copy of the latest HUD payload for HTTP /state (AR PWA polling)."""

    global _hud_mirror

    with _hud_mirror_lock:
        _hud_mirror = dict(payload)


async def broadcast(payload):
    _record_hud_mirror(payload)

    if not CLIENTS:
        return

    msg = json.dumps(payload)

    await asyncio.gather(
        *[c.send(msg) for c in CLIENTS.copy()],
        return_exceptions=True,
    )


# =========================
# Pipeline: HUD reporting (off the wake path)
# =========================

# =========================
# Catch me up (ElevenLabs Scribe, off the wake path)
# =========================

_background_tasks = set()  # keep references so running tasks aren't garbage-collected


def _spawn(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


TRACK_WINDOW_SEC = 0.4   # fresh speech readings used to follow the speaker during a guide
TRACK_MAX_JUMP = 60      # ignore readings this far from where we expect them (someone else talking)


def _track_speaker(expected):
    """
    For haptics.guide(): the speaker's current head-relative direction while they keep talking
    (the mic is on the head, so it changes as the wearer turns), or None if there's no fresh
    speech reading near where we expect them.
    """
    cutoff = time.monotonic() - TRACK_WINDOW_SEC
    with _doa_lock:
        angles = [a for t, a in _doa_history if t >= cutoff]
    mean = _circular_mean(angles)
    if mean is None or angle_difference(mean, expected) > TRACK_MAX_JUMP:
        return None
    return mean


def _speaker_directions(turns, clip_start):
    """Each Scribe speaker's direction: mean of the speech DOA readings taken while they were talking."""
    out = {}
    for speaker in {t[0] for t in turns}:
        spans = [(clip_start + s, clip_start + e) for sp, s, e, _ in turns if sp == speaker]
        mean = _circular_mean(_angles_during(spans, pad=0.1))
        if mean is not None:
            out[speaker] = mean
    return out


def _find_caller(turns, directions, wake_angle):
    """Who said the name: the last speaker with a name word, else whoever sits nearest the wake direction."""
    names = set(_name_targets()) | set(WAKE_EXTRA_NAMES)
    for speaker, _, _, words in reversed(turns):
        if any(re.sub(r"[^a-z]", "", w.lower()) in names for w in words):
            return speaker
    if directions:
        return min(directions, key=lambda sp: angle_difference(directions[sp], wake_angle))
    return turns[-1][0]


async def _catch_up(wake_angle, wake_time):
    """Show what the caller said in the last CATCHUP_SEC, after the arrow. Never raises."""
    try:
        audio = _snapshot_buffer(CATCHUP_SEC)
        clip_start = time.monotonic() - len(audio) / SAMPLE_RATE
        result, secs = await asyncio.to_thread(
            scribe.transcribe, scribe.wav_bytes(audio, SAMPLE_RATE), ELEVENLABS_API_KEY)
        turns = scribe.speaker_turns(result)
        if not turns:
            print(f"    catch-up: Scribe heard nothing ({secs:.1f}s)")
            return

        labels = scribe.unknown_labels(turns)
        directions = _speaker_directions(turns, clip_start)
        caller = _find_caller(turns, directions, wake_angle)
        print(f"    catch-up: Scribe {secs:.1f}s, {len(labels)} speaker(s), caller {labels[caller]}")
        for speaker, start, end, words in turns:
            where = display_cue.direction(directions[speaker]) if speaker in directions else "?"
            print(f"      {labels[speaker]:<4} {where:<6} {' '.join(words)}")

        words = scribe.clean_words([w for sp, _, _, ws in turns if sp == caller for w in ws])
        text = " ".join(words[-CATCHUP_WORDS:])
        if len(words) > CATCHUP_WORDS:
            text = "... " + text
        where = display_cue.direction(directions.get(caller, wake_angle))

        wait = wake_time + CATCHUP_ARROW_SEC - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        # Same label the live captions use for that direction, so "??" means the same person.
        # With live captions on but no match, show no label rather than a clashing one (the
        # arrow still points at them). Without live captions, Scribe's own ?/?? is fine.
        label = labels[caller]
        if _live_people is not None:
            # Simple name rules on the voice-identified turns too ("I'm Sam" -> that person = SAM).
            for speaker, _, _, ws in turns:
                name = scribe.find_self_name(" ".join(scribe.clean_words(ws)),
                                             exclude=[USER_NAME, *USER_NAME_ALIASES])
                person = _live_people.nearest(directions[speaker]) if speaker in directions else None
                if name and person:
                    _live_people.set_name(person, name)
            person = _live_people.nearest(directions[caller]) if caller in directions else None
            label = _live_people.display(person) or ""
        display_cue.show_caption_async(label, text, where)
    except Exception:
        print("    catch-up failed (arrow/motors unaffected):")
        traceback.print_exc()


# =========================
# Live captions (ElevenLabs Scribe Realtime, alongside "Hello Jax")
# =========================

class _ServerDirection:
    """live_captions' direction interface on top of this server's DOA thread (speech readings only)."""

    def __init__(self, window):
        self.window = window

    def current(self):
        cutoff = time.monotonic() - self.window
        with _doa_lock:
            angles = [a for t, a in _doa_history if t >= cutoff]
        return _circular_mean(angles)

    def silent_for(self):
        with _doa_lock:
            last = _doa_history[-1][0] if _doa_history else 0.0
        return time.monotonic() - last

    def angles_between(self, start, end):
        with _doa_lock:
            return [a for t, a in _doa_history if start <= t <= end]


async def _live_caption_loop(live_captions):
    """Keep a Scribe Realtime session running; reconnect with backoff (e.g. hotspot drops)."""
    print("    live captions: task started")
    direction = _ServerDirection(live_captions.DIRECTION_WINDOW)
    backoff = {"sec": 2.0}

    def connected():
        backoff["sec"] = 2.0

    while True:
        try:
            await live_captions.run(ELEVENLABS_API_KEY, _caption_audio, direction, _live_people,
                                    on_ready=connected)
            print("    live captions: connection closed, reconnecting")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"WARNING live captions: {exc!r}; retrying in {backoff['sec']:.0f}s "
                  "(Hello Jax is unaffected)")
        await asyncio.sleep(backoff["sec"])
        backoff["sec"] = min(backoff["sec"] * 2, 30.0)


async def _report(transcript, alternatives, angle, volume):
    """Dedupe overlapping windows' transcripts, then broadcast to the HUD/OLED clients."""

    if not transcript or not is_new_transcript(transcript):
        return

    name_detected = detect_name(alternatives)
    local = local_reason(transcript, angle, volume, name_detected)
    await broadcast(make_payload(
        angle, volume,
        stage="final",
        transcript=transcript,
        name_detected=name_detected,
        reasoning=local,
    ))

    # Gemini is slow; never let it hold up the next window. No key: skip entirely.
    if GEMINI_API_KEY:
        _spawn(_gemini_update(transcript, alternatives, angle, volume, name_detected))


async def _gemini_update(transcript, alternatives, angle, volume, name_detected):
    gemini = await call_gemini(transcript, angle, volume, alternatives=alternatives)
    if gemini is None:
        return

    if name_detected:
        gemini["directed_at_user"] = True
        gemini["importance"] = "high"

    print(f"{_event_tag(angle, volume)} FINAL (gemini): {_fmt_classification(gemini)}")
    await broadcast(make_payload(
        angle, volume,
        stage="final",
        transcript=transcript,
        name_detected=name_detected,
        reasoning=gemini,
    ))


# =========================
# Pipeline: poll loop
# =========================

async def handle_client(websocket):
    CLIENTS.add(websocket)
    print(f"Client connected. Total: {len(CLIENTS)}")

    try:
        await websocket.wait_closed()
    finally:
        CLIENTS.discard(websocket)
        print(f"Client disconnected. Total: {len(CLIENTS)}")


async def wake_loop():
    """
    Rolling wake-phrase window. Every WAKE_HOP_SEC (or as soon as Whisper finishes, if slower),
    transcribe the last WAKE_WINDOW_SEC of audio, if any part of it was loud. Every "Hello Jax"
    lands whole, with silence after it, in at least one window, and nothing is lost while Whisper
    is busy: the next window simply covers what was said meanwhile.
    """

    global _last_wake, _last_audio_time
    last_heartbeat = 0.0
    reopen_failures = 0

    while True:
        tick = time.monotonic()

        if tick - _last_audio_time > AUDIO_STALL_SEC:
            # Mic stopped delivering audio (e.g. USB drop-out): drop the frozen audio and reopen.
            # The PortAudio calls can block on a vanished device, so they run off the event loop.
            print(f"WARNING audio: no audio for {tick - _last_audio_time:.1f}s, reopening the mic...")
            with _audio_lock:
                _audio_blocks.clear()
            ok = await asyncio.to_thread(_reopen_mic)
            reopen_failures = 0 if ok else reopen_failures + 1
            # Give the reopened stream AUDIO_STALL_SEC; back off up to 10 s if the mic stays missing.
            _last_audio_time = time.monotonic() + min(10.0, 2.0 * reopen_failures)
            await asyncio.sleep(1.0)
            continue

        audio = _snapshot_buffer(WAKE_WINDOW_SEC)
        spans, peak = _loud_spans(audio, time.monotonic())

        if not spans:
            if VOLUME_HEARTBEAT_SEC and tick - last_heartbeat >= VOLUME_HEARTBEAT_SEC:
                last_heartbeat = tick
                print(f"    listening… peak={peak:.4f} (threshold {VOLUME_THRESHOLD:.4f})")
            await asyncio.sleep(WAKE_HOP_SEC)
            continue

        try:
            angle = _direction_during(spans)
            tag = _event_tag(angle, peak)
            await broadcast(make_payload(angle, peak, stage="directional"))

            t0 = time.monotonic()
            transcript, alternatives = await asyncio.to_thread(transcribe_audio, audio)
            if WAKE_LOG:
                print(f"{tag} whisper (wake check only, not captions): \"{transcript}\" "
                      f"({time.monotonic() - t0:.2f}s)")

            # Wake check first, before dedupe or broadcasting: this is the latency-critical path.
            # Only the wake phrase ("Hello Jax" or a variation) triggers, not the name on its own.
            if wake_phrase_detected(alternatives):
                # Use the window's capture time, not "after Whisper": Whisper's 0.8-1.6 s jitter
                # would otherwise let the same phrase slip past the cooldown and announce twice.
                now = tick
                if now - _last_wake >= WAKE_COOLDOWN_SEC:
                    _last_wake = now
                    haptics.guide(angle, track=_track_speaker)
                    display_cue.show_async(angle)
                    print(f"{tag} wake phrase: BUZZ toward {display_cue.direction(angle)} ({angle:.0f} deg)")
                    if CATCHUP_SEC and ELEVENLABS_API_KEY:
                        _spawn(_catch_up(angle, time.monotonic()))
                else:
                    print(f"{tag} wake phrase: same phrase still in the window, not re-announcing")

            await _report(transcript, alternatives, angle, peak)
        except Exception:
            # Never let one bad window kill the loop, but show what went wrong.
            traceback.print_exc()

        await asyncio.sleep(max(0.0, WAKE_HOP_SEC - (time.monotonic() - tick)))


def run_flask_server():
    kwargs = dict(
        host='0.0.0.0', port=5000, debug=False, threaded=True, use_reloader=False,
    )
    cert = os.path.join(_ROOT, '192.168.68.55.pem')
    key = os.path.join(_ROOT, '192.168.68.55-key.pem')
    if os.path.isfile(cert) and os.path.isfile(key):
        kwargs['ssl_context'] = (cert, key)
        print("🖥️  Flask HUD: https://0.0.0.0:5000  (/state, index.html)")
    else:
        print(
            "🖥️  Flask HUD: http://0.0.0.0:5000  "
            "(add 192.168.68.55.pem + key next to server.py for HTTPS)"
        )
    app.run(**kwargs)


async def main():
    print("🎙️  DOA WebSocket server starting on ws://0.0.0.0:8765 (+ Flask on :5000)")

    if _DOTENV_LOADED:
        print(f"    .env            = loaded {_DOTENV_LOADED} var(s)")
    else:
        print("    .env            = not loaded (file missing or empty)")

    print(f"    USER_NAME       = {USER_NAME}")

    if USER_NAME_ALIASES:
        print(f"    ALIASES         = {', '.join(USER_NAME_ALIASES)}")

    print(f"    FUZZY THRESH    = {NAME_FUZZY_THRESHOLD}")
    print(f"    WHISPER_MODEL   = {WHISPER_MODEL}  (beam={WHISPER_BEAM_SIZE}, vad={WHISPER_VAD_FILTER})")
    print(f"    SILERO_VAD      = {'enabled' if USE_SILERO_VAD else 'disabled'}")
    print(f"    GEMINI_MODEL    = {GEMINI_MODEL}")
    print(f"    GEMINI_KEY      = {'set' if GEMINI_API_KEY else 'NOT set (local fallback only)'}")
    print(f"    WAKE WINDOW     = last {WAKE_WINDOW_SEC:.1f}s every {WAKE_HOP_SEC:.1f}s, threshold {VOLUME_THRESHOLD:.4f}")
    if CATCHUP_SEC and ELEVENLABS_API_KEY:
        print(f"    CATCH-UP        = last {CATCHUP_SEC:.0f}s via ElevenLabs Scribe after each wake")
    else:
        print("    CATCH-UP        = off (set ELEVENLABS_API_KEY in .env to enable)")
    if LIVE_CAPTIONS and ELEVENLABS_API_KEY:
        print(f"    LIVE CAPTIONS   = on (Scribe Realtime, mic channel {CAPTION_AUDIO_CHANNEL})")
    else:
        print("    LIVE CAPTIONS   = off (needs LIVE_CAPTIONS=1 and ELEVENLABS_API_KEY)")
    print(f"    WAKE NAMES      = {', '.join(_name_targets() + list(WAKE_EXTRA_NAMES))} after {', '.join(WAKE_GREETINGS)}")
    print(f"    AUDIO CHANNEL   = {AUDIO_CHANNEL} of {AUDIO_INPUT_CHANNELS}")
    print(f"    DOA             = {DOA_SOURCE}, flip {'on' if DOA_FLIP_LEFT_RIGHT else 'off'}, offset {DOA_OFFSET_DEG:.0f} deg, poll {DOA_POLL_SEC:.2f}s")

    # Eagerly load heavy models at startup so the first real event isn't slow.
    print("    Pre-loading models …")
    try:
        await asyncio.to_thread(_get_whisper)
    except Exception as exc:
        print(f"    WARNING: Whisper pre-load failed: {exc}")

    if USE_SILERO_VAD:
        try:
            await asyncio.to_thread(_get_silero)
        except Exception as exc:
            print(f"    WARNING: Silero VAD pre-load failed: {exc}")

    # Warm up with one window-sized call: the first real transcription took ~17 s cold.
    print("    Warming up speech models …")
    t0 = time.monotonic()
    warm = (0.01 * np.random.randn(int(WAKE_WINDOW_SEC * SAMPLE_RATE))).astype("float32")
    await asyncio.to_thread(transcribe_audio, warm, False)
    print(f"    Warm-up done in {time.monotonic() - t0:.1f}s")

    # Live captions: set up the caption feed BEFORE the mic stream starts calling _audio_callback.
    # Imported here, after .env is loaded, because live_captions reads its settings at import.
    global _caption_audio, _live_people
    caption_task = None
    if LIVE_CAPTIONS and ELEVENLABS_API_KEY:
        if not 0 <= CAPTION_AUDIO_CHANNEL < AUDIO_INPUT_CHANNELS:
            print(f"ERROR: CAPTION_AUDIO_CHANNEL={CAPTION_AUDIO_CHANNEL} must be below "
                  f"AUDIO_INPUT_CHANNELS={AUDIO_INPUT_CHANNELS}; live captions off")
        else:
            import live_captions
            _live_people = live_captions.People()
            _caption_audio = live_captions.CaptionAudio(asyncio.get_running_loop())
            caption_task = asyncio.create_task(_live_caption_loop(live_captions))

    try:
        start_audio_stream()
    except Exception as exc:
        print(f"ERROR: couldn't start audio: {exc}")
        return

    threading.Thread(target=run_flask_server, daemon=True).start()

    try:
        async with websockets.serve(handle_client, "0.0.0.0", 8765):
            await wake_loop()
    finally:
        if caption_task:
            caption_task.cancel()
        stop_audio_stream()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        stop_audio_stream()
        print("\nServer stopped.")
