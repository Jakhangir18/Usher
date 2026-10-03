# Usher: "Hello Jax" headset

A headset for people with **Usher syndrome** (hearing loss plus progressive tunnel vision). When someone says
**"Hello Jax"**, a 360° mic array works out where the voice came from, and the wearer is told which way to turn:
an arrow on a small display, and vibration motors on the temples. Built at MHacks 2026.

## How it works
```
reSpeaker XVF3800 mic array ──► direction (DOA, averaged over the phrase)
          │
          └──► audio ──► rolling 4 s window ──► Whisper (tiny.en, on the Pi) ──► "hello" + "Jax"?
                                                                                    │
                                              OLED arrow (LEFT / RIGHT / FRONT / BEHIND) ◄──┤
                                              temple motors (pulse count = how far to turn) ◄┘
```
Only the greeting plus the name triggers it; the name on its own doesn't. Everything runs locally on the Pi.

## Hardware
- Raspberry Pi 5
- Seeed reSpeaker XVF3800 USB 4-mic array
- 128×64 SSD1306 OLED (I2C, yellow/blue)
- 2 micro vibration motors via MOSFET modules on GPIO4 (left) and GPIO5 (right). **Keep motor power low**: they're fragile.

## Setup and running
See **Setup on the Pi** in [CLAUDE.md](CLAUDE.md) for the full steps. In short:
```bash
python3 -m venv --system-site-packages .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit; see comments inside
python3 doa_calibrate.py    # once, with the mic mounted
python server.py
```
Bench tests: `display_cue.py` (display), `motor_test.py` (gentle motor power ramp), `haptics.py` (turn patterns).

## Roadmap
- ElevenLabs Scribe captions on the display ("catch me up" on what was said)
- Speaker tags (`?`, `??`, ... until a name is learned) and Gemini for names and context

## Credits
Builds on the team's earlier projects SPOOT (sound direction + Whisper) and Touchpoint (haptic motors).
