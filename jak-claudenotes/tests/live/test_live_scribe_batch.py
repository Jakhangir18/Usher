"""Live check (costs ~11 s of audio): batch Scribe contract + the catch-up helpers on a real result.

Needs ELEVENLABS_API_KEY and USHER_TEST_WAV (16 kHz mono WAV of the two-voice test conversation:
"Hey, are you coming to the cafe..." / "Hello Jax, we saved you a seat over here. Hi, I'm Sam.").
"""

import os

import pytest

import scribe
import server

KEY = os.environ.get("ELEVENLABS_API_KEY", "")
WAV = os.environ.get("USHER_TEST_WAV", "")
pytestmark = pytest.mark.skipif(not (KEY and os.path.isfile(WAV)), reason="ELEVENLABS_API_KEY or USHER_TEST_WAV not set")


def test_batch_scribe_contract_and_catch_up_helpers():
    result, secs = scribe.transcribe(open(WAV, "rb").read(), KEY, timeout=60)
    print(f"scribe batch {secs:.1f}s: {result.get('text')!r}")
    words = result.get("words") or []
    assert words and {"text", "type", "start", "end", "speaker_id"} <= set(words[0])
    assert {w["type"] for w in words} <= {"word", "spacing", "audio_event"}
    turns = scribe.speaker_turns(result)
    assert len(scribe.unknown_labels(turns)) == 2, turns
    assert server.wake_phrase_detected(result["text"], extra_names=False)
    assert scribe.find_self_name(result["text"], exclude=["Jax", "jacks", "jaxx", "jack"]) == "Sam"
    caller = server._find_caller(turns, {}, 0.0)
    assert any("jax" in w.lower() for sp, _, _, ws in turns if sp == caller for w in ws)
    assert secs < 10.0
