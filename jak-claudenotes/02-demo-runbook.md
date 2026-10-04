# Demo runbook (Sunday judging)

Written 2026-10-04, 03:40 EDT, against commit `f48118a`. Everything below was checked by reading
the code and by running the API and logic checks in `tests/`; nothing here was run on the Pi, so
the rehearsal step is not optional.

## Before you leave for the judging table

1. **Pull and rehearse once with the real hardware.** Commit `f48118a` ("Fix review findings...")
   changed the caption wake, the segment ids, the refine budget and the Whisper thread, and its own
   message says it has not been run on the Pi. Do one full run: "Hello Jax" from the left, the
   right and the front; watch the catch-up text; open the web app; confirm voice-ID lines appear.
2. **Keys.** The Pi's `.env` needs `ELEVENLABS_API_KEY` and `GEMINI_API_KEY`. Both keys were valid
   at 03:00 EDT: Gemini lists `gemini-3.5-flash-lite`, ElevenLabs accepts batch and Realtime
   speech-to-text. Git history contains no key (checked with `git log -p --all -S`). Rotate both
   keys after the hackathon: they were pasted into a chat today.
3. **ElevenLabs balance: look at the dashboard, not the API.** The key has no `user_read`
   permission, so `GET /v1/user/subscription` answers 401. Realtime streams the whole time captions
   are on; voice-ID re-sends reference clips on every request (the team's own estimate: 8-15 s of
   audio per second of conversation while the web app is open). `REFINE_BUDGET_SEC=3600` caps one
   server run; a restart resets the cap.
4. **Calibration.** If the mic array was remounted on the printed headband (finished 02:06), run
   `python doa_calibrate.py` again and put the new `DOA_FLIP_LEFT_RIGHT` / `DOA_OFFSET_DEG` into
   `.env`. The front of the wearer is the side of the board away from the USB plug.
5. **Power and cables.** Motor modules on 3.3 V, the official 5 V/5 A supply, a firm USB cable on
   the mic: the first full run lost the mic to a USB drop-out, and the watchdog can only reopen a
   mic that comes back.
6. **Cold-boot check for the motors.** Power the Pi with the motors wired, before any program
   runs: the temples must stay silent. If they buzz until `server.py` starts, see R16 in
   `01-findings.md` (GPIO4/5 power up with pull-ups); today, start `server.py` straight after
   boot.
7. **Whisper model is cached.** The first `python server.py` on a fresh clone downloads about
   75 MB; do that on Wi-Fi, not at the table.
8. **Hotspot.** Laptop and Pi on the same hotspot. The page must be opened over plain `http://`;
   an `https://` page cannot reach `ws://` or the camera.

## Start order and what the terminal must say

```bash
cd ~/usher && source .venv/bin/activate
python server.py                      # terminal 1
/usr/bin/python3 camera_server.py     # terminal 2 (system Python: OpenCV lives there)
```

Open `http://<pi-address>:8081/` on the laptop. Not port 5000: that is the old SPOOT phone page that
`server.py` still serves, and it shows the wrong UI.

Lines to see in terminal 1 before anyone talks:

| Line | Meaning |
|---|---|
| `DOA source      = usb DOA_VALUE (angle + on-chip speech flag)` | direction over USB. If it says `xvf_host`, the udev rule is not active: readings are slower and have no speech flag |
| `AUDIO_DEVICE   = [n] ... XVF3800 ...` | the right mic; `AUDIO CHANNEL   = 2 of 6` |
| `Warm-up done in N.Ns` | Whisper loaded and warmed |
| `LIVE CAPTIONS   = on (Scribe Realtime, mic channel 0)` and later `live captions: listening` | ElevenLabs connected |
| `CATCH-UP        = last 30s via ElevenLabs Scribe after each wake` | key found |
| `GEMINI_KEY      = set` | names + summary on |
| `listening… peak=0.0xx (threshold 0.0150)` every 3 s | mic alive; peak must sit below the threshold when the room is quiet |

A line `WARNING haptics: motors unavailable (...); running with dummy motors` means **no buzz at
all**: the venv was created without `--system-site-packages` or `GPIOZERO_PIN_FACTORY=lgpio` is
missing. Fix before the demo, the server will otherwise look healthy.

## At the table

- The trigger is **greeting + name within one word**: "Hello Jax", "Hey Jax", "Hi there Jax".
  "Jax!" alone never buzzes, by design; say so to the judges before they try it.
- Two speakers, clearly apart (left and right of the wearer), 1-2 m away, one at a time. Direction
  labels cannot separate two people standing on the same side.
- One buzz per 4.5 s: a second "Hello Jax" inside the cooldown is ignored. Wait before repeating.
- Whisper alone takes 1-2 s; with internet, ElevenLabs fires the buzz in about 0.2 s. If the
  hotspot is slow, the arrow still comes, later.
- Names: "Hi, I'm Sam" names the speaker at once (local rule); "this is Oliver" and names used in
  address come from Gemini within about 12 s.
- **Do not say "Hi, I'm Jax" while wearing it.** The wearer's own introduction counts as the wake
  phrase (R1 in `01-findings.md`): motors buzz toward the wearer, a catch-up of their own words
  follows, and a judge's real "Hi Jax" in the next 4.5 s is swallowed. Introduce the persona
  before putting the headset on, or set `WAKE_MAX_GAP=0` in `.env` (then only "Hello Jax" with
  nothing in between wakes; "hello there Jax" stops working).
- Known false triggers on the Whisper path: "hey Jake", "hi Jackie", "hey jock" and a plain "Hey
  Jack" buzz, because "jack" is an accepted spelling and is also matched by sound (F1, R6). Avoid
  those names at the table, or remove `jack` from `WAKE_EXTRA_NAMES` in `.env` (Whisper then
  misses the phrase when it hears "jack"; the ElevenLabs path still catches "Jax").
- If live captions vanish from the OLED while the terminal keeps printing `caption ...` lines,
  the caption session reconnected (R2): restart `server.py`.
- Voice-ID lines in the web app stop after about 4-7 minutes of conversation per server run
  (`REFINE_BUDGET_SEC`, R4), and a page reload after that sticks the page in "identifying
  voice..." mode (R3). Restart `server.py` between judge groups, or raise `REFINE_BUDGET_SEC`
  and watch the balance.
- The web page's **Try demo** button runs a scripted conversation with no hardware. It is the plan B
  if the Pi dies at the table; have a phone video of a real run as well.

## If something goes wrong

| Symptom | Likely cause | What to do |
|---|---|---|
| No buzz, but the terminal prints `wake phrase (...): BUZZ toward ...` | dummy motors, wiring, or 3.3 V missing | check for the `dummy motors` warning at start-up; otherwise stop `server.py` (it holds GPIO4/5; a second program gets `GPIO busy`), run `python motor_test.py left`, start `server.py` again |
| No `wake phrase` line at all | the phrase did not land in a window, or too quiet | set `WAKE_LOG=1` in `.env` to print Whisper's text; compare `listening… peak=` with `VOLUME_THRESHOLD` |
| Buzz comes from the wrong side | calibration | `python doa_calibrate.py` with the mic mounted; always use the wearer's left/right |
| `WARNING audio: no audio for 2.0s, reopening the mic...` | USB mic dropped (cable, power) | the watchdog reopens it; if it repeats, reseat the cable and check the supply |
| Captions stop, `live captions: ... retrying in N s` | internet gone | wake, arrow and motors keep working; captions return by themselves (2 to 30 s backoff) |
| Catch-up text never appears | no internet or no key; it fails silently by design | check `CATCH-UP = ...` at start-up and the `catch-up:` lines after a wake |
| `refine: spending cap reached` | `REFINE_BUDGET_SEC` used up | restart `server.py` (the cap is per run) or raise it in `.env` |
| Display shows nothing or stale text | two programs on the display, or an I2C hiccup | only `server.py` may run; restart it; `i2cdetect -y 1` must show `3c` |
| Web page says `Can't reach ... is server.py running?` | server down, or laptop on another network | restart `server.py`; both devices on the hotspot; use `http://`, not `https://` |
| Camera panel dark, page works | `camera_server.py` not running or OpenCV missing | `/usr/bin/python3 camera_server.py`; `sudo apt install python3-opencv python3-flask` |
| Server stops reacting | a hang somewhere | `pkill -USR1 -f '(^|[ /])server\.py'` from another terminal prints every thread's stack (the plain `-f server.py` form from CLAUDE.md also kills `camera_server.py`, see F9); then restart |
| `Bus error` on Ctrl+C | USB reader torn down late (fixed by `_stop_doa`) | harmless; start again |
| Motors buzz after `server.py` was stopped | pins released without a pull, MOSFET gate floating (R16) | `pinctrl set 4,5 op dl`, or start `server.py` again; a gate pull-down resistor is the real fix |

## Privacy answer for the judges

Other people's speech goes to ElevenLabs (captions, catch-up, voice-ID) and the transcript to
Gemini (names, summary). Nothing is written to disk: audio lives in a 30 s ring buffer in memory,
the camera stream is not recorded, the transcript is only in the browser tab. The "Hello Jax"
path (detect, direction, buzz, arrow) is fully local and works offline. A real product would need
consent handling and a pairing step; the demo page has no login and is meant for a hotspot.

Keep the pitch honest: an attention and conversation aid, not a safety or alarm device; spoken
replies were considered and rejected because users speak for themselves.
