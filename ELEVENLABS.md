# ElevenLabs in the Usher headset

What's built, what's been tested, how to run it, and what's left. For the overall project and Pi setup see
[CLAUDE.md](CLAUDE.md); this file covers the ElevenLabs parts only.

## The idea

People with Usher syndrome often miss the start of a conversation. The headset already turns the wearer toward
whoever says "Hello Jax" (arrow on the display + temple motors, all local on the Pi). ElevenLabs adds **what was said**:

- **Live captions:** words on the display as people talk, with an arrow toward whoever is speaking now.
- **Catch me up:** after "Hello Jax", show what the caller said in the last 30 seconds (voice-identified).

The arrow/motor path never depends on ElevenLabs. No internet or no key = no captions, but "Hello Jax" still works.

## How it all runs together (`python server.py`)

One program, one mic stream (6 channels):
- **"Hello Jax"** (local Whisper on ch2) → full turn pattern on the motors + arrow on the display, then **catch-up**.
- **Live captions** (Scribe Realtime on ch0) run the whole time in the background (`LIVE_CAPTIONS=1`).
- **Motors only fire for "Hello Jax"**, never for captions (decision: fewer chances for wrong cues).
- Display priority: the arrow and catch-up text own the screen; live caption updates pause until they're done.
- Live captions reconnect by themselves if the internet drops (backoff 2 → 30 s).
- The catch-up shows the live captions' `?`/`??` label for the caller's direction when it matches a known person
  (within 50°); otherwise no label, just the arrow (so the two never contradict each other).

`live_captions.py` and `scribe_test.py` also run on their own for testing (stop `server.py` first: only one program
can use the mic and the display).

## Status (2026-10-03)

| Piece | File | Status |
|---|---|---|
| Shared Scribe helpers (batch API, WAV packing, speaker turns, `?`/`??` labels, filler cleanup) | `scribe.py` | Used by everything below |
| Batch Scribe test (record or WAV → speaker turns, optional playback on the OLED) | `scribe_test.py` | **Tested on the Pi** |
| Live captions engine | `live_captions.py` (`run()`) | **Tested standalone** (many rounds); merged into `server.py`, **merged version not yet tested** |
| Catch me up on each "Hello Jax" | `server.py` (`_catch_up`) | Written, **not yet tested** |
| Display caption modes | `display_cue.py` (`show_caption`, `show_live`) | Both work on the OLED |

### Test results so far
- **Batch Scribe (`scribe_v2`):** 20 s clip → **1.3–1.5 s** round trip. Two speakers separated correctly turn by turn
  (even one-word interruptions). "Jax" spelled correctly with no hints (local Whisper hears "jacks").
- **Captions on the OLED:** legible at 12 px, 3 lines × ~20 characters, 2.5 s per page.
- **Realtime Scribe:** words appear quickly and accurately; it shows a rough guess while you talk, then corrects it at
  the pause.
- **Who is talking (direction-based):** "mostly right" when people are clearly apart; labels on *past* text were
  too often wrong, hence option B (below).
- **Mic channel A/B:** ch2 (raw mic) put the quieter second speaker under the loudness cut-off; ch0 (processed beam)
  kept nearby voices at ≥ 0.034 with background below 0.02 → captions use ch0.

## Setup

1. API key: elevenlabs.io → Developers → API Keys. Permissions: Speech to Text ✅, Text to Speech ✅ (for later
   replies), Voices read ✅, Models read ✅; everything else off. Set a credit limit on the key (but check the balance
   before a demo: Realtime streams continuously while captions are on).
2. Put it in `.env` next to `server.py` (gitignored, never commit it): `ELEVENLABS_API_KEY=...`
   On the Pi, edit `.env` with `nano`; don't copy `.env.example` over it (that wipes the key).
3. `pip install -r requirements.txt` (needs `requests`, `websockets`, `sounddevice`, `numpy`).

## How to run

```bash
python server.py                             # everything: "Hello Jax" + motors + arrow + catch-up + live captions
python live_captions.py                      # live captions only (testing), Ctrl+C to stop
python scribe_test.py --record 20            # record 20 s, print speaker turns (batch Scribe)
python scribe_test.py --record 20 --display  # ...and play them back on the OLED (playback, not live)
```

## How each piece works

### Batch Scribe (`scribe.py`)
`POST https://api.elevenlabs.io/v1/speech-to-text`, header `xi-api-key`, multipart form: `model_id=scribe_v2`,
`language_code=en`, `diarize=true`, `tag_audio_events=true`, `timestamps_granularity=word`, `file=<wav>`.
Response `words[]` has `text`, `type` (`word` / `spacing` / `audio_event`), `start`, `end`, `speaker_id`.
`speaker_turns()` groups consecutive words by speaker (sound tags join the current speaker); `unknown_labels()` maps
`speaker_0 → ?`, `speaker_1 → ??`, ... Speaker IDs only hold within one request.
`clean_words()` / `clean_text()` drop filler sounds (uh, um, er, ah, hmm; not "uh-huh"/"mm-hmm") and collapse
stutters ("I, I, I wonder" → "I wonder").

### Catch me up (`server.py`)
On a wake (after the arrow and motors have already fired):
1. Take the last `CATCHUP_SEC` (30 s) from the audio ring buffer (ch2) and send it to batch Scribe in a background task.
2. Give each speaker a direction: circular mean of the mic's speech direction readings taken during their words
   (Scribe word times + our timestamped DOA history).
3. The caller = the last speaker who said the name (`jax`/`jacks`/`jack`); otherwise the speaker nearest the arrow.
4. Once the arrow has been up 2.5 s, show the caller's last `CATCHUP_WORDS` (45) words (cleaned) with label + arrow.
5. The terminal prints every speaker turn with label and direction.

Settings in `.env`: `CATCHUP_SEC=30` (0 = off), `CATCHUP_WORDS=45`.

### Live captions (`live_captions.py`, `run()`)
`wss://api.elevenlabs.io/v1/speech-to-text/realtime?model_id=scribe_v2_realtime&audio_format=pcm_16000&language_code=en&commit_strategy=manual&keyterms=Jax&include_timestamps=true`,
header `xi-api-key`. We send `{"message_type": "input_audio_chunk", "audio_base_64": ..., "commit": false, "sample_rate": 16000}`
for every audio chunk (64 ms inside server.py, 100 ms standalone). Scribe sends `partial_transcript` (live guess),
`committed_transcript` and `committed_transcript_with_timestamps` (final, with word times).

**Realtime Scribe has no speaker labels**, so the mic array tells people apart by direction:
- each direction is a person (within `SAME_PERSON_DEG` = 50°), labelled `?`, `??`, `???` in order of appearance; a new
  person is only added once a new direction has held 0.8 s (single jumpy readings can't invent people);
- we send `commit: true` when the talker's direction changes (new direction held 0.4 s), when speech stops (0.7 s),
  or when a piece has run 6 s (loud rooms can keep the speech flag on); only one commit per new text, and not within
  0.4 s of the last one (Scribe rejects < 0.3 s with `commit_throttled`);
- a finished piece is labelled with the speaker at commit time; word-level directions only override that for 3+
  consecutive words (`MIN_OVERRIDE_WORDS`), because word times vs direction times are a fraction of a second apart;
- **option B (chosen):** the scrolling text has **no speaker tags**; the yellow band shows who is talking *now*
  (arrow + label). A wrong label on past text misleads more than no label. `LIVE_SPEAKER_TAGS=1` brings back
  inverted tags at each speaker change;
- background chatter: runs of 2+ words quieter than `CAPTION_MIN_RMS` (0.02) are dropped (single quiet words like
  "to" are kept); the live guess is only shown while the last 0.6 s was loud enough. When anything is dropped, the
  terminal prints a `heard:` line with the whole piece and the dropped parts in red (`[✗ can see it? 0.019]`);
- fillers/stutters are removed (`scribe.clean_text`);
- robustness: the audio queue is bounded (old audio dropped while offline); if the uplink falls > ~2–3 s behind, the
  session resyncs; if a helper task fails, the socket closes and `server.py` reconnects.

### Environmental noise: what helps, in order
1. Loudness gate (above). Cheap, tunable via `CAPTION_MIN_RMS`.
2. Mic channel 0 (the chip's processed beam: noise suppression + steering toward the talker) for captions:
   `CAPTION_AUDIO_CHANNEL=0` (the default). Don't change `AUDIO_CHANNEL`: that's the tested ch2 "Hello Jax" path.
3. Physical: mic close to the wearer's head, speakers within ~1–2 m and clearly apart (e.g. left and right).
4. Not possible with Realtime: ElevenLabs Voice Isolator works on files (batch), so it could only help catch-up.

## Display constraints
128×64 SSD1306, yellow band (top 16 px) and blue below. Colours are fixed in the glass: no mixing, no per-person
colours. Tools we have: inverted text (filled box), boxes, position. Only one program may drive the display.
One-screen test (screen clears after 4 s, so watch it):
`python -c "import display_cue, time; display_cue.show_live('??', [('?', 'Hello, my name is Oliver.'), ('??', 'I am testing from the right.')], 'left'); print('drawn'); time.sleep(5)"`

## To do / ideas
- [ ] Test the merged `server.py` (captions + "Hello Jax" + catch-up together); check the arrow isn't overwritten.
- [ ] Test catch-up on the Pi (two people chat, one says "Hello Jax").
- [ ] Names instead of `?`/`??`: send recent transcript to Gemini ("Hi, I'm Sam" → `??` = SAM).
- [ ] Sound awareness: Scribe's audio event tags (laughter, applause) → a distinct haptic pattern. Not a safety
      feature; don't pitch it as alarm detection. (Note: motors are currently "Hello Jax" only, by decision.)
- [ ] Replies: wearer types/taps, ElevenLabs Text to Speech speaks it.
- [ ] Realtime could also back up local Whisper for the wake phrase (keyterm "Jax").

## Gotchas
- Never commit `.env`. Check `git status` shows it as ignored.
- Scribe speaker IDs reset per request; directions are what keep people consistent across requests.
- Privacy: this streams bystanders' speech to the cloud. Have an answer ready for judges (consent, nothing stored).
- Credits: Realtime streams the whole time captions are on. Check the balance before a demo.
