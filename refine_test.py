"""
Offline check of refine.py's voice matching (no mic, no network; needs the Pi venv's numpy and
requests): python refine_test.py
Feeds made-up Scribe results through Refiner and checks speakers keep the same V1/V2 across
requests even when the diarizer swaps its speaker numbers, plus the edge cases from review.
"""

import numpy as np

import refine

RATE = 16000


def word(text, start, end, speaker, kind="word"):
    return {"text": text, "start": start, "end": end, "speaker_id": speaker, "type": kind}


def say(text, start, end, speaker):
    """Spread a sentence's words evenly over [start, end]."""
    words = text.split()
    step = (end - start) / len(words)
    return [word(w, start + i * step, start + (i + 1) * step, speaker) for i, w in enumerate(words)]


def main():
    r = refine.Refiner(RATE)
    window = np.zeros(8 * RATE, dtype=np.float32)

    # Pass 1 (window clock 100-108 s, no references yet): A talks, then B, then quiet.
    clip, spans, offset = r.build_clip(window)
    assert spans == [] and offset == 0.0 and len(clip) == len(window)
    result = {"words": say("hey are you coming to the cafe", 0.5, 2.5, "speaker_0")
                       + say("yeah give me a minute", 3.0, 5.0, "speaker_1")}
    turns, pending = r.process(result, spans, offset, window, 100.0)
    assert [(t[0], t[3]) for t in turns] == [("V1", "hey are you coming to the cafe"),
                                            ("V2", "yeah give me a minute")], turns
    assert pending is None
    assert r.voices["V1"].ref is not None and r.voices["V2"].ref is not None  # both turns were 2 s

    # Pass 2 (window clock 103-111 s): the diarizer swaps numbers. speaker_1 is on V1's reference
    # and is the one talking now, so it must stay V1.
    clip, spans, offset = r.build_clip(window)
    assert [s[0] for s in spans] == ["V2", "V1"], spans  # most recently heard first
    v2_ref, v1_ref = spans[0], spans[1]
    result = {"words": say("ref words", v2_ref[1], v2_ref[2], "speaker_0")
                       + say("ref words", v1_ref[1], v1_ref[2], "speaker_1")
                       + say("give me a minute", offset + 0.0, offset + 2.0, "speaker_0")   # old B speech
                       + say("hello jax we saved you a seat", offset + 3.0, offset + 5.0, "speaker_1")}
    turns, pending = r.process(result, spans, offset, window, 103.0)
    assert [(t[0], t[3]) for t in turns] == [("V1", "hello jax we saved you a seat")], turns
    assert len(r.voices) == 2, "no new voice for a swapped speaker number"

    # Pass 3: someone new starts and is still talking at the end of the audio: held back.
    clip, spans, offset = r.build_clip(window)
    result = {"words": say("can I come too", offset + 6.0, offset + 7.9, "speaker_2")}
    turns, pending = r.process(result, spans, offset, window, 106.0)
    assert turns == [] and pending is not None and abs(pending - 112.0) < 1e-6, (turns, pending)

    # Pass 4: they finished; now the turn comes through as a new voice, V3.
    clip, spans, offset = r.build_clip(window)
    result = {"words": say("can I come too I have been stuck all day", offset + 4.0, offset + 6.5, "speaker_0")}
    turns, pending = r.process(result, spans, offset, window, 108.0)
    assert [(t[0], t[3]) for t in turns] == [("V3", "can I come too I have been stuck all day")], turns

    # A one-word blip from an unmatched "speaker" joins the turn around it instead of becoming V4.
    # (V3 is recognised from its reference clip this time: no overlap with earlier words.)
    clip, spans, offset = r.build_clip(window)
    v3_ref = next(s for s in spans if s[0] == "V3")
    result = {"words": say("ref words", v3_ref[1], v3_ref[2], "speaker_0")
                       + say("of course", offset + 3.0, offset + 3.6, "speaker_0")
                       + [word("the", offset + 3.6, offset + 3.75, "speaker_9")]
                       + say("more the merrier", offset + 3.75, offset + 4.8, "speaker_0")}
    turns, pending = r.process(result, spans, offset, window, 112.0)
    assert len(r.voices) == 3, r.voices.keys()
    assert len(turns) == 1 and turns[0][3] == "of course the more the merrier", turns

    # Sound tags without a speaker stay with whoever was talking.
    # Sound tags without a speaker stay with whoever was talking (V3 again, from its reference).
    clip, spans, offset = r.build_clip(window)
    v3_ref = next(s for s in spans if s[0] == "V3")
    result = {"words": say("ref words", v3_ref[1], v3_ref[2], "speaker_0")
                       + say("that was funny", offset + 5.0, offset + 6.0, "speaker_0")
                       + [{"text": "(laughter)", "start": offset + 6.0, "end": offset + 6.5, "type": "audio_event"}]}
    turns, _ = r.process(result, spans, offset, window, 112.0)
    assert turns and turns[-1][0] == "V3" and turns[-1][3].endswith("[laughter]"), turns
    assert len(r.voices) == 3

    # A long monologue still at the end of the audio sends its finished words instead of waiting
    # (otherwise its start could slide out of the window unsent).
    r2 = refine.Refiner(RATE)
    long_window = np.zeros(20 * RATE, dtype=np.float32)
    clip, spans, offset = r2.build_clip(long_window)
    result = {"words": say(" ".join(["word"] * 40), 0.5, 19.8, "speaker_0")}
    turns, pending = r2.process(result, spans, offset, long_window, 200.0)
    assert len(turns) == 1 and pending is not None, (turns, pending)
    assert turns[0][2] <= 220.0 - refine.HOLD_SEC + 1e-9 and pending > turns[0][2] - 1e-9, (turns, pending)

    # A pause of a second or more starts a new line, even for the same voice.
    clip, spans, offset = r2.build_clip(window)
    result = {"words": say("first thought", offset + 1.0, offset + 2.0, "speaker_0")
                       + say("second thought", offset + 3.5, offset + 4.5, "speaker_0")}
    turns, _ = r2.process(result, spans, offset, window, 230.0)
    assert [t[3] for t in turns] == ["first thought", "second thought"], turns

    # Quiet turns (distant chatter on the raw mic) are dropped; loud ones kept.
    r3 = refine.Refiner(RATE)
    quiet_loud = np.zeros(8 * RATE, dtype=np.float32)
    quiet_loud[1 * RATE:3 * RATE] = 0.1
    clip, spans, offset = r3.build_clip(quiet_loud)
    result = {"words": say("loud and close", 1.0, 3.0, "speaker_0") + say("far away chatter", 4.0, 6.0, "speaker_1")}
    turns, _ = r3.process(result, spans, offset, quiet_loud, 300.0, min_rms=0.01)
    assert [t[3] for t in turns] == ["loud and close"] and r3.dropped == 1, (turns, r3.dropped)
    assert len(r3.voices) == 1, "distant chatter must not become a voice"

    # Null words / no words / missing timestamps don't crash and keep the previous words for voting.
    prev = list(r3.prev)
    assert r3.process({"words": None}, [], 0.0, quiet_loud, 400.0) == ([], None)
    assert r3.process({}, [], 0.0, quiet_loud, 400.0) == ([], None)
    assert r3.process({"words": [{"text": "hi", "type": "word", "speaker_id": "speaker_0"}]},
                      [], 0.0, quiet_loud, 400.0) == ([], None)
    assert r3.prev == prev

    # Only sound tags from an unknown "speaker": no new voice, no line.
    voices = len(r3.voices)
    result = {"words": [{"text": "(music)", "type": "audio_event", "speaker_id": "speaker_7", "start": 1.0, "end": 3.0}]}
    assert r3.process(result, [], 0.0, quiet_loud, 500.0) == ([], None)
    assert len(r3.voices) == voices

    # Voice cap: with max_voices=2 already reached, an unmatched speaker joins the voice from the
    # nearest direction (here V1 at 90 deg, the new speech comes from 95 deg) instead of becoming V3.
    r4 = refine.Refiner(RATE, max_voices=2)

    def angle_of(spans):
        if not spans:
            return None
        return 90.0 if spans[0][0] < 602 else 270.0 if spans[0][0] < 605.5 else 95.0

    clip, spans, offset = r4.build_clip(window)
    result = {"words": say("hi I am over here", 0.5, 2.5, "speaker_0") + say("and I am over there", 3.0, 5.0, "speaker_1")}
    turns, _ = r4.process(result, spans, offset, window, 600.0, angle_of=angle_of)
    assert [t[0] for t in turns] == ["V1", "V2"] and abs(r4.voices["V1"].angle - 90.0) < 1e-6, (turns, r4.voices["V1"].angle)
    clip, spans, offset = r4.build_clip(window)
    result = {"words": say("it is me again", offset + 1.0, offset + 3.0, "speaker_2")}
    turns, _ = r4.process(result, spans, offset, window, 606.0, angle_of=angle_of)
    assert [t[0] for t in turns] == ["V1"] and len(r4.voices) == 2, (turns, list(r4.voices))

    print("refine_test: all checks passed")


if __name__ == "__main__":
    main()
