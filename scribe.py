"""
ElevenLabs Scribe helpers shared by server.py (catch-up), scribe_test.py and live_captions.py.

Batch Scribe v2 (POST /v1/speech-to-text): accurate, labels speakers (diarize), word timestamps.
"""

import io
import os
import time
import wave

import numpy as np
import requests

API_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def load_env(path):
    """Minimal .env loader (existing environment variables win)."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))


def wav_bytes(audio, rate=16000):
    """Mono audio (float32 in -1..1, or int16) -> 16-bit WAV bytes."""
    audio = np.asarray(audio)
    if audio.dtype != np.int16:
        audio = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio.tobytes())
    return buf.getvalue()


def transcribe(wav, key, timeout=30):
    """Send WAV bytes to batch Scribe with speaker labels. Returns (result_json, seconds_taken)."""
    t0 = time.monotonic()
    data = {
        "model_id": "scribe_v2",
        "language_code": "en",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    r = requests.post(
        API_URL,
        headers={"xi-api-key": key},
        data=data,
        files={"file": ("audio.wav", wav, "audio/wav")},
        timeout=timeout,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Scribe error {r.status_code}: {r.text[:300]}")
    return r.json(), time.monotonic() - t0


# Hesitation sounds dropped from captions ("uh-huh" / "mm-hmm" are kept: they mean yes).
FILLERS = {"uh", "um", "uhm", "umm", "er", "erm", "ah", "eh", "hmm", "hm", "mm"}


def _norm(word):
    return "".join(ch for ch in word.lower() if ch.isalnum() or ch in "'-").strip("-'")


def is_filler(word):
    return _norm(word) in FILLERS


def clean_words(words):
    """Drop filler sounds and collapse stutters ("I, I, I wonder" -> "I wonder") for easier reading."""
    out, previous = [], None
    for w in words:
        n = _norm(w)
        if not n or n in FILLERS or n == previous:
            continue
        out.append(w)
        previous = n
    return out


def clean_text(text):
    return " ".join(clean_words(text.split()))


def speaker_turns(result):
    """Group consecutive words by speaker: [[speaker_id, start, end, [words]], ...] (times in seconds into the clip)."""
    turns = []
    for w in result.get("words", []):
        if w.get("type") == "spacing":
            continue
        # A sound tag without a speaker belongs to whoever was talking; don't invent a speaker.
        speaker = w.get("speaker_id") or (turns[-1][0] if turns else None)
        if speaker is None:
            continue
        text = w["text"] if w.get("type") == "word" else f"[{w['text']}]"
        if turns and turns[-1][0] == speaker:
            turns[-1][2] = w.get("end", turns[-1][2])
            turns[-1][3].append(text)
        else:
            turns.append([speaker, w.get("start", 0.0), w.get("end", 0.0), [text]])
    return turns


def unknown_labels(turns):
    """speaker_0 -> '?', speaker_1 -> '??', ... in order of first appearance (names come later)."""
    labels = {}
    for speaker, *_ in turns:
        labels.setdefault(speaker, "?" * (len(labels) + 1))
    return labels
