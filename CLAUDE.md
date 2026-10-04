# Usher Syndrome Haptic Headset ("Hello Jax")

## What we're building
A headset for people with Usher syndrome (hearing loss + progressive tunnel vision).
When someone says "Hello Jax", a 360° mic array finds the direction of the voice and
two vibration motors on the temples buzz to guide the wearer to turn toward the speaker.
Team of 4, hackathon build. Repo: https://github.com/Jakhangir18/Usher
`.env` is gitignored. Never commit API keys (ElevenLabs etc.).

## Hardware
- Raspberry Pi 5 (runs everything). gpiozero needs lgpio: `GPIOZERO_PIN_FACTORY=lgpio`, venv with `--system-site-packages`.
- reSpeaker XVF3800 USB 4-mic array. Direction: `DOA_VALUE` over USB via pyusb (`doa_reader.py`, needs the udev
  rule); the vendor `xvf_host` binary (needs passwordless sudo) is only the fallback. ALSA card 2, **6 channels**.
  Never record it as 1 channel (averages all 6 → echoey). Tested 2026-10-03: ch0 = processed beam, auto-gain,
  clipped in a quiet room (−21 dBFS avg); ch2 = single raw mic, clean, −27 dBFS. **Whisper "Hello Jax" + catch-up use
  ch2** (`AUDIO_CHANNEL=2`); **live captions use ch0** (`CAPTION_AUDIO_CHANNEL=0`: better nearby-vs-background gap).
- 2 motors on the temples (left = GPIO4 / pin 7 / "P1", right = GPIO5 / pin 29 / "P2", BCM numbering), wired like Quackhack/Touchpoint;
  micro vibration motors (red/blue leads),
  each switched by a MOSFET trigger module as in Touchpoint's schematic (motor between VCC and the module, Pi pin → module signal).
- **Motors are fragile; change power in small steps.** History: pulse patterns at 0.35 then 0.25 felt **too strong**;
  the user wants a **soft continuous buzz** instead (see `haptics.py` below: 0.10–0.20, one 0.35 nudge for 0.03 s to
  spin up). `motor_test.py` buzzes at a fixed 0.25 (no ramp), hard cap 0.4.
  Power motors from 3.3 V, not 5 V, if they're ~3 V rated (also keeps motor current off the USB/5V rail).
- No IMU. The buzz follows the speaker's live direction while they keep talking (mic is head-mounted), otherwise
  assumes a 90°/s head turn. If an IMU is added later, BNO055 is preferred (on-chip fusion, gives heading directly).

## Code base
Built on two of a teammate's prior hackathon repos:
- SPOOT: https://github.com/Jakhangir18/BeaverHacks-2026
  Its `server.py` gave us: continuous DOA polling, audio ring buffer, faster-whisper STT, fuzzy/phonetic name
  matching via `USER_NAME`, local fallback when no Gemini key (its Silero VAD is now off by default, see below).
- Touchpoint: https://github.com/Jakhangir18/Quackhack3.0
  `output/motors.py` = gpiozero PWM motor control with kick-start. Pattern reused in `haptics.py`.

## Our additions
- `haptics.py` (in this repo): **soft continuous buzz that leads the wearer toward the speaker** (replaced the pulse
  counts, which felt too strong). Tested on the Pi in server.py ("Hello Jax" buzzes the correct side).
  - `guide(rel_angle, track=None)` is non-blocking; a new call cancels the old one immediately. `stop()` turns both off.
  - Angle convention matches SPOOT: 0 = front, 90 = right, 270 = left.
  - Continuous buzz on the side to turn toward, softer as you get closer (`SOFT_MAX` 0.20 → `SOFT_MIN` 0.10), gentle
    fade-out when facing (±15°), switches temple on overshoot, gives up after 5 s. Already in front: one 0.25 s tap at
    0.15 on both. One brief 0.35 nudge (0.03 s) when a motor starts, so it spins up without a jolt.
  - Remaining turn: from `track(expected)` (server's `_track_speaker`: speech DOA in the last 0.4 s, within 60° of
    where the speaker should be) while the speaker keeps talking; otherwise assumes `TURN_RATE_DEG_S` 90°/s.
  - Dead behind (exactly 180°) counts as left.
  - If GPIO can't be opened (dev laptop, missing lgpio) it falls back to dummy motors with a warning, so `server.py` still runs.
  - Bench test: `python haptics.py`

## Changes made to SPOOT's `server.py` (copied into this repo; SPOOT's phone files, `oled_hud.py` and `oled_sanity_check.py` left out: `display_cue.py` replaces them)
1. **Rolling wake window replaced SPOOT's trigger + capture.** `wake_loop()`: every `WAKE_HOP_SEC` (1 s, or when
   Whisper finishes if slower) transcribe the last `WAKE_WINDOW_SEC` (4 s) of audio, if any 0.1 s of it reaches
   `VOLUME_THRESHOLD`. Why: the old loudness trigger + capture window cut "Hello Jax" off before "Jax" finished
   (the quiet "x" ended the capture), and one slow Whisper job (3 s+ on the Pi) blocked everything said meanwhile.
   Now each phrase lands whole in some window and nothing is lost while busy. Removed: `poll_mic`, `enrich_event`,
   `capture_phrase`, PREBUFFER/POSTBUFFER/ENRICH_COOLDOWN settings.
2. **Rule: the motors/display only trigger on the wake phrase "Hello Jax" or a variation, never on the name alone.**
   A variation = a greeting (`WAKE_GREETINGS`, default hello/helo/hallo/hullo/hi/hey/hiya) followed within
   `WAKE_MAX_GAP` (1) words by the name, an alias, or a `WAKE_EXTRA_NAMES` spelling (default "jack", Whisper's
   likeliest; safe only because a greeting is required). Exact word or same Metaphone code; apostrophes stripped
   ("Jack's" → "jacks"). Soundex/fuzzy are too loose for "Jax". `WAKE_COOLDOWN_SEC` (4.5) > window so one phrase
   isn't announced twice. Wake check runs before transcript dedupe. No "Hello/Hey Jax" in Whisper's prompt.
3. Direction (`doa_reader.py`, approach from sajjad's `usher-audio` branch): reads `DOA_VALUE` = angle + the chip's own
   speech flag over USB with pyusb (`DOA_SOURCE=auto`, falls back to the `xvf_host` binary's `AEC_AZIMUTH_VALUES`).
   Only readings taken during speech go into `_doa_history`; a wake uses their circular mean over the loud parts of the
   window. Polled every `DOA_POLL_SEC` (0.05 s; USB reads are cheap, no more `sudo` process per reading).
   USB source tested 2026-10-03: ~20 speech readings/s, calibration flip=1 / offset 184°, residual error 3–11°
   (the xvf_host source gave 181°, so both agree on "front").
4. Mic: `AUDIO_INPUT_CHANNELS`/`AUDIO_CHANNEL` (open 6, keep one; validated at startup), stream status printed.
5. `VOLUME_THRESHOLD` env + "listening… peak=" heartbeat every 3 s while quiet (shows level and that the stream is alive).
6. Speech filtering: `USE_SILERO_VAD=0` + `WHISPER_VAD_FILTER=1` (Whisper's built-in VAD, no torch). With the rolling
   window, Silero + Whisper stalled the loop on the Pi (first window 17.6 s, then froze). Silero, if re-enabled, is
   limited to 1 torch thread. Start-up warm-up runs one window through Whisper (first call was ~17 s cold).
   Each window logs `timing: silero …, whisper …`. If the server freezes: `pkill -USR1 -f server.py` from another
   terminal prints every thread's stack (faulthandler).
   Gemini is skipped with no key and never blocks the wake loop; window errors print a traceback.
7. Whisper decodes once (`temperature=0.0`, no previous-text conditioning, no timestamps, `max_new_tokens=60`).
   Before this, repetitive windows ("hello jacks, hello jacks, ...") took 10–36 s because of Whisper's default
   temperature-fallback re-decoding. After: tested 2026-10-03, wake → display arrow works from front/left/right,
   0.8–1.6 s per window, no stalls, ~1–2 s from phrase to arrow. Whisper hears "Jax" as "jacks" (accepted).
8. `XVF_HOST` (default `~/Documents/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/rpi_64bit/xvf_host`) and
   `DOA_OFFSET_DEG` configurable; `.env` loaded from next to `server.py`. Code defaults match the tested setup
   (beam 1, Silero off, threshold 0.015, offset 184) except the mic channels, which need `.env` (laptop-safe defaults).
   Audio stream opened with `latency='high'` (one startup "input overflow" seen before). Wake cooldown is measured
   from the window's capture time so Whisper jitter can't cause a double announcement.

Current focus: the merged demo program (see "One program for the demo" below). Phone/PWA is out of scope. server.py's
Flask on :5000 still serves `/state` and, since the merge with main, teammates' older page (root `index.html`,
`static/`; it keeps transcript history in the browser). The judge demo is `frontend/` on :8081 (camera_server.py).

## Setup on the Pi (from scratch)
1. `sudo apt install -y libportaudio2 python3-lgpio i2c-tools sox` and enable I2C: `sudo raspi-config nonint do_i2c 0`.
2. Mic tool: `cd ~/Documents && git clone https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY.git`, then
   `chmod +x ~/Documents/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/rpi_64bit/xvf_host`.
   Only needed for the fallback direction source and `doa_calibrate.py`'s fallback; it runs `sudo xvf_host`, so sudo
   must not ask for a password (Pi OS default user is fine). The normal source is USB (step 5).
3. Code: clone this repo, then `python3 -m venv --system-site-packages .venv && source .venv/bin/activate && pip install -r requirements.txt`
   (`--system-site-packages` so gpiozero/lgpio from Pi OS are visible).
4. `cp .env.example .env`, then add any keys. After that, edit `.env` with nano; don't copy over it again (it holds keys).
5. USB access to the mic for direction (no sudo): add a udev rule, then unplug/replug the mic:
   `echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2886", ATTR{idProduct}=="001a", MODE="0666"' | sudo tee /etc/udev/rules.d/99-respeaker.rules && sudo udevadm control --reload-rules && sudo udevadm trigger`
6. Bench tests: `i2cdetect -y 1` (display shows `3c`), `python display_cue.py`, `python motor_test.py left|right`, `python haptics.py`.
7. With the mic mounted: `python doa_calibrate.py` (in the venv), put its `DOA_FLIP_LEFT_RIGHT` / `DOA_OFFSET_DEG` in `.env`.
   Redo after changing `DOA_SOURCE` or remounting the mic.
8. Run: `python server.py`. First start downloads the Whisper model (~75 MB; the HF_TOKEN warning is harmless).
   Check the startup line `DOA source = usb DOA_VALUE ...`; if it says xvf_host, the udev rule (step 5) isn't active.

## Display
`display_cue.py`: on "Hello Jax", `server.py` also shows the turn direction on the OLED (yellow band "HELLO JAX 270°",
blue area arrow + LEFT/RIGHT/FRONT/BEHIND, same thresholds as `haptics.py`, clears after 4 s).
Bench test: `python display_cue.py` (also the display wiring test).
`display_cue.show_caption(who, text, where)`: caption mode (yellow band = small arrow + speaker label, blue = text
word-wrapped 3 lines × ~20 chars, paged every 2.5 s). Try it with Scribe: `python scribe_test.py --record 20 --display`.

128×64 SSD1306, two-colour: fixed yellow band at the top (~16 px), blue below. Colours are fixed in the glass,
so we can't colour-code people; speakers are shown by name/letter + direction arrow instead.
I2C on GPIO2/3 (pins 3/5, no clash with motor pins), address 0x3C. Only one program may drive the display at a time.

Live caption layout (option B, built: `display_cue.show_live`):
```
┌────────────────────────┐
│ ◀ ??                   │  yellow: who is talking NOW + direction arrow
├────────────────────────┤
│ did you bring the      │  blue: what's being said, last 3 lines,
│ motors? I left them    │  no speaker tags (LIVE_SPEAKER_TAGS=1 adds them)
│ by the door            │
└────────────────────────┘
```

## Speaker tagging plan
| Layer | Figures out | How |
|---|---|---|
| Direction (mic array) | where they are; the headset's live speakers `?`/`??` | DOA angle (instant, local; drifts when the wearer turns) |
| ElevenLabs batch Scribe | transcript + who spoke when within a clip | cloud STT with diarization (labels reset per request) |
| Voice reference clips (`refine.py`, web app only) | same voice across requests: stable `V1`/`V2` | each batch request starts with a short clip of every known voice; whoever the diarizer puts on V2's clip is V2 |
| Gemini | names and context ("A is Sam", who's talking to Jax, topic) | reads the recent labelled transcript every 12 s |

Unknown speakers are shown as question marks, one more per new unknown person: first `?`, second `??`, third `???`, ...
(e.g. `◀ ??`). When Gemini learns a name ("Hi, I'm Sam"), that person's marks become `SAM` everywhere, including history.
Counting marks gets hard past ~4, so the 5th unknown onwards could fall back to `?5`, `?6` (decide when testing).

LLM: keep Gemini (already in SPOOT, and counts for the MLH Gemini track). Gemini can only name people who get
named in conversation. It never sits in the buzz path.

## Web app for judges (`frontend/`, served by `camera_server.py`)
A visualisation for judges, not a companion app. Run `python server.py` and `/usr/bin/python3 camera_server.py`
on the Pi, open `http://<pi>:8081/` (same origin for page + camera: browsers auto-upgrading a separate camera URL to
HTTPS broke the feed; it auto-connects to ws :8765). Left: camera + tunnel-vision overlay (toggles), noise reticle
(`type:"sound"`), off-view speaker chip. Right: transcript + Gemini summary. Message contract: `frontend/README.md`.
**Voice-ID lines (`refine.py`, `REFINE_CAPTIONS=1`):** while the page is open, after each live caption finishes the
server batch-transcribes the audio since the last line sent (≤ `REFINE_WINDOW_SEC` 20 s, 3 s overlap, ch2) with
reference clips of known voices prepended, and sends `type:"refined"` lines (`V1`, `V2`, stable for the session)
that replace the live ones listed in `replaces`. Turns are sent when they end (held while the speaker keeps talking,
up to 8 s; a 1 s pause starts a new line); quiet speakers/turns (`REFINE_MIN_RMS`) are dropped; at most 6 retries per
new live caption. Gaps (errors, no page open) leave live lines alone rather than "replacing" text a line never
contained. Offline test: `python refine_test.py`.
Tested on the Pi 2026-10-04: works, 0.6–2.6 s per request, but 4 people became 16 voices. Now every voice gets a
reference clip, new voices need 1.5 s of clear speech, and `REFINE_MAX_VOICES` (6) caps them (past it: nearest
direction). Reticle: `type:"sound"` only on chip-flagged speech with agreeing readings (non-speech readings jump
between beams and drew random pings).

## .env
Copy `.env.example` to `.env` (the Pi's `.env` also holds the ElevenLabs key, so edit it with nano rather than
overwriting it). Put "jack" in `WAKE_EXTRA_NAMES`, not `USER_NAME_ALIASES` (aliases also drive name-only matching).
`GEMINI_API_KEY` turns on names + summary (`_gemini_loop`, background only; never in the buzz path).

## Known issues / to check
- Latency: Whisper tiny.en takes ~0.8–1.6 s per window on the Pi 5 (faster-whisper pads to 30 s). Watch the
  "(whisper N.NNs)" log. If wake detection is still unreliable/slow: Porcupine custom keyword (needs a Picovoice
  account) or Vosk with a restricted grammar (offline, no account), keeping Whisper for captions.
- Teammate's `origin/usher-audio` branch (sajjad): its DOA_VALUE + speech-flag idea is now in `doa_reader.py`. Its
  `Calibration` applies the offset BEFORE the flip, so its offset numbers can't be copied into `DOA_OFFSET_DEG`.
  Don't run its scripts alongside `server.py` (both use the mic's USB control interface).
- Mic orientation: `DOA_FLIP_LEFT_RIGHT` (default ON) + `DOA_OFFSET_DEG` (rotation, added to server.py).
  Measure both with `python3 doa_calibrate.py` once the mic is mounted on the headset; redo if it's remounted.
  **Mounting: the wearer's front = the side of the mic board away from the USB plug.**
  Calibrated 2026-10-03: `DOA_FLIP_LEFT_RIGHT=1`, `DOA_OFFSET_DEG=184` with the USB source (181 with xvf_host).
  (A first run gave flip=0/offset 179 because left and right were swapped during it; confirmed and fixed in the
  second run. Always use the WEARER's left/right.)
- The vendor `xvf_host` binary has no `DOA_VALUE` command (only the Python tool does); use `AEC_AZIMUTH_VALUES`,
  last value = auto-selected beam. Single readings jump between beams; average over the utterance.
- BCM4 is the default 1-wire pin: make sure `dtoverlay=w1-gpio` is off.
- Touchpoint's key/button circuit pulls up to VCC through the button with 100 Ω to GND: if that VCC is 5 V it can damage Pi GPIO (3.3 V only). Not used here; check before reusing.
- Mount the mic array rigidly to the headset so its 0° = the wearer's forward.
- Touchpoint's pin comments for dots 5/6 don't match its code. Check before reusing those pins.
- Drive motors through transistors or a driver (e.g. DRV2605L), not raw GPIO current.
- Temples are sensitive: keep default intensity low and adjustable.
- Test DOA in a noisy hall, not just a quiet room (echoes, false name triggers).

## One program for the demo (`python server.py`)
"Hello Jax" (Whisper, ch2) → motors (soft continuous guiding buzz) + arrow → catch-up text; live captions (Scribe Realtime,
ch0, `LIVE_CAPTIONS=1`) run alongside via `live_captions.run()`, fed from the same mic stream (`_audio_callback` →
`CaptionAudio.feed`) and the server's DOA history (`_ServerDirection`), reconnecting on their own.
**Decision: motors only fire for "Hello Jax"**, never for live captions (fewer chances for wrong cues).
"Hello Jax" is detected by both Whisper and the ElevenLabs live captions (whichever first; `_wake()` with a shared
cooldown). Captions go to the web app (`frontend/`) over the :8765 WebSocket as `type:"caption"` messages, plus
voice-ID `type:"refined"` lines while it's open (see "Web app for judges"). **Decisions: no sound-awareness haptics, no auto-start, no spoken replies** (people with
Usher syndrome can speak for themselves; the device shouldn't take that autonomy away).
`display_cue` priority: `show()` / `show_caption()` own the screen; `show_live()` is skipped meanwhile.
`live_captions` is imported after `.env` is loaded (it reads settings at import). Details: [ELEVENLABS.md](ELEVENLABS.md).

First full run (2026-10-03): live captions connected, "Hey Jax" buzzed left twice (old pulse version), catch-up worked (Scribe 0.8–0.9 s,
3 speakers with directions). Then the mic dropped off USB (`USBError 19 No such device`): DOA reconnected but the audio
stream died silently, so Whisper re-transcribed the same frozen 4 s and captions disconnected. Likely a loose cable or a
power dip from the motors on the Pi's 5V rail. Fixed in code: audio watchdog in `wake_loop` (no audio for 2 s → clear
buffer, re-scan PortAudio devices, reopen the stream). Hardware: motor modules on 3.3V, official 5V/5A supply, firm cable.

## Catch me up (built in server.py, tested once on the Pi: works)
On a wake, after the arrow: `_catch_up()` sends the last `CATCHUP_SEC` (30 s; audio ring buffer sized for it) to batch
Scribe via `scribe.py` (diarize, word timestamps), gives each speaker a direction (circular mean of the speech DOA
readings during their words, `_speaker_directions`), picks the caller (last speaker with a name word, else nearest the
wake direction, `_find_caller`), and shows the caller's last `CATCHUP_WORDS` (45) words with `display_cue.show_caption`
once the arrow has had 2.5 s. Runs as a background task: arrow/motors never wait; no key or no internet = no caption.
Terminal prints every speaker turn with label + direction. `scribe.py` = shared Scribe helpers (also used by
`scribe_test.py`, `live_captions.py`).

## ElevenLabs plan (sponsor track)
Core idea: "Catch me up". People with Usher syndrome often miss the start of a conversation.
1. Scribe (STT) transcribes continuously; each line is tagged with a speaker label and the DOA angle.
2. "Hello Jax" → buzz → wearer turns.
3. Micro display shows what that speaker said in the last ~30 s, then live captions.
(Spoken replies via TTS and sound-awareness haptics were considered and **decided against**, see "One program for
the demo".)

Additions:
- Voice Isolator on noisy-hall audio before Scribe, if the added latency is acceptable.
- Name learning (built): "Hi, I'm Sam" rule + Gemini, so captions read `SAM` instead of `??`.

Telling speakers apart: DOA angle first (free, local, instant), Scribe diarization second, LLM last
(names, summaries, off the critical path). Keep the "Hello Jax" → buzz loop local and independent of all of this.

Checked in ElevenLabs docs (2026-10-03): **Scribe v2 Realtime (WebSocket, ~150 ms) has NO diarization**; batch
`scribe_v2` does (up to 32 speakers). So: live captions = Realtime with speakers by direction (option B: only "who is
talking now" in the yellow band, no tags on past text); catch-up = batch Scribe with voice-based speakers.
**Current, detailed state of all ElevenLabs parts (API details, test results, tuning, to-dos): [ELEVENLABS.md](ELEVENLABS.md).**

Privacy: this streams other people's speech to the cloud. Have an answer ready for judges (consent, what's stored).

## Tracks (MHacks 2026)
One main track, unlimited sponsor tracks.
- Main track: **Beyond the Code (Hardware)** (decided). Grand Prize is judged across everything.
- Sponsor tracks, worth entering:
  - Best Project Built with ElevenLabs + [MLH] Best Use of ElevenLabs (needs Scribe actually in the build)
  - [MLH] Best Use of Gemini (SPOOT already supports it via `GEMINI_API_KEY`; needs it switched on and shown)
  - Notability (use it for brainstorming/wiring diagrams, 2+ screenshots + a note on Devpost)
  - [MLH] Best .Tech Domain Name (register a free .tech domain for the project page)
  - Best Design (Figma), if someone mocks up the display UI / headset in Figma
- Skipped (poor fit or big extra work): FinchNode, Fetch.ai, Photon, Neon, FREE-WILi, Relay, SpaceXAI (needs Cursor + space data), Spacetime, Nessie, Solana, Tiger Data, Presage.

## Stretch goals
- Mic-only closed loop: the mic is head-mounted, so DOA is already head-relative. If the speaker keeps talking, re-read DOA and re-call `guide()` until they're in front.
- IMU closed-loop guidance (covers the case where the speaker goes quiet)
- Speaker embeddings to keep tracking the same voice

## Design principles
- Co-design with Usher/DeafBlind users. Haptics over visuals, since remaining central vision is precious.
- Keep it simple. Avoid unnecessary abstractions.
