"""
Make the two-voice test conversation used by the live tests, with Gemini TTS (multi-speaker).

    GEMINI_API_KEY=... python jak-claudenotes/tools/make_test_wav.py /tmp/usher-test.wav

Writes a 16 kHz mono 16-bit WAV (about 11 s). Sam says "Hello Jax ... Hi, I'm Sam", Oliver answers,
so one clip exercises the wake rule, diarization, catch-up's caller choice and the name rule.
Never commit the WAV (*.wav is gitignored).
"""

import base64
import io
import json
import os
import sys
import urllib.request
import wave

import numpy as np

MODEL = "gemini-3.8-flash-lite-tts"
LINES = [
    ("Sam", "Hey, are you coming to the cafe after the workshop?"),
    ("Oliver", "Yeah, give me a minute to pack up."),
    ("Sam", "Hello Jax, we saved you a seat over here. Hi, I'm Sam."),
    ("Oliver", "Of course, the more the merrier."),
]
VOICES = {"Sam": "Kore", "Oliver": "Puck"}


def synthesize(key):
    body = {
        "contents": [{"parts": [{"text": t, "speechMetadata": {"speaker": s}} for s, t in LINES]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"multiSpeakerVoiceConfig": {"speakerVoiceConfigs": [
                {"speaker": s, "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v}}}
                for s, v in VOICES.items()]}},
        },
    }
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=120) as r:
        parts = json.load(r)["candidates"][0]["content"]["parts"]
    inline = next(p["inlineData"] for p in parts if p.get("inlineData"))
    return base64.b64decode(inline["data"]), inline["mimeType"]


def to_wav16k(raw, mime):
    if mime.startswith("audio/wav"):
        with wave.open(io.BytesIO(raw)) as w:
            rate, ch = w.getframerate(), w.getnchannels()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    else:  # audio/L16;codec=pcm;rate=24000
        rate = int(mime.split("rate=")[1]) if "rate=" in mime else 24000
        ch, data = 1, np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    x = data.astype(np.float32) / 32768.0
    if rate != 16000:
        t_old = np.arange(len(x)) / rate
        x = np.interp(np.arange(0, t_old[-1], 1 / 16000), t_old, x).astype(np.float32)
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def main():
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key or len(sys.argv) != 2:
        raise SystemExit(__doc__)
    pcm = to_wav16k(*synthesize(key))
    with wave.open(sys.argv[1], "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    print(f"wrote {sys.argv[1]}: {len(pcm) / 16000:.1f} s, peak {abs(pcm).max() / 32768:.2f}")


if __name__ == "__main__":
    main()
