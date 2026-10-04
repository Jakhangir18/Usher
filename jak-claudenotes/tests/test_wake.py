"""The "Hello Jax" rule (server.wake_phrase_detected) and the shared wake cooldown."""

import time

import pytest

import server


@pytest.mark.parametrize("text", [
    "hello jax", "Hey Jacks!", "hi there jax", "Hello Jack's", "hiya jack",
    "so, hello jax, how are you", "HELLO JAXX",
])
def test_greeting_then_name_wakes(text):
    assert server.wake_phrase_detected(text)


@pytest.mark.parametrize("text", [
    "jax is here", "jax hello", "I told jax about it", "hello josh", "come back",
    "hello there my friend jax", "", None, "hello", "jack",
])
def test_name_alone_or_too_far_from_greeting_does_not_wake(text):
    assert not server.wake_phrase_detected(text)


def test_list_of_alternatives_is_any_match():
    assert server.wake_phrase_detected(["nothing here", "hey jax"])
    assert not server.wake_phrase_detected(["nothing here", "jax"])


@pytest.mark.xfail(strict=True, reason="Finding F1: Metaphone of 'jack' (JK) also matches jake/jock/joke/jackie on the Whisper path")
@pytest.mark.parametrize("text", ["hey jake", "hi jock", "hello jackie", "hey joke"])
def test_other_jk_names_do_not_wake_on_the_whisper_path(text):
    assert not server.wake_phrase_detected(text)


@pytest.mark.parametrize("text", ["hey jake", "hello jack", "hi jackie", "hey joke"])
def test_caption_path_without_extra_names_ignores_jack_lookalikes(text):
    assert not server.wake_phrase_detected(text, extra_names=False)


def test_caption_path_still_wakes_on_the_real_name():
    assert server.wake_phrase_detected("Hello Jax, we saved you a seat.", extra_names=False)
    assert server.wake_phrase_detected("hi jacks", extra_names=False)


@pytest.fixture
def quiet_outputs(monkeypatch):
    """Record motor/display calls instead of touching hardware; no catch-up task."""
    calls = []
    monkeypatch.setattr(server.haptics, "guide", lambda angle, track=None: calls.append(("guide", angle)))
    monkeypatch.setattr(server.display_cue, "show_async", lambda angle, title="HELLO JAX": calls.append(("show", angle)))
    monkeypatch.setattr(server, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(server, "_last_wake", 0.0)
    return calls


def test_wake_cooldown_is_shared_between_whisper_and_elevenlabs(quiet_outputs):
    now = time.monotonic()
    assert server._wake(90.0, "ElevenLabs") is True
    # Whisper's window was captured before ElevenLabs fired, or just after: both suppressed.
    assert server._wake(90.0, "Whisper", when=now - 1.0) is False
    assert server._wake(95.0, "Whisper", when=now + 1.0) is False
    assert server._wake(100.0, "Whisper", when=now + server.WAKE_COOLDOWN_SEC + 0.1) is True
    assert [c[0] for c in quiet_outputs] == ["guide", "show", "guide", "show"]
    assert quiet_outputs[0][1] == 90.0 and quiet_outputs[2][1] == 100.0


def _fill_history(angles, age=0.0):
    now = time.monotonic()
    with server._doa_lock:
        server._doa_history.clear()
        for i, a in enumerate(angles):
            server._doa_history.append((now - age - 0.05 * (len(angles) - i), a))


def test_caption_wake_uses_current_speech_direction(quiet_outputs):
    _fill_history([268.0, 270.0, 272.0])
    assert server._caption_wake("hello jax we saved you a seat", "??") is True
    assert quiet_outputs[0][0] == "guide" and abs(quiet_outputs[0][1] - 270.0) < 1.0


def test_caption_wake_ignores_whisper_only_spellings(quiet_outputs):
    assert server._caption_wake("hello jack", "?") is False
    assert quiet_outputs == []


def test_caption_wake_final_piece_never_double_buzzes(quiet_outputs):
    _fill_history([90.0, 90.0])
    assert server._caption_wake("hello jax", "?") is True          # live guess fires
    assert server._caption_wake("hello jax", "?", final=True) is True  # finished piece: no second buzz
    assert len([c for c in quiet_outputs if c[0] == "guide"]) == 1
