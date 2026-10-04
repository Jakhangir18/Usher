"""
The real offline wake path on a real utterance: transcribe_audio() -> wake_phrase_detected().

Heavy: needs faster-whisper installed and downloads tiny.en (about 75 MB) on first run; skipped
otherwise. Needs USHER_TEST_WAV (see 04-dev-env.md). On the VPS run it under the heavy wrapper.
"""

import os
import wave

import numpy as np
import pytest

pytest.importorskip("faster_whisper")
import server  # noqa: E402

WAV = os.environ.get("USHER_TEST_WAV", "")
pytestmark = pytest.mark.skipif(not os.path.isfile(WAV), reason="USHER_TEST_WAV not set")


def _audio():
    with wave.open(WAV) as w:
        assert w.getframerate() == 16000
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype("float32") / 32768.0


def test_whisper_window_with_hello_jax_wakes_and_one_without_does_not():
    audio = _audio()
    # Batch Scribe timed "Hello, Jax." at about 5.5-6.3 s: the 4 s window the wake loop would see.
    window = audio[int(4.5 * 16000):int(8.5 * 16000)]
    text, alts = server.transcribe_audio(window)
    print("whisper:", repr(text))
    assert text
    assert server.wake_phrase_detected(alts), text
    text2, alts2 = server.transcribe_audio(audio[: int(4.0 * 16000)])
    print("whisper (no phrase):", repr(text2))
    assert not server.wake_phrase_detected(alts2), text2
