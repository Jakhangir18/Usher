"""scribe.py helpers: WAV packing, speaker turns, filler cleanup, self-introductions."""

import io
import wave

import numpy as np
import pytest

import scribe


def test_wav_bytes_is_16k_mono_16bit():
    audio = (0.25 * np.sin(np.linspace(0, 100, 1600))).astype("float32")
    with wave.open(io.BytesIO(scribe.wav_bytes(audio, 16000))) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 1600)
    with wave.open(io.BytesIO(scribe.wav_bytes(np.zeros(10, dtype=np.int16)))) as w:
        assert w.getnframes() == 10


def test_speaker_turns_groups_words_and_hands_sound_tags_to_the_current_speaker():
    result = {"words": [
        {"text": "Hello", "type": "word", "start": 0.0, "end": 0.4, "speaker_id": "speaker_0"},
        {"text": " ", "type": "spacing", "start": 0.4, "end": 0.5},
        {"text": "Jax", "type": "word", "start": 0.5, "end": 0.9, "speaker_id": "speaker_0"},
        {"text": "laughter", "type": "audio_event", "start": 1.0, "end": 1.5},
        {"text": "Yeah", "type": "word", "start": 2.0, "end": 2.3, "speaker_id": "speaker_1"},
    ]}
    turns = scribe.speaker_turns(result)
    assert turns == [["speaker_0", 0.0, 1.5, ["Hello", "Jax", "[laughter]"]],
                     ["speaker_1", 2.0, 2.3, ["Yeah"]]]
    assert scribe.unknown_labels(turns) == {"speaker_0": "?", "speaker_1": "??"}
    assert scribe.speaker_turns({"words": [{"text": "noise", "type": "audio_event", "start": 0, "end": 1}]}) == []


def test_clean_words_drops_fillers_and_stutters_but_keeps_uh_huh():
    assert scribe.clean_words(["uh", "I,", "I,", "I", "wonder", "um", "uh-huh"]) == ["I,", "wonder", "uh-huh"]
    assert scribe.clean_text("Hmm, so, so what is it?") == "so, what is it?"


@pytest.mark.parametrize("text,name", [
    ("Hi, I'm Sam.", "Sam"), ("my name is Oliver.", "Oliver"), ("Call me Sajjad", "Sajjad"),
    ("I am Priya and this is Tom", "Priya"), ("I’m Sam", "Sam"),
    ("I'm okay", None), ("I'm really tired", None), ("this is Sam", None), ("I'm Jack", None),
    ("I am Jax", None), ("I'm Jacks", None), ("", None),
])
def test_find_self_name(text, name):
    assert scribe.find_self_name(text, exclude=["Jax", "jacks", "jaxx", "jack"]) == name
