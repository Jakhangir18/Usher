# How to do it better (after the hackathon)

Not for tonight. These are the things a second pass should change, roughly in order of value.
None of them blocks the demo; the demo-relevant items are in `01-findings.md` and
`02-demo-runbook.md`.

## 1. Keep the offline harness and run it in CI

`jak-claudenotes/tests/` runs on any machine in about 3 s and covers the wake rule, the angle
maths, the DOA history lookups, the catch-up helpers, the people/name rules, the haptics state
machine, the display layout and the WebSocket contract. Move it to `tests/` at the repo root, add
`pytest` to a `requirements-dev.txt`, and run it on every push with GitHub Actions. The live API
tests stay opt-in (`LIVE=1`, keys in repository secrets).

## 2. Split `server.py` (2,200 lines) by responsibility

It currently holds: `.env` loading, DOA thread, Flask legacy HUD, Whisper, Silero (unused), name
matching, the wake rule, two Gemini integrations, catch-up, live-caption glue, the refine loop,
the sound-event loop, the WebSocket server and `main()`. A split that keeps every tested function
intact:

- `config.py`: the `.env` loader and every `os.environ` read (today `live_captions.py` reads the
  environment a second time and re-implements `logical_angle`; one module ends that duplication).
- `direction.py`: the DOA thread, `_doa_history`, `_angles_during`, `_direction_during`,
  `_track_speaker`, `_circular_mean`.
- `wake.py`: Whisper, `wake_phrase_detected`, `_wake`, `_caption_wake`, `wake_loop`.
- `catchup.py`, `refine_loop.py`, `gemini_summary.py`, `ws_server.py`.

## 3. Remove the SPOOT leftovers

- The Flask app on port 5000 still serves `index.html` + `static/usher.*` (the old phone PWA) and
  `/state`. Judges must not open it. Delete the route, the files and the `192.168.68.55.pem` cert
  lookup, or move the page to `legacy/`.
- The per-window Gemini classifier (`GEMINI_PROMPT_TEMPLATE`, `call_gemini_blocking`,
  `_gemini_update`, `local_reason`, `URGENT_PHRASES`) only runs when live captions are off. It
  then calls Gemini for every loud window that contains the name or "hey", which is noise and
  spend. Delete it; `_gemini_loop` is the real integration.
- Silero VAD (`USE_SILERO_VAD`, `_get_silero`, `is_speech`) is off by default and documented as
  having stalled the Pi. Delete it with its torch instructions.
- `name_match_details` / `detect_name` (substring, Soundex, fuzzy) are used only by the legacy
  classifier path; the wake rule has its own stricter matcher.

## 4. One wake-name matcher with an explicit policy

`_is_wake_name` accepts any word whose Metaphone code equals a target's. That is right for
`USER_NAME` and its aliases (Jax, jacks, jaxx share `JKS`), and wrong for `WAKE_EXTRA_NAMES`
("jack" shares `JK` with jake, jock, joke, jackie, jacky). Match extra names exactly, phonetic
matching only for the real name. Finding F1 has the patch and the failing test.

## 5. Requirements split

`requirements.txt` is a `pip freeze` of the Pi venv (RPi.GPIO, rpi_ws281x, Neopixel, pyftdi,
binho, sysv_ipc, hf-xet...). Keep `requirements-pi.txt` as the frozen set that is known to work on
the device, and add `requirements.txt` with the dozen packages the code imports, so a laptop can
install it. `camera_server.py` runs on the system Python and needs `python3-opencv python3-flask`
from apt; say so in one place.

## 6. Type hygiene

`pyright` (basic) reports 44 errors, almost all of the same shape: module globals initialised to
`None` (`_refiner`, `_refine_wake`, `_live_people`, `_fonts`) and used without a guard. They are
safe at runtime because `main()` sets them before the loops start, but a small `State` object
created in `main()` and passed to the loops would make that explicit and let a type checker help.
The one fixable lint today is `server.py:368` (an f-string without placeholders).

## 7. Documentation drift to fix

- `CLAUDE.md` says `index.html`/`manifest.json` are not in the repo: `index.html` is (from the
  `usher-audio` merge), `manifest.json` is not, so `/manifest.json` returns 404.
- `CLAUDE.md` "Known issues" still lists the Touchpoint 5 V button circuit and 1-wire overlay;
  fine as history, but mark what applies to this build.
- `.env.example` and `CLAUDE.md` both document every setting; keep one (the example file) and
  link to it.

## 8. Product and robustness ideas the code is already shaped for

- **Head tracking (IMU)**: `haptics.guide(track=...)` already takes a callable; an IMU heading
  source drops in without changing the buzz logic.
- **Dedicated wake word**: the rolling Whisper window costs 0.8-1.6 s of CPU per second on the Pi.
  A keyword engine (Porcupine, or Vosk with a two-word grammar) would cut latency and heat;
  keep Whisper for the offline fallback until it is measured.
- **Speaker embeddings on-device** (ECAPA or similar) would make the headset's `?`/`??` labels
  stable when the wearer turns, which is the biggest user-visible gap today.
- **Soft bound on the caption queue**: `CaptionAudio.feed` reads the queue size that only grows
  when the event loop runs its callbacks; with a 64 ms mic callback this is fine, but a blocked
  loop can still schedule unbounded puts. A `collections.deque(maxlen=...)` filled from the
  callback thread and drained by `send_audio` is the simpler shape.
- **Logs**: the terminal is the only observability. A rotating log file per run (already
  gitignored as `*.log`) would make post-demo debugging possible.
