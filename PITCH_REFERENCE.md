# Usher: tech stack and design process

A reference for the pitch: how every part works, how the parts fit together, why we built it this way, what went
wrong along the way, and what we'd do next. Built at MHacks 2026.

---

## 1. The problem

**Usher syndrome** is the most common genetic cause of combined deafness and blindness. People are born with hearing
loss and slowly lose their side vision (tunnel vision, from retinitis pigmentosa). Together, these make a simple
moment hard: **someone across the room says your name, and you can't tell where they are.** You can't hear the
direction, and you can't see them unless they're straight in front of you. Group conversations are worse. By
the time you've found who's talking, you've missed what they said.

## 2. What Usher does

The wearer, Jax, wears a headset with a mic array, two small vibration motors at the temples and a tiny display.

1. **Someone says "Hello Jax."** The mic array works out where the voice came from.
2. **A soft buzz on one temple** guides Jax to turn toward them. It fades as Jax faces them and stops when
   they're in front. It keeps following the speaker while they keep talking.
3. **An arrow on the display** confirms the direction (LEFT / RIGHT / FRONT / BEHIND).
4. **"Catch me up":** a moment later, the display shows what that person said in the last 30 seconds, so Jax
   doesn't start the conversation already behind.
5. **Live captions** run the whole time: what's being said, plus an arrow toward whoever is talking now.
6. **Names:** "Hi, I'm Sam" (or someone saying "this is Sam") turns the unknown label `??` into `SAM`.

For the judges, a **web app** shows both sides at once: the headset camera with a simulated tunnel-vision overlay
("what they see"), next to the voice-identified transcript and a live AI summary ("what Usher gives them").

## 3. System overview

```
                         ┌──────────────────────── Raspberry Pi 5 ────────────────────────┐
 reSpeaker XVF3800       │                                                                 │
 4-mic array (USB) ──────┼─► audio, 6 channels ─┬─ ch2 (raw mic) ─► rolling 4 s window      │
                         │                      │                   ─► Whisper tiny.en      │
                         │                      │                      (local, offline)     │
                         │                      │                         │ "hello"+"Jax"?  │
                         │                      │                         ▼                 │
                         │                      │                  ┌── WAKE ──┐             │
                         │                      │                  │          │             │
                         │                      │      temple motors ◄┘          └► OLED arrow │
                         │                      │      (soft buzz, GPIO PWM)                │
                         │                      │                  │                        │
                         │                      │                  └► catch-up: last 30 s ──┼──► ElevenLabs
                         │                      │                     (ring buffer)         │    batch Scribe
                         │                      │                                           │    (who said what)
                         │                      └─ ch0 (beam) ──► live captions ────────────┼──► ElevenLabs
                         │                                        also a fast wake trigger  │    Scribe Realtime
 └─► direction (DOA) ────┼─► USB control reads, ~20/s, + chip's speech flag                │
                         │    → each person = a direction, each wake = an angle            │
                         │                                                                 │
                         │  server.py ── WebSocket :8765 ── captions, voice-ID lines,      │
                         │                                  summary, sound events          │
                         │  Gemini (every 12 s) ◄── recent transcript ── names + summary ──┼──► Google Gemini
 USB webcam ─────────────┼─► camera_server.py :8081 ── MJPEG stream + serves the web app   │
                         └─────────────────────────────────────────────────────────────────┘
                                         │
                                         ▼
                           Laptop browser: http://<pi>:8081/
                           camera + tunnel vision + reticle │ transcript + summary
```

**One rule shapes all of this:** the "Hello Jax" path (detect → buzz → arrow) runs **entirely on the Pi and works
offline**. Everything that needs the cloud (captions, catch-up, names, summary, voice ID) runs in the background and
can fail without affecting the buzz.

## 4. Hardware

| Part | Role | Notes |
|---|---|---|
| Raspberry Pi 5 | Runs everything | Python, one main program (`server.py`) |
| Seeed reSpeaker XVF3800 (USB, 4 mics) | Audio + direction of arrival (DOA) | 6 audio channels; we read the direction and the chip's own "speech" flag over USB |
| 2 micro vibration motors | Temple haptics | Driven through MOSFET trigger modules from GPIO4 (left) and GPIO5 (right), PWM |
| 128×64 SSD1306 OLED (I2C) | Arrow, captions | Two-colour glass: yellow band on top, blue below |
| USB webcam | Judge demo only | Shows "what they see" in the web app |
| Headset frame | Holds it together | CAD component models (Pi 5, mic array) in the repo for the enclosure design |

Wiring borrowed from our earlier hackathon project Touchpoint (motor modules); sound direction and Whisper from our
earlier project SPOOT.

## 5. Software, part by part

| File | What it does | Talks to |
|---|---|---|
| `server.py` | The main program. Opens the mic, runs the wake loop, fires the response, runs captions, catch-up, voice ID and Gemini as background tasks, serves the WebSocket | everything below |
| `doa_reader.py` | Reads direction + speech flag from the mic over USB (pyusb), ~20 readings/s | `server.py` DOA thread |
| `haptics.py` | The guiding buzz: continuous, softer as you turn closer, switches sides if you overshoot, fades out when facing | GPIO motors |
| `display_cue.py` | Everything on the OLED: arrow, catch-up caption (paged), live captions, priorities so they never fight | OLED over I2C |
| `live_captions.py` | ElevenLabs Realtime session: streams audio, splits text by speaker direction, labels people `?`/`??`, learns names from "I'm Sam" | ElevenLabs WebSocket |
| `scribe.py` | Batch ElevenLabs helpers: send a WAV, get words with speakers and timestamps, clean fillers ("um") and stutters | ElevenLabs REST |
| `refine.py` | Voice ID for the web app: keeps the same speaker number across separate batch requests | `server.py` |
| `camera_server.py` | Webcam → MJPEG stream; also serves the web app | Browser |
| `frontend/` | The judge demo page | WebSocket + camera stream |
| `doa_calibrate.py`, `motor_test.py`, `scribe_test.py`, `refine_test.py` | Calibration and bench tests | |

### 5.1 Hearing "Hello Jax" (local)
- Every second, Whisper (faster-whisper, `tiny.en`, on the Pi's CPU) transcribes **the last 4 seconds** of audio, if
  anything in it was loud enough.
- Every phrase lands whole in at least one window, and nothing gets lost while Whisper is busy.
- **The rule:** a greeting (hello / hi / hey / ...) followed within one word by the name. The name alone never
  triggers, because people say names all the time.
- Whisper usually hears "Jax" as "jacks" or "jack". We match by sound (Metaphone) and accept "jack" only after a
  greeting.
- **ElevenLabs as a second, faster trigger:** the live caption guess contains "Hello Jax" within a fraction of a second (ElevenLabs quotes ~150 ms; we didn't time it).
  The same rule and a shared cooldown mean one buzz per phrase, from whichever engine hears it first. Whisper
  remains the offline fallback.

### 5.2 Finding the direction
- The XVF3800 reports an angle plus its own voice-activity flag. Only readings taken *during speech* count.
- A wake uses the circular mean of the readings during the loud parts of the phrase (359° and 1° average to 0°,
  not 180°).
- **Calibration** (`doa_calibrate.py`): the wearer faces a speaker at front / left / right. The script works out
  whether left and right are mirrored and the rotation offset. Measured: flip on, offset 184°, remaining error 3–11°.

### 5.3 The buzz
- A continuous, low-level buzz (10–20% power) on the temple of the side to turn toward, softer as the wearer gets
  closer. A brief 35% nudge for 0.03 s helps the motor spin up.
- **No IMU, so no head tracking.** The mic is on the head, so the speaker's direction is already relative to the
  head. While the speaker keeps talking, we re-read their direction and steer live. When they go quiet, we assume a
  90°/s head turn. The buzz gives up after 5 s.
- Already facing them: one gentle tap on both sides.

### 5.4 "Catch me up"
- The Pi keeps the last ~30 s of audio in memory (never on disk).
- On a wake, that clip goes to **ElevenLabs batch Scribe** with speaker separation (diarization) and word timestamps.
- Each speaker gets a direction: the average of the mic's direction readings taken while their words were spoken.
- The caller is whoever said the name (otherwise whoever is nearest the arrow). Their last ~45 words appear on the
  display once the arrow has had 2.5 s on screen.
- Measured round trip: **0.6–2.4 s**.

### 5.5 Live captions
- ElevenLabs **Scribe v2 Realtime** over a WebSocket: a rough live guess while someone talks, a corrected final at
  each pause.
- **Realtime Scribe can't tell speakers apart**, so the mic array does: each direction is a person (`?`, `??`,
  `???`). We cut a new caption piece when the talker's direction changes, when speech stops (0.7 s), or every 6 s.
- The display shows **who is talking now** (arrow + label in the yellow band) and the last three lines of text with
  no speaker tags on past text (see "option B" in the decisions).
- Background chatter: quiet runs of words below a loudness threshold are dropped. Fillers ("um") and stutters are
  removed.
- Captions use mic channel 0 (the chip's processed, noise-suppressed beam). Wake detection uses channel 2 (one raw
  mic), which we chose after A/B testing both.

### 5.6 Names and summary (Gemini)
- Every 12 s, if anything new was said, the recent transcript goes to **Gemini (3.5 Flash-Lite)** as one JSON
  request.
- It returns names for any label the conversation makes clear (introductions, "this is Sajad", people addressing
  each other) and 2–4 summary bullets, with anything said to or about Jax first.
- Gemini never sits in the buzz path. If it fails, it backs off, and simple local rules ("I'm Sam") still name
  people.

### 5.7 Voice-identified transcript (the web app's "ideal version")
- The headset needs captions **instantly**, so it uses Realtime with direction-based speakers. Directions drift when
  the wearer turns their head, so the same person can reappear as a new label.
- The web app can wait a few seconds (about 2–4 s in practice). After each caption finishes, the Pi sends **batch Scribe** the audio
  since the last voice-identified line, plus a 3 s overlap, at most 20 s. Batch Scribe tells speakers apart
  **by voice**, and the app swaps the faint live line for the voice-identified one once that person's turn ends.
- **Problem:** batch Scribe numbers speakers per request (speaker 0 in one call can be speaker 1 in the next).
- **Our fix (`refine.py`):** each request starts with a short reference clip of every voice heard so far. Whichever
  speaker the diarizer puts on V2's reference clip *is* V2. Words that overlap the previous request also vote. So
  V1, V2, V3 are meant to stay the same people for the whole session, however the wearer moves. The first
  hardware test showed this is hard in a busy room (see below); the fixes are not yet tested on hardware.
- **Safeguards:**
  - Quiet speakers and turns (distant chatter) are dropped and can't become a voice.
  - A new voice needs 1.5 s of clear speech.
  - There's a cap on voices. Past it, an unknown voice joins the voice coming from the nearest direction.
  - Long monologues are sent in parts.
  - Retries are capped.
  - It only runs while the web app is open, because it's billed per second of audio sent.
- First hardware test: it worked end to end (0.6–2.6 s per request) but split 4 people into 16 voices in a busy
  room, which led to the reference-for-every-voice and voice-cap fixes above.

### 5.8 The web app (judge demo)
- **Left half, "what they see":**
  - The headset camera with a tunnel-vision overlay. Toggles go from full vision through mild / moderate / severe to
    none.
  - A **noise reticle**: arcs on the edge of the visible circle where someone is speaking, read like a radar
    from above (top = in front, bottom = behind). Louder means a thicker, redder arc. It only pings when the mic
    chip hears speech and its direction readings agree. Without speech, the readings jump between beams, which
    drew random pings in the first test.
  - An **off-view arrow chip** when someone is speaking outside the camera's view.
- **Right half, "what Usher gives them":**
  - The voice-identified transcript, with the live line faint underneath.
  - Below it, Gemini's summary, people chips and a "Jax ×N" mention counter.
- Served by the Pi (`http://<pi>:8081/`), connects to the headset automatically. **Try demo** runs a scripted
  conversation without any hardware.

## 6. How it all runs together

One Python process (`server.py`) with an asyncio event loop plus a few threads:

| Runs in | Job |
|---|---|
| Audio callback thread (sounddevice) | Copies ch2 into a ring buffer, feeds ch0 to the caption queue |
| DOA thread | Reads the direction over USB ~20×/s, keeps a timestamped history of speech readings |
| Haptics thread | Runs one guiding buzz at a time (a new wake cancels the old one) |
| Display threads | Draw the arrow / captions with a priority lock |
| asyncio: wake loop | Whisper on the last 4 s every second (in a worker thread), wake rule, cooldown |
| asyncio: live captions | ElevenLabs Realtime session, reconnects with backoff (2 → 30 s) |
| asyncio: catch-up, voice ID | Batch Scribe requests in worker threads |
| asyncio: Gemini | Names + summary every 12 s |
| asyncio: WebSocket :8765 | Sends captions, voice-ID lines, summaries and sound events to the web app |
| Separate process: `camera_server.py` | Flask, MJPEG stream, serves the web app |

**Shared timeline:** audio, direction readings and Scribe word timestamps are all mapped onto the same clock. So
"who said this word" and "where were they" can be joined: catch-up speaker directions, caption splits and voice-ID
directions all work this way.

## 7. Tech stack

| Layer | Technology |
|---|---|
| Hardware | Raspberry Pi 5, reSpeaker XVF3800, SSD1306 OLED, micro vibration motors + MOSFET modules, USB webcam |
| Language | Python 3.11 (Pi), plain HTML/CSS/JavaScript (web app, no framework) |
| Audio + direction | sounddevice (PortAudio), numpy, pyusb (XVF3800 `DOA_VALUE`) |
| Local speech | faster-whisper `tiny.en` (CTranslate2, CPU), jellyfish (Metaphone sound-alike matching) |
| Cloud speech | **ElevenLabs Scribe v2 Realtime** (WebSocket captions), **ElevenLabs Scribe v2** batch (diarization, word timestamps) |
| AI | **Google Gemini 3.5 Flash-Lite** (REST, JSON output): names + summary |
| Motors / display | gpiozero + lgpio (PWM), Adafruit CircuitPython SSD1306 + Pillow (I2C) |
| Servers | websockets (asyncio, :8765), Flask (camera + web app, :8081), OpenCV (camera capture → JPEG) |
| Design | Figma (web app redesign in progress), Atkinson Hyperlegible font (Braille Institute, for low-vision readers; bundled), CAD for the headset |

## 8. Numbers we measured

| What | Result |
|---|---|
| "Hello Jax" → arrow (Whisper) | ~1–2 s (0.8–1.6 s per Whisper window on the Pi 5) |
| ElevenLabs live guess containing "Hello Jax" | Noticeably faster than Whisper in testing (ElevenLabs quotes ~150 ms; we didn't time it) |
| Direction accuracy after calibration | 3–11° error, ~20 speech readings/s |
| Catch-up (batch Scribe, 30 s clip) | 0.6–2.4 s round trip |
| Batch Scribe, 20 s clip | 1.3–1.5 s, two speakers separated correctly turn by turn |
| Gemini summary + names | every ~12 s |
| Display text | 3 lines × ~20 characters, legible at 12 px |

## 9. Design decisions (and why)

**What triggers the device**
- **Greeting + name, never the name alone.** Names come up in conversation all the time ("I told Jax..."). A greeting
  means someone is actually calling you.
- **Motors only fire for "Hello Jax".** Not for captions or other sounds. Every buzz has one meaning, and fewer wrong
  cues build trust.
- **No sound-awareness haptics.** We could buzz for laughter or applause, but it isn't a safety device and we don't
  want it mistaken for an alarm detector.

**Respecting the user**
- **No spoken replies.** We considered text-to-speech replies and decided against it. People with Usher syndrome can
  speak for themselves; the device shouldn't take that autonomy away.
- **Haptics before visuals.** The remaining central vision is precious, so direction comes first as touch. The
  display only confirms and adds words.
- **A soft continuous buzz, not pulses.** Our first version counted pulses. On the temples it felt too strong and too
  much to decode. A gentle buzz that fades as you turn is intuitive: follow it until it stops.
- **No auto-start** for now: the device runs when started (hackathon scope).

**Architecture**
- **Local first.** Wake detection, direction, motors and the arrow all run on the Pi with no internet. The cloud only
  adds words. A bad Wi-Fi connection can never stop someone getting your attention.
- **Three speech engines, each for what it's best at:**
  - Whisper (local, offline): wake phrase.
  - ElevenLabs Realtime (fraction of a second): captions and the faster wake.
  - ElevenLabs batch (voice identification): catch-up and the web app transcript.
- **Directions for the headset, voices for the web app.** The headset can't wait, so it uses instant
  direction-based speakers. The web app can wait 1–2 s for voice-identified speakers. Speed for the wearer,
  accuracy for the record.
- **Gemini kept off the critical path.** It works on names and summaries, which aren't urgent. We chose Flash-Lite for
  speed and cost.
- **Lightweight integrations:** Gemini over plain REST (no SDK), direction over USB with pyusb (no sudo binary called
  20 times a second).

**Display**
- **The two-colour OLED can't colour-code people** (the colours are fixed in the glass). Unknown speakers are `?`,
  `??`, `???` in order of appearance; learned names replace them.
- **Option B for live captions:** no speaker tags on past text, only "who is talking now" in the yellow band. In
  testing, direction-based labels on past text were wrong too often, and a wrong label misleads more than no label.

**Mic**
- **Mic channels chosen by A/B test:** ch2 (raw, clean) for wake and catch-up, ch0 (processed beam,
  noise-suppressed) for captions. Never record the array as one channel: that averages all six into an echo.

**Web app**
- **A visualisation for judges, not a companion app.** A phone app is little use to someone with tunnel vision and
  hearing loss. The page exists to make judges *feel* the problem (tunnel vision over a live camera) and see the
  solution side by side.

## 10. Troubles we hit (and how we solved them)

**Speech recognition**

| Problem | Cause | Fix |
|---|---|---|
| "Hello Jax" got cut off before "Jax" | The loudness trigger ended the recording at the quiet "x" sound; one slow Whisper job blocked everything else | Rolling 4 s window every second: every phrase lands whole in some window |
| Server froze; first window took 17.6 s | Silero VAD (PyTorch) + Whisper fighting over the Pi's CPU | Whisper's built-in VAD instead, no torch; a warm-up call at startup |
| Some windows took 10–36 s | Whisper re-decoding repetitive audio at higher "temperatures" | Single decode (temperature 0, no conditioning, token cap): 0.8–1.6 s, no stalls |
| Whisper hears "Jax" as "jacks" / "jack" | Unusual name | Sound-alike matching (Metaphone) + "jack" accepted only after a greeting; ElevenLabs spells "Jax" right (keyterm hint) |
| Realtime Scribe rejected commits | Commits under 0.3 s apart are throttled | Minimum gap + one commit per new text |

**Audio and direction**

| Problem | Cause | Fix |
|---|---|---|
| Echoey recordings | Recording the 6-channel array as 1 channel averages all of them | Open 6 channels, keep the one we want |
| Left and right swapped | First calibration run mixed up the wearer's left/right | Re-ran calibration from the *wearer's* view; front = away from the USB plug |
| The mic dropped off USB during the first full run, captions died silently | Loose cable / power dip from motors on the 5 V rail | Audio watchdog: no audio for 2 s → re-scan devices and reopen (hardware: motors on 3.3 V, a 5 V/5 A supply, a firm cable) |
| "Bus error" when stopping the server | USB reader still running at exit | Stop the direction thread and close USB cleanly on shutdown |

**Haptics, display and names**

| Problem | Cause | Fix |
|---|---|---|
| Buzz pulses felt too strong | Temples are sensitive; the micro motors are fragile | Soft continuous buzz at 10–20%, power changed in small steps |
| Arrow and live captions overwrote each other | Two features drawing on one tiny display | Priority lock: the arrow and catch-up own the screen until done |
| Names flipped OLIVER ↔ SAJAD | Catch-up and Gemini both renamed people, and directions drift while the wearer turns | Local rules only name unnamed people; only Gemini can change a name |
| Everyone got named "JACK" | "I am Jack" (misheard Jax) was learned as a name | All spellings of the wearer's name are excluded everywhere, and wrong names get cleaned up |
| One name spelled three ways (Ajad / Sajjad / Sajad) | Speech-to-text guessing at spelling | Similar spellings don't overwrite; Gemini picks the likeliest spelling |
| Gemini calls failed | `gemini-2.0-flash` was shut down | Switched to Gemini 3.5 Flash-Lite |
| New labels (`???`) when the wearer turns | Speakers are directions relative to the head | Voice-identified speakers in the web app (`refine.py`) |

**Web app**

| Problem | Cause | Fix |
|---|---|---|
| Camera feed broke in the browser | Browser auto-upgraded the camera URL to HTTPS; the Pi only speaks HTTP | The Pi serves the page and camera from the same address |
| Web page loaded with no styling | Flask's built-in `/static` route pointed at a folder that didn't exist | Pointed it at the web app's folder |
| (Caught in code review) Voice-ID lines could delete live text they never contained, and duplicated lines after a page reload | After an error or while no page was open, the "what's been handled" marker didn't move with the audio actually sent | The marker moves with every gap; live lines from a gap, or from speech judged too quiet, are kept rather than "replaced" |
| 4 people became 16 voices in a busy room | Only the 4 most recent voices had reference clips; short talkers never got one; chatter from other teams created voices | Reference clip for every voice, 1.5 s of clear speech to create one, quiet speakers ignored, a voice cap with a nearest-direction fallback |
| Reticle pinged from random places | Without speech, the mic's direction readings jump between beams | Ping only on speech with agreeing direction readings |
| (Caught in code review) One "Hello Jax" could buzz twice | When Whisper fired first, the caption trigger didn't remember it had seen the phrase | Once the phrase is seen in a caption piece, that piece can't trigger again |

## 11. Privacy and safety

- The device streams other people's speech to the cloud (ElevenLabs, Gemini) for captions. Nothing is saved to disk
  on the headset: audio lives in a ~30 s in-memory buffer, and the camera stream isn't recorded. Those cloud
  services have their own data-retention policies, which a real product would need to choose and disclose.
- API keys live in a local `.env` file that is never committed.
- The demo web app has no login: anyone on the same Wi-Fi who knows the Pi's address could open it and see live
  transcripts. That's fine for a judged demo on our own hotspot; a real product would need pairing and encryption.
  (An older teammate page in the repo root keeps a transcript history in the browser; the judge demo doesn't.)
- The wake path works fully offline, so the core function doesn't depend on sending audio anywhere.
- It is an attention and conversation aid, **not** a safety or alarm device, and we don't pitch it as one.

## 12. Given more time

**Better guidance**
- **An IMU (e.g. BNO055)** for true head tracking: the buzz could steer accurately even after the speaker stops
  talking.
- **Haptic language co-designed with Usher and DeafBlind users** (intensity, patterns, placement), with adjustable
  strength per person.

**Faster, more private speech**
- **On-device voice fingerprints** (speaker embeddings such as ECAPA) for consistent speakers on the headset itself,
  offline and instant, instead of only in the web app.
- **A dedicated wake-word engine** (e.g. Porcupine or Vosk with a fixed grammar) for lower latency and power than
  Whisper windows.
- **More on-device speech** (a stronger Whisper model on better hardware) so captions work offline and less audio
  leaves the device.
- **Noise handling:** steer the mic array's beam toward the selected speaker; ElevenLabs Voice Isolator before batch
  transcription.

**Product**
- **A better display:** a near-eye micro-OLED or AR glasses placed in the wearer's remaining field of view, larger
  text options, high contrast.
- **Refreshable braille output** (Bluetooth braille display) for users whose vision has gone further.
- **Product polish:** battery, enclosure, auto-start on boot, a consent indicator light, multiple languages.
- **Real user testing** in noisy places (cafés, classrooms) rather than quiet rooms.

## 13. Sponsor tracks this touches

- **Beyond the Code (Hardware):** the main track.
- **ElevenLabs:** Realtime captions, batch voice identification (catch-up + voice-ID transcript), and the fast wake
  trigger.
- **Gemini:** names and conversation summaries.
- **Best Design (Figma):** the judge demo redesign (in progress).

## 14. Credits

Builds on the team's earlier hackathon projects **SPOOT** (sound direction + Whisper server) and **Touchpoint**
(haptic motor control). The direction-over-USB approach and the first web page and camera server came from
teammates' work on the `usher-audio` branch; the headset CAD is in `main`.
