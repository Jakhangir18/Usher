# Running the checks on any laptop (no Pi needed)

Everything in `jak-claudenotes/tests/` runs on a plain laptop or server: no mic array, no GPIO, no
OLED. The hardware modules are stubbed in `tests/conftest.py`; the logic they call is real.

## One-time setup

```bash
cd Usher
python3 -m venv .venv-vps            # any name; keep it out of git (it is not in .gitignore)
.venv-vps/bin/pip install numpy requests websockets flask flask-cors jellyfish pillow pytest ruff
```

`requirements.txt` is a `pip freeze` of the Pi venv and does not install off the Pi (it pins
`RPi.GPIO`, `rpi_ws281x`, `lgpio`, `sysv_ipc`, ...), which is why the dev environment installs the
short list above instead. `faster-whisper` is not needed for the offline suite (the wake rule is
tested on text); install it only for the heavy check in `tests/heavy/`, which `check.sh` leaves out.

## Run

```bash
jak-claudenotes/check.sh             # ruff (report), compileall, node --check, offline pytest
LIVE=1 GEMINI_API_KEY=... ELEVENLABS_API_KEY=... USHER_TEST_WAV=/tmp/usher-test.wav jak-claudenotes/check.sh
```

Keys go in the environment, not in a file: `server.py` reads the environment first and `.env`
second, so no `.env` is needed on a dev machine. The live tests are skipped when a key or the WAV
is missing, so the offline run is always safe.

Make the test WAV once (about 11 s of two synthetic voices, Gemini TTS, costs a fraction of a cent):

```bash
GEMINI_API_KEY=... .venv-vps/bin/python jak-claudenotes/tools/make_test_wav.py /tmp/usher-test.wav
```

## What is stubbed, and why it is still a real test

| Pi dependency | Stub in `conftest.py` | What the tests still exercise |
|---|---|---|
| `sounddevice` (needs libportaudio) | module stub; `InputStream` raises | ring-buffer maths (`_loud_spans`, `_snapshot_buffer` callers), `CaptionAudio` clock |
| `gpiozero` (motors) | import blocked, so `haptics.py` takes its own documented dummy-motor path | side choice, levels, timeout, cancel, closed-loop tracking |
| `usb`, `doa_reader.open_doa` | fake reader that reports nothing | every lookup on `server._doa_history` (wake direction, catch-up directions, tracking, caption wake) |
| `board`, `busio`, `adafruit_ssd1306` (OLED) | import blocked; `display_cue` catches it | text wrapping/layout, direction thresholds, the never-raises promise |
| ElevenLabs, Gemini | not stubbed: offline tests never call them; live tests call the real APIs | request/response contracts through the production functions |

`server.py` is imported as a module. It starts its DOA thread and creates the Flask app at import,
which is fine with the fake reader; the audio stream and the asyncio loops only start in `main()`,
which the tests never call.

## Adding a test

- Put pure-logic tests next to the existing files (one file per module).
- For anything that needs `_doa_history`, fill it under `server._doa_lock` (see `test_geometry.py`).
- For WebSocket or publish paths, run the coroutine with `asyncio.run` and a fake socket
  (see `test_server_protocol.py`).
- A test that documents a known bug is marked `xfail(strict=True)` with the finding id, so it turns
  into a failure the day the bug is fixed and reminds you to flip it.
