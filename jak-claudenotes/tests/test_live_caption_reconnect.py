"""
live_captions.run() across a reconnect, against a fake Scribe socket: the frame numbers it hands
to display_cue.show_live must keep rising, or the OLED drops every frame of the new session
(finding R2). No network: websockets.connect is replaced.
"""

import asyncio
import json

import numpy as np
import pytest

import display_cue
import live_captions


class FakeScribe:
    """One session: session_started, one live guess, then the server closes the socket."""

    def __init__(self):
        self._messages = [{"message_type": "session_started"},
                          {"message_type": "partial_transcript", "text": "hello there"}]

    async def send(self, msg):
        pass

    async def close(self):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            await asyncio.sleep(0.3)      # let the redraw thread run before the session ends
            raise StopAsyncIteration
        await asyncio.sleep(0.05)
        return json.dumps(self._messages.pop(0))


class OnePerson:
    def current(self):
        return 300.0

    def silent_for(self):
        return 0.0

    def angles_between(self, start, end):
        return [300.0]


def _two_sessions(monkeypatch):
    seqs = []
    monkeypatch.setattr(display_cue, "show_live", lambda who, segments, where=None, seq=None: seqs.append(seq))

    async def fake_connect(url, **kwargs):
        return FakeScribe()

    monkeypatch.setattr(live_captions.websockets, "connect", fake_connect)
    people = live_captions.People()

    async def session():
        audio = live_captions.CaptionAudio(asyncio.get_running_loop())
        audio.feed(np.full(3200, 4000, dtype=np.int16))   # 0.2 s, loud enough for the live guess to show
        await live_captions.run("key", audio, OnePerson(), people)

    runs = []
    for _ in range(2):
        before = len(seqs)
        asyncio.run(session())
        runs.append(seqs[before:])
    return runs


@pytest.mark.xfail(strict=True, reason="Finding R2: run() restarts its frame counter at 0 on every session, display_cue._live_seq never resets")
def test_frame_counter_keeps_rising_across_reconnects(monkeypatch):
    first, second = _two_sessions(monkeypatch)
    assert first and second, (first, second)
    assert second[0] > first[-1], (first, second)


def test_each_session_draws_its_live_guess(monkeypatch):
    first, second = _two_sessions(monkeypatch)
    assert first and second and all(isinstance(s, int) for s in first + second)
