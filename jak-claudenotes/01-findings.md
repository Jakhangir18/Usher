# Findings (ranked)

Reviewed: commit `f48118a` on `main`, 2026-10-04 03:00-04:30 EDT. Method: full read of every
source file, an offline test harness (`tests/`, 144 checks), live calls to Gemini and ElevenLabs
through the production functions, Whisper tiny.en on a synthetic utterance, and two independent
model reviewers whose claims were re-verified against the code before they were kept.

Per the owner's decision nothing in the team's files was changed. Every finding carries a patch
you can apply in a minute and, where possible, a test in `tests/` that proves it (marked
`xfail(strict=True)`: it turns green the moment the fix lands).

Severity: **blocker** = demo fails; **high** = wrong behaviour a judge could see; **medium** =
wrong behaviour in a plausible situation; **low** = correctness nit or robustness; **info**.

## F1 [medium] "Hey Jake" and "hi Jackie" buzz the motors (Whisper path)

`server.py:1125` (`_is_wake_name`) and `server.py:1143` (`wake_phrase_detected`; the target list is built at line 1153).

The wake rule matches a word by exact spelling **or by Metaphone code** against all targets,
and the targets include `WAKE_EXTRA_NAMES` ("jack"). Metaphone("jack") is `JK`, which is also
the code of jake, jock, joke, jag, jacky and jackie (verified with jellyfish 1.2.1; "jax", "jacks",
"jaxx" share `JKS`). So on the Whisper path any greeting followed by one of those names fires the
buzz and the arrow. Commit `f48118a` already removed the extra names from the ElevenLabs caption
path for exactly this reason ("hey Jack to a real Jack in the room shouldn't buzz"); the Whisper
path still has the phonetic widening on top.

Scenario: someone at the table says "Hey Jake, look at this" -> Whisper hears it -> buzz toward
them, arrow, catch-up request to ElevenLabs. Test: `tests/test_wake.py::test_other_jk_names_do_not_wake_on_the_whisper_path`
(4 cases, currently xfail).

Fix: phonetic matching only for the real name and its aliases; extra names match exactly.

```python
def _is_wake_name(word, targets, phonetic_targets):
    if word in targets:
        return True
    try:
        import jellyfish
        code = jellyfish.metaphone(word)
        return any(code == jellyfish.metaphone(t) for t in phonetic_targets)
    except Exception:
        return False


def wake_phrase_detected(text_or_alts, extra_names=True):
    ...
    phonetic = _name_targets()
    targets = phonetic + (list(WAKE_EXTRA_NAMES) if extra_names else [])
    ...
            if word in WAKE_GREETINGS or not _is_wake_name(word, targets, phonetic):
```

Whisper's own mishearings keep working: "jacks" is an alias and shares `JKS` with "jax"; "jack"
stays an exact extra name. Then remove the `xfail` marker from the test.

Fix before the demo? Optional. Three lines, no behaviour change for "Hello Jax"; apply it if a
Jake, Jackie or Jack will be near the table.

## F2 [low] The arrow can be overwritten by a live caption during its last second

`display_cue.py:42` (`CUE_PRIORITY_SEC = 3.0`), `display_cue.py:25` (`HOLD_SEC = 4.0`),
`display_cue.py:179` (`show` claims the screen) and `display_cue.py:299` (`show_live` returns while `_priority_until` holds).

`show()` claims the screen for 3.0 s but clears it after 4.0 s. Between 3.0 and 4.0 s a live
caption frame (up to 5 per second while someone talks) replaces the arrow. With catch-up on, the
caption text usually takes over at 2.5 s anyway; with catch-up off (no key, no internet) the arrow
is cut short whenever people keep talking.

Fix: `CUE_PRIORITY_SEC = HOLD_SEC` (or drop the separate constant). Not demo-relevant if the
ElevenLabs key is set.

## F3 [low, ops] The ElevenLabs balance cannot be checked with the key the Pi uses

`GET /v1/user/subscription` and `/v1/user` return 401 `missing_permissions: user_read`; the key
was created with speech-to-text and models-read only, which is the right scope. Consequence: the
only way to see the remaining credit before the demo is the ElevenLabs dashboard. The runbook says
so. No code change.

## F4 [low] Legacy SPOOT page on port 5000

`server.py:249-257` (`flask_index`, `flask_manifest`) serve the repository-root `index.html` and
`static/usher.*`, the phone PWA from the earlier project. It looks like a product page ("Stay in
the conversation") and connects to the same WebSocket, so a judge or teammate opening
`http://<pi>:5000/` gets the wrong UI; `/manifest.json` is a 404 because the file is not in the
repo (`CLAUDE.md` still says `index.html` is not in the repo either). The judge app is on 8081.

Fix now: nothing; open 8081. Later: delete the route and the legacy files (see
`03-improvements.md`).

## F5 [low] `requirements.txt` only installs on the Pi

It is a `pip freeze` of the device venv and pins `RPi.GPIO`, `rpi_ws281x`,
`Adafruit-Blinka-Raspberry-Pi5-Neopixel`, `lgpio`, `sysv_ipc`, `pyftdi`, `binho-host-adapter`,
`hf-xet` and others the code never imports. On a laptop `pip install -r requirements.txt` fails,
which is why `04-dev-env.md` lists the twelve packages the code actually needs.

## F6 [low] With captions off and a Gemini key set, the old per-window classifier spends requests

`server.py:2023-2043` (`_report` -> `_gemini_update` unless `_caption_audio` is set, line 2028) and
`server.py:1282` (`_gemini_skip_reason`). When `LIVE_CAPTIONS=0` or the ElevenLabs key is
missing, every loud 4 s window whose Whisper text contains the name or a social phrase ("hey",
"excuse me", "move") goes to Gemini for a "directed at user" classification, at most once per
4 s. The result only feeds the legacy page on port 5000. Harmless in the demo configuration
(captions on), wasteful otherwise. Fix later: delete the path.

## F7 [info] Caption upload queue bound is soft

`live_captions.py:215-230` (`CaptionAudio.feed`). The `QUEUE_DROP` check reads
`self.queue.qsize()`, but the puts are scheduled with `call_soon_threadsafe`, so the size only
grows when the event loop runs its callbacks. With a 64 ms mic callback and a responsive loop the
bound holds (tested: `tests/test_live_caption_audio.py`); a loop blocked for seconds could queue
more. Not a demo risk; a `deque(maxlen=...)` would make it exact.

## F8 [info] Lint

`server.py:368`: f-string without placeholders (`ruff F541`). Nothing else from ruff's pyflakes
rules. pyright basic reports 44 "possibly None" errors, all from module globals initialised to
`None` and set in `main()`; none is reachable at runtime.

## Checked and found correct (so you do not have to)

- Shared wake cooldown between Whisper and ElevenLabs, including Whisper windows captured before
  the ElevenLabs trigger (`tests/test_wake.py`).
- `_caption_wake` after `f48118a`: live guesses buzz toward the current speech direction, the
  finished-piece backup cannot double-buzz, "hello jack" is ignored on that path.
- The rewritten `_angles_during` (binary search): same results as the old linear scan for
  overlapping, unordered and padded spans.
- `_speaker_directions`, `_find_caller`, `_track_speaker`, `_direction_during` fallback.
- `handle_client`: snapshot with `session_id`, `hello`/`refine` opt-in, cleanup on disconnect.
- `_publish_caption` / `_publish_refined` / `_drop_replaced`: voice-ID lines replace the right
  live lines, Gemini's transcript log follows, segment ids carry the reconnect attempt prefix.
- `haptics`: side choice, levels within the soft range, 5 s timeout, cancel on a new guide,
  closed-loop tracking ends the buzz early, "behind" counts as left.
- `display_cue` layout helpers and the never-raises promise without an OLED.
- `scribe` helpers: WAV packing, speaker turns with sound tags, filler/stutter cleanup,
  self-introduction rule including the wearer-name exclusions.
- `live_captions.People`: add/nearest/drift/name rules (rule names only unnamed people, Gemini
  may correct).
- Live: batch Scribe (0.7-0.9 s for 11 s, two speakers separated turn by turn, "Jax" spelled
  right), Scribe Realtime (session, partial then final with the same id, caption wake fired on a
  live guess 'Hello, Jax.', "I'm Sam" learned), Gemini JSON (`names` {"?": "Sam", "??": "Oliver"},
  two bullets), Whisper tiny.en on the same clip ("Hello, jacks, we saved you a seat" -> wake;
  the window without the phrase does not wake).
- Git history: no API key was ever committed.
