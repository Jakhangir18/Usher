# Usher: "Hello Jax" headset

A headset for people with **Usher syndrome** (hearing loss plus progressive tunnel vision). When someone says
**"Hello Jax"**, a 360° mic array works out where the voice came from, and the wearer is told which way to turn:
an arrow on a small display, and vibration motors on the temples. Live captions show what people are saying, and a
"catch me up" view shows what the caller just said. Built at MHacks 2026.

## How it works
```
reSpeaker XVF3800 mic array ──► direction (DOA + on-chip speech flag, averaged over the phrase)
   │
   ├─ ch2 ──► rolling 4 s window ──► Whisper (tiny.en, on the Pi) ──► "hello" + "Jax"?
   │                                                                     │
   │                          temple motors (soft buzz toward them, fades as you face them) ◄─┤
   │                          OLED arrow (LEFT / RIGHT / FRONT / BEHIND)     ◄─┤
   │                          catch-up: last 30 s → ElevenLabs Scribe (voice ID) → what the caller said
   │
   └─ ch0 ──► ElevenLabs Scribe Realtime ──► live captions on the OLED (+ arrow toward who is talking now)
```
Only the greeting plus the name triggers the motors; the name on its own doesn't. "Hello Jax" (detection, arrow,
motors) runs entirely on the Pi and works offline; captions and catch-up need internet and an ElevenLabs key.

## Hardware
- Raspberry Pi 5
- Seeed reSpeaker XVF3800 USB 4-mic array
- 128×64 SSD1306 OLED (I2C, yellow/blue)
- 2 micro vibration motors via MOSFET modules on GPIO4 (left) and GPIO5 (right), soft continuous buzz (0.10–0.20).

## Setup and running
See **Setup on the Pi** in [CLAUDE.md](CLAUDE.md) for the full steps. In short:
```bash
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit; add ELEVENLABS_API_KEY for captions/catch-up
python doa_calibrate.py     # once, with the mic mounted
python server.py            # everything
```
Bench tests: `display_cue.py` (display), `motor_test.py` (3 buzzes at a fixed 0.25), `haptics.py` (guiding buzz).
ElevenLabs tests: `live_captions.py` (captions alone), `scribe_test.py` (batch Scribe). Details: [ELEVENLABS.md](ELEVENLABS.md).

## Roadmap
- Names instead of `?`/`??` (Gemini, from "Hi, I'm Sam")
- Spoken replies with ElevenLabs Text to Speech

## Credits
Builds on the team's earlier projects SPOOT (sound direction + Whisper) and Touchpoint (haptic motors), and the
DOA_VALUE + speech-flag approach from the team's `usher-audio` branch.
