# Usher judge demo (web app)

A visualisation for judges, not a companion app: the left half shows what the wearer sees (headset camera
with a tunnel-vision overlay), the right half shows what Usher gives them (a voice-identified transcript and a
Gemini summary). Started from syedak1's page on the `usher-audio` branch; visuals will follow the Figma redesign.

## Run it

On the Pi, in `~/usher`, start both:
```bash
python server.py
/usr/bin/python3 camera_server.py
```
Then open **http://\<pi-address\>:8081/** on any laptop on the same network. `camera_server.py` serves this page,
so the page and camera share one plain-HTTP address (browsers that auto-upgrade a separate camera URL to HTTPS
broke the feed) and the page connects to the headset by itself.

**Try demo** works anywhere without a Pi (open `index.html` directly, or any static server).

## Data from the Pi

| What | Where | Started by |
|---|---|---|
| Captions, voice-ID lines, summary, sound events | WebSocket `ws://<pi>:8765` | `python server.py` |
| Headset camera (MJPEG) | `/camera/stream` on `http://<pi>:8081` | `/usr/bin/python3 camera_server.py` (needs `sudo apt install python3-opencv`; `CAMERA_DEVICE=/dev/video0` by default) |

Messages (all JSON; `angle`: 0 = front, 90 = right, 270 = left, relative to the wearer's head):

```json
{"type": "snapshot", "refine": true, "captions": [...recent finished lines]}
{"type": "caption", "session_id": "...", "segment_id": "live-12", "speaker_id": "??", "speaker_label": "OLIVER",
 "text": "...", "is_final": true, "angle": 274.5, "timestamp": 1790000000.0, "source": "scribe_v2_realtime"}
{"type": "refined", "session_id": "...", "segment_id": "voice-7", "speaker_id": "V2", "speaker_label": "Sajad",
 "text": "...", "is_final": true, "angle": 70.0, "start": 1790000001.2, "end": 1790000003.9,
 "replaces": ["live-12", "live-12.1"], "timestamp": 1790000005.1, "source": "scribe_v2"}
{"type": "summary", "bullets": ["..."], "names": {"V2": "Sajad", "??": "Oliver"}, "model": "...", "timestamp": 0}
{"type": "sound", "angle": 70.0, "level": 0.04, "speech": true, "timestamp": 0}
```

- **caption**: ElevenLabs Realtime, ~0.2 s after speech. Live guesses have `is_final: false`; the final text
  reuses the same `segment_id`. Speakers (`?`, `??`) are told apart by direction.
- **refined**: batch Scribe, a couple of seconds after each speaker's turn ends (a long turn is sent in parts),
  speakers told apart by voice (`V1`, `V2`, stable for the session). Replaces the live lines listed in `replaces`.
  Only sent while the page is open (`refine: true`).
- With `refine`, the page shows live captions as faint "identifying voice…" lines and counts only voice-ID
  speakers as people. Live lines nothing replaced within 12 s (offline, API error) stay as lines in a neutral
  colour (not counted as people).
- An empty final caption (`text: ""`) means that piece was all background chatter: it just clears "speaking…".
- **summary**: Gemini every ~12 s: 2-4 bullets plus names learned for any label.
- **sound**: ~10 times a second while someone is speaking loudly enough from a clear direction (chip speech flag + agreeing readings), for the reticle.

## Files
`index.html` + `static/app.js` + `static/app.css` + `static/fonts/` (Atkinson Hyperlegible, bundled so it works offline).
