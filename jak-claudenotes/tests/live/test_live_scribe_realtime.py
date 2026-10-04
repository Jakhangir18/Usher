"""Live check (streams ~15 s of audio): one Scribe Realtime session through live_captions.run()
with a fake mic clock and a fake direction reader, exactly as server.py drives it.

Needs ELEVENLABS_API_KEY and USHER_TEST_WAV. Checks: session starts, finished pieces come back with
text, the caption-wake callback fires on a live guess of "Hello Jax", publish() sees partial then
final updates with the same segment id.
"""

import asyncio
import os
import time
import wave

import numpy as np
import pytest

import display_cue
import live_captions
import server

KEY = os.environ.get("ELEVENLABS_API_KEY", "")
WAV = os.environ.get("USHER_TEST_WAV", "")
pytestmark = pytest.mark.skipif(not (KEY and os.path.isfile(WAV)), reason="ELEVENLABS_API_KEY or USHER_TEST_WAV not set")


class FakeDirection:
    """One person at 300 deg (left); 'speech' whenever the fed audio is loud."""

    def __init__(self):
        self.last = 0.0
        self.angle = 300.0

    def mark(self, speech):
        if speech:
            self.last = time.monotonic()

    def current(self):
        return self.angle if time.monotonic() - self.last < live_captions.DIRECTION_WINDOW else None

    def silent_for(self):
        return time.monotonic() - self.last

    def angles_between(self, start, end):
        return [self.angle]


def test_realtime_session_end_to_end(monkeypatch):
    monkeypatch.setattr(display_cue, "show_live", lambda *a, **k: None)
    with wave.open(WAV) as w:
        assert (w.getframerate(), w.getnchannels()) == (16000, 1)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    published, wakes, log = [], [], []

    def on_text(text, label, final=False):
        hit = server.wake_phrase_detected(text, extra_names=False)
        if hit:
            wakes.append((text, label, final))
        return hit

    def publish(seg, label, text, is_final):
        published.append((seg, label, text, is_final))

    async def session():
        loop = asyncio.get_running_loop()
        audio = live_captions.CaptionAudio(loop)
        direction = FakeDirection()
        people = live_captions.People()
        ready = asyncio.Event()

        async def feeder():
            await asyncio.wait_for(ready.wait(), timeout=20)
            chunk = live_captions.CHUNK
            for i in range(0, len(pcm), chunk):
                piece = pcm[i:i + chunk]
                audio.feed(piece)
                rms = float(np.sqrt(np.mean((piece.astype(np.float32) / 32768.0) ** 2)))
                direction.mark(rms > 0.01)
                await asyncio.sleep(chunk / live_captions.RATE)
            for _ in range(40):  # 4 s of silence so the last piece commits
                audio.feed(np.zeros(chunk, dtype=np.int16))
                await asyncio.sleep(0.1)

        run = asyncio.create_task(live_captions.run(KEY, audio, direction, people, on_ready=ready.set,
                                                    on_text=on_text, publish=publish))
        feed = asyncio.create_task(feeder())
        done, _ = await asyncio.wait({run, feed}, timeout=60, return_when=asyncio.FIRST_COMPLETED)
        if run in done and not run.cancelled() and run.exception():
            raise run.exception()
        for task in (run, feed):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return people

    people = asyncio.run(session())
    finals = [p for p in published if p[3] and p[2]]
    print("finals:", finals)
    print("wakes:", wakes)
    assert finals, published
    text = " ".join(p[2] for p in finals).lower()
    assert "jax" in text and "seat" in text, text
    assert wakes, "the fast caption wake never saw 'Hello Jax'"
    assert wakes[0][2] is False, "wake came only from the finished-piece backup, not a live guess"
    partial_ids = {p[0] for p in published if not p[3]}
    assert partial_ids & {p[0] for p in finals}, "partial and final updates should share a segment id"
    assert list(people.angles) == ["?"]
    assert people.display("?") == "SAM"
