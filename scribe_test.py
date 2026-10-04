"""
Try ElevenLabs Scribe (batch, scribe_v2) on a recording: transcript, speaker labels, sound tags.

    python scribe_test.py --record 20            # record 20 s from the reSpeaker, then send it
    python scribe_test.py mic_test.wav           # send an existing file
    python scribe_test.py --record 20 --display  # also play the speaker turns back on the OLED

Needs ELEVENLABS_API_KEY in .env (next to this file). Stop server.py first: only one
program can use the mic. Prints one line per speaker turn, e.g.
    [speaker_0  1.2- 3.4s] hello jax, are you coming?
"""

import argparse
import os
import time

import scribe

HERE = os.path.dirname(os.path.abspath(__file__))


def record(seconds):
    """Record from the same mic channel server.py uses; return WAV bytes."""
    import numpy as np
    import sounddevice as sd

    device = os.environ.get("AUDIO_DEVICE", "XVF3800")
    channels = int(os.environ.get("AUDIO_INPUT_CHANNELS", "6"))
    channel = int(os.environ.get("AUDIO_CHANNEL", "2"))
    rate = 16000

    print(f"Recording {seconds} s from {device} (channel {channel} of {channels}). Talk now...")
    audio = sd.rec(int(seconds * rate), samplerate=rate, channels=channels, dtype="int16", device=device)
    sd.wait()
    return scribe.wav_bytes(np.ascontiguousarray(audio[:, channel]), rate)


def print_turns(turns):
    for speaker, start, end, words in turns:
        print(f"[{speaker:<10} {start:5.1f}-{end:5.1f}s] {' '.join(words)}")
    speakers = sorted({t[0] for t in turns})
    print(f"\n{len(speakers)} speaker(s): {', '.join(speakers)}")


def main():
    scribe.load_env(os.path.join(HERE, ".env"))
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set ELEVENLABS_API_KEY in .env first.")

    p = argparse.ArgumentParser()
    p.add_argument("wav", nargs="?", help="existing WAV file to send")
    p.add_argument("--record", type=float, metavar="SECONDS", help="record from the mic instead")
    p.add_argument("--display", action="store_true", help="play the speaker turns back on the OLED")
    args = p.parse_args()

    if args.record:
        wav = record(args.record)
    elif args.wav:
        wav = open(args.wav, "rb").read()
    else:
        raise SystemExit("Give a WAV file or --record SECONDS.")

    try:
        result, elapsed = scribe.transcribe(wav, key, timeout=60)
    except RuntimeError as exc:
        raise SystemExit(str(exc))
    print(f"\nScribe took {elapsed:.1f}s. Full text:\n  {result.get('text', '')}\n")
    turns = scribe.speaker_turns(result)
    print_turns(turns)

    if args.display:
        import display_cue

        labels = scribe.unknown_labels(turns)
        print("\nPlaying on the display...")
        for speaker, start, end, words in turns:
            text = " ".join(words)
            print(f"  {labels[speaker]:<4} {text}")
            display_cue.show_caption(labels[speaker], text)
        time.sleep(display_cue.HOLD_SEC)


if __name__ == "__main__":
    main()
