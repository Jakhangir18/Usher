"""
ElevenLabs Scribe helpers shared by server.py (catch-up), scribe_test.py and live_captions.py.

Batch Scribe v2 (POST /v1/speech-to-text): accurate, labels speakers (diarize), word timestamps.
"""

import io
import os
import re
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


# Self-introductions: "I'm Sam", "I am Sam", "my name is Sam", "my name's Sam", "call me Sam".
# Not "this is Sam" (usually introducing someone else, so it would name the wrong person).
# The phrase is case-insensitive; the name must be capitalised (Scribe capitalises names).
_INTRO = re.compile(r"\b(?i:i'?m|i am|my name is|my name's|call me),?\s+([A-Z][a-z]{1,15})\b")

# Capitalised words that follow "I'm" but aren't names.
_NOT_NAMES = {
    "okay", "ok", "sorry", "fine", "good", "great", "here", "ready", "not", "just", "so", "gonna",
    "going", "sure", "done", "back", "home", "in", "on", "the", "a", "an", "also", "still", "really",
    "very", "pretty", "trying", "testing", "talking", "doing", "like", "yeah", "yes", "no", "well",
    "actually", "good", "fine", "tired", "hungry", "confused", "excited", "happy", "sad", "glad",
    "afraid", "with", "from", "at", "over", "out", "up", "down", "kidding", "serious", "late", "new",
}


def find_self_name(text, exclude=()):
    """Name from a self-introduction in `text` ("Hi, I'm Sam" -> "Sam"), or None."""
    skip = {n.lower() for n in exclude}
    for match in _INTRO.finditer(text.replace("’", "'")):
        name = match.group(1)
        if name.lower() not in _NOT_NAMES and name.lower() not in skip:
            return name
    return None


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
