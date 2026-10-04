# Findings (ranked)

## Read this first: the five things that matter before judging

Whether to apply each one today, and how to check it on the Pi in two minutes: `05-apply-today.md`.

| Id | What a judge could see | Fix size |
|---|---|---|
| R1 | The wearer saying "Hi, I'm Jax" buzzes the motors toward themselves and runs a catch-up on their own words | 2 lines, or `WAKE_MAX_GAP=0` in `.env` |
| R2 | After any caption reconnect (hotspot blip) the OLED shows no live captions for minutes, while the terminal looks healthy | 2 lines |
| R4 + R3 | Voice-ID lines stop after about 4-7 minutes of conversation per server run (`REFINE_BUDGET_SEC`), and a page reload after that sticks the web app in "identifying voice..." mode | restart `server.py` between judge groups, or raise the cap; 1 line in `app.js` |
| F1 + R6 | "Hey Jake", "hi Jackie" and a plain "Hey Jack" to a real Jack buzz through the Whisper path | 3 lines, or drop `jack` from `WAKE_EXTRA_NAMES` when a Jack is in the room |
| R5 | Duplicate lines in the web app transcript (faint live line + VOICE-ID line with the same words) once speech is dropped as quiet or someone laughs | 6 lines |

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

## F9 [low, docs] `pkill -USR1 -f server.py` also hits `camera_server.py`

`CLAUDE.md` ("If the server freezes: `pkill -USR1 -f server.py`") and `server.py:75-76`
(`faulthandler.register(signal.SIGUSR1, all_threads=True)`). `pkill -f` matches the pattern
anywhere in the command line, and `/usr/bin/python3 camera_server.py` contains `server.py`.
Only `server.py` installs a SIGUSR1 handler; for `camera_server.py` the default action of
SIGUSR1 is to terminate, so the documented command kills the judge page and the camera.
Use `pkill -USR1 -f '(^|[ /])server\.py'` (matches `python server.py` and `/path/server.py`,
not `camera_server.py`). The runbook uses that form.

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

---

# Findings from the two independent reviewers (verified)

Two Opus reviewers read the code independently (one on `server.py`, one on `live_captions.py`,
`refine.py`, `display_cue.py`, `haptics.py`, `camera_server.py`, `app.js`). Every claim below was
re-checked against the cited lines before it was kept; the claims that could not be confirmed are
listed at the end as suspicions, not findings.

## R1 [high] The wearer's own "Hi, I'm Jax" is a wake phrase

`server.py:1161-1162` (`before = words[max(0, i - 1 - WAKE_MAX_GAP):i]`, any greeting in `before`),
called from `server.py:1628` (captions) and `server.py:2152` (Whisper).

With `WAKE_MAX_GAP=1`, one word may sit between the greeting and the name. "Hi, I'm Jax" becomes
`["hi", "im", "jax"]` (apostrophes are stripped), so `before` is `["hi", "im"]` and the rule fires.
Same for "Hey, it's Jax". The wearer's voice is the loudest thing the head-mounted array hears, so
the loudness gates pass. Result: motors buzz toward the wearer's own direction, the OLED shows
HELLO JAX, a billed catch-up shows the wearer's own words (the caller rule picks the last speaker
who said the name), and a judge's real "Hi Jax!" in the next 4.5 s is swallowed by the cooldown.

Test: `tests/test_wake.py::test_the_wearer_introducing_themselves_does_not_wake` (xfail).
"Hello, I am Jax" is safe already: two words between greeting and name.

Fix (code): in `wake_phrase_detected`, skip the match when the word right before the name is a
self-reference:

```python
            before = words[max(0, i - 1 - WAKE_MAX_GAP):i]
            if before and before[-1] in ("im", "i", "am", "its", "it", "is", "this", "me"):
                continue
            if any(b in WAKE_GREETINGS for b in before):
                return True
```

Fix (no code, today): `WAKE_MAX_GAP=0` in `.env`. Then only "greeting + name" with nothing in
between wakes; "hello there Jax" stops working, which is easy to avoid at the table.

## R2 [high] After a caption reconnect the OLED stops showing live captions

`live_captions.py:288` (`"seq": 0` in the per-session `state`), `live_captions.py:312-314`
(`state["seq"] += 1`, passed to `show_live`), `display_cue.py:272` (`_live_seq = 0`, module global),
`display_cue.py:302-304` (`if seq <= _live_seq: return`), `server.py:2003-2011` (the reconnect loop
calls `run()` again in the same process).

`show_live` drops any frame whose sequence number is not higher than the last one drawn, so late
frames cannot overwrite newer ones. The counter lives in `run()`'s `state` and restarts at 0 on
every reconnect, while `display_cue._live_seq` keeps the previous session's maximum. After a
session that drew 1,500 frames (about 5 minutes of talk at 5 redraws a second), the next session's
first 1,500 frames are silently skipped: the terminal prints `caption ...` lines, the web app gets
everything, the headset display stays blank (after the last frame clears in 4 s) until the counter
catches up. Reconnects happen on a hotspot drop and whenever `send_audio` resyncs ("caption uplink
behind").

Test: `tests/test_live_caption_reconnect.py::test_frame_counter_keeps_rising_across_reconnects`
(xfail; it runs `live_captions.run()` twice against a fake Scribe socket and records the frame
numbers handed to `display_cue.show_live`). `tests/test_display_cue.py` shows the in-session guard.

Fix: a counter that survives reconnects. In `live_captions.py`:

```python
import itertools
_FRAME_SEQ = itertools.count(1)   # module level: monotonic across run() calls
...
        if shown:
            seq = next(_FRAME_SEQ)
            threading.Thread(target=display_cue.show_live,
                             args=(people.display(state["speaker"]) or "", shown, state["where"], seq),
```

(and drop `"seq"` from `state`). Workaround at the table: restart `server.py` if captions vanish
from the OLED while the terminal still prints them.

## R3 [medium] After the spending cap, a page reload sticks the web app in voice-ID mode

`server.py:2073-2074` (snapshot: `refine: _refine_on()` plus `captions: list(_recent_captions)`),
`server.py:1798-1799` (cap sets `_refine_stopped`), `frontend/static/app.js:80-81` (`S.refine =
!!m.refine` then `m.captions.forEach(receive)`), `app.js:105` (`if (refined) S.refine = true;`).

Once the cap has stopped the refine loop, a client that connects or reconnects gets `refine:
false` and the recent lines, which still include `refined` ones. Replaying a `refined` line sets
`S.refine` back to true. From then on every live caption is drawn as a faint "LIVE / identifying
voice..." line, is not counted as a person, and only becomes a normal line after 12 s. Clients that
were connected at the moment of the cap are fine (they get an empty snapshot).

Fix (`app.js`): apply the server's flag before and after the replay. Before, so that on a fresh
load with voice-ID on the replayed live lines stay faint and do not become people (`connect()`
resets `S.refine` to false, `app.js:196`); after, so a replayed voice-ID line cannot switch the mode
back on:

```js
      S.refine = !!m.refine;
      Array.isArray(m.captions) && m.captions.forEach(receive);
      S.refine = !!m.refine;
      return render();
```

## R4 [medium, ops] The default refine budget lasts about 4-7 minutes of conversation

`server.py:497-500`: the comment says voice-ID runs at "~8-15 s of audio per second while the app
is open" (every request re-sends up to six 2.5 s reference clips plus the window), and the cap is
`REFINE_BUDGET_SEC=3600` per server run. 3600 / 8-15 is 4-7.5 minutes of talk with the page open;
then `refine: spending cap reached` and voice-ID lines are off until `server.py` is restarted
(and R3 applies to every reload after that). The rate is the code's own estimate, not a
measurement; the arithmetic is exact.

What to do today: restart `server.py` between judge groups, or set `REFINE_BUDGET_SEC` to the
expected total (and watch the ElevenLabs balance). Later: a per-minute rate limit that pauses and
resumes instead of a one-way stop.

## R5 [medium] Duplicate transcript lines in the web app after dropped speech

`refine.py:162` (quiet speakers' word spans added to `dropped_spans`), `refine.py:210-212`
(sound-tag-only turns added too), `server.py:1812-1819` (live lines whose commit time falls in a
dropped span are untracked; this runs before the same batch's `_publish_refined`).

The untracking is by time only. A live line committed while someone else laughed, or during a
stretch of quiet background words, is removed from `_live_unrefined` even when the same batch
publishes a voice-ID line for exactly that speech. That voice-ID line then has no `replaces`
entry for it, so the page keeps the live line (promoted after 12 s, neutral colour) next to the
VOICE-ID line with the same words. Both also stay in `_recent_captions` and in Gemini's
transcript log. In a busy hall with `REFINE_MIN_RMS` tuned up, most live lines sit near some
dropped word, so this happens often.

Fix: do not add sound-only turns to `dropped_spans` (`refine.py:210-212`), and in `server.py`
untrack a live line only when no turn published in this batch covers its commit time:

```python
            covered = [(s, e + REFINE_COMMIT_LAG) for _, s, e, _ in turns]
            kept = [(seg, t) for seg, t in _live_unrefined
                    if any(cs <= t <= ce for cs, ce in covered)
                    or not any(ds <= t <= de + REFINE_COMMIT_LAG for ds, de in _refiner.dropped_spans)]
```

## R6 [low] "Hey Jack" to a real Jack still buzzes, through Whisper

`server.py:1626-1628` removes `WAKE_EXTRA_NAMES` from the caption path with the comment '"hey
Jack" to a real Jack in the room shouldn't buzz', but `server.py:2152` (`wake_loop`) calls
`wake_phrase_detected(alternatives)` with the default `extra_names=True`. The buzz just arrives
1-2 s later from Whisper. `tests/test_wake.py::test_extra_names_switch_controls_plain_jack` documents
the switch; the decision itself lives in `wake_loop`, so no test can flag a fix yet.
Together with F1 (jake/jackie/jock by sound) this is one decision: either accept "jack" on the
Whisper path only while the caption session is down (a flag set in `connected()` and cleared when
`run()` returns), or remove `jack` from `WAKE_EXTRA_NAMES` for the demo.

## R7 [medium, needs several misses] The finished-piece backup wake can point at the previous speaker

`server.py:1634` (final branch: `angle = _live_people.angles.get(label)`), `live_captions.py:561`
(`on_text(..., info[0], True)`, the label from commit time), `live_captions.py:466-470` (a new
person is only added after a 0.8 s hold).

The partial-path wake was fixed in `f48118a` to use the current speech direction because the
speaker label lags a new talker. The final-path backup still uses the label. If a newcomer on the
right says only "Hey Jax!" (0.6 s, never held long enough to become a person), the piece is
labelled with the previous speaker, so the backup buzz points at the previous speaker on the left.
It only matters when both the live guess and Whisper missed the phrase, so it is rare; the fix is
to pass the matched words' time span to `on_text` and use `_direction_during([(s, e)])` in the
final branch.

## R8 [medium, confidence medium] A throttled commit knocks `pending` out of step for the rest of the session

`live_captions.py:323-331` (`request_commit` throttles on fed samples: `mark = audio.samples /
RATE`), `live_captions.py:441` (the commit flag rides on the next chunk dequeued, which may be
several chunks old), `live_captions.py:580-581` (an error reply is only printed),
`live_captions.py:356` (`next_commit_info` pops the oldest entry).

The 0.4 s minimum between commits is measured on audio fed by the mic, but Scribe measures audio it
received. When the uplink backlog grows between two commits (slow hotspot), Scribe can see less
than 0.3 s between them and answer `commit_throttled` with no transcript. The `pending` entry for
that commit is never popped, so every later finished piece takes the previous commit's speaker and
time marks until the next reconnect: short pieces get the wrong speaker label and angle in the
app, "I'm Sam" in such a piece names the wrong person. Whether Scribe sends a transcript for a
throttled commit could not be verified from the code; the team's own comment says it ignores them.

Fix: pop the newest `pending` entry on an error reply that mentions `commit_throttled` and set
`state["dirty"] = True` so the text is committed again; better, create the `pending` entry (and
measure the 0.4 s) in `send_audio` when the flag actually goes out.

## R9 [low] Partial-path wake direction can fall back to an unfiltered reading

`server.py:1641` (`_direction_during([(now - 1.0, now)])`), `server.py:782-784` (fallback to
`_doa_azimuth_deg`), `server.py:176-178` (that value is set for every sample; only speech readings
go to history). If the live guess arrives more than about 1 s after the phrase (uplink lag up to
the 1.9 s resync limit), the window holds no speech reading and the buzz follows the latest raw
reading, which jumps between beams when nobody speaks. Fix: when the window is empty, use the
speech readings in the second before the newest history entry instead of `_doa_azimuth_deg`.

## R10 [low] A slow catch-up from an earlier wake overwrites a newer arrow

`server.py:1550-1552` (wait until `wake_time + 2.5 s`), `server.py:1563`
(`show_caption_async` with no check for a newer wake); `display_cue.show_caption` takes the screen
unconditionally. Two wakes 5 s apart with a 7 s Scribe round trip (measured 0.6-2.4 s, timeout
30 s) show caller 1's text and arrow while the motors guide toward caller 2. Fix: a wake counter
captured in `_wake` and checked before drawing.

## R11 [low] "?" means both "unknown speaker" and "the first person"

`live_captions.py:330` and `:356` (`state["speaker"] or "?"`), `live_captions.py:412` (words with
no direction take `info[0]`), `live_captions.py:566-568` (`set_name(label, name)`),
`server.py:1668` and `:1674` (label lookups). `People.add` names the first person `"?"` as well.
After a reconnect `state["speaker"]` is None while `people` persists, so a piece spoken before any
direction reading (a quiet talker the chip's speech flag misses) is published with person 1's name
and angle, and a self-introduction in it renames person 1. Fix: a sentinel `People` never assigns
(for example `""`) for "unknown", with no lookups or `set_name` for it.

## R12 [low] A stale speaker-switch candidate skips the 0.4 s hold

`live_captions.py:457-461` (silence branch keeps `candidate`), `live_captions.py:466-470`
(unknown-direction branch keeps it too), `live_captions.py:478-481` (`elif time.monotonic() -
since >= SWITCH_HOLD: request_commit()`). A single reading toward B sets the candidate; after
seconds of silence, one more reading toward B switches speakers and commits immediately, splitting
A's sentence. Fix: `candidate = None` in both `continue` branches.

## R13 [low] With voice-ID off, Gemini summaries add never-captioned people to the web app

`frontend/static/app.js:90-91` (`if (S.refine && !S.people.has(id)) continue; color(id)...`),
`server.py:1945-1946` (`names` lists every direction label and every voice). When `S.refine` is
false (`REFINE_CAPTIONS=0`, or after the cap) every label in the summary becomes a person chip,
including a passer-by that `track_speakers` added but who never got a line. Fix: skip ids not in
`S.people` regardless of `S.refine`.

## R14 [low] Demo mode: ending and restarting within 1.4 s leaks a line from the old run

`frontend/static/app.js:265` (`setTimeout(() => S.demo && receive({type: 'refined', ...}), 1400)`),
`app.js:250` (`stopDemo` clears only the interval), `app.js:253` (restart resets the transcript and
sets `S.demo = true`). Fix: keep the timeout ids and clear them in `stopDemo()`, or tag each demo
run with a counter.

## R15 [info] `/state` on port 5000 is overwritten by every caption

`server.py:1453` (`broadcast` mirrors every payload), `server.py:216` (`flask_state` reads
`stage`, `message`, `transcript`). Caption and summary payloads have none of those fields, so
`/state` shows empty HUD fields while captions run. Nothing in the repo reads `/state` (both pages
use the WebSocket); part of the legacy cleanup in F4.

## R16 [check on the device] Motor pins GPIO4 and GPIO5 power up with internal pull-ups

`haptics.py:27-28` (`LEFT_GPIO = 4`, `RIGHT_GPIO = 5`), `motor_test.py:16` (same pins). On
Raspberry Pi boards GPIO0-8 come out of reset with the internal pull-up enabled and GPIO9-27 with
the pull-down (BCM2835 datasheet, GPIO pull-up/down defaults; the Pi 5's RP1 keeps the same
power-on defaults). The MOSFET trigger modules are driven straight from these pins. If a module
has no pull-down of its own on the gate, the pull-up (tens of kΩ) can hold the gate high from
power-on until `server.py` (or `motor_test.py`) claims the pin and drives it low: both temple
motors would buzz through boot and during any time no program owns the pins. If the modules do
have a gate pull-down, nothing happens. Not verified here (no hardware); it came from a review of
the sibling hardware notes and was checked against the datasheet default only.

The same floating gate matters at exit: when `server.py` stops, `haptics.stop()` drives both
pins low, but gpiozero then releases them as inputs with no pull (`lgpio` `SET_PULL_NONE`), so the
gate is left floating and a motor can stay on or start by itself until something drives the pin
again. A gate pull-down fixes both cases; without it, `pinctrl set 4,5 op dl` after a stop drives
the pins low from the shell.

Check once after a cold boot, before `server.py` starts: `pinctrl get 4-5` (shows `pu` for
pull-up) and feel whether the temples buzz. If they do: either move the motors to pull-down pins
(for example GPIO13 and GPIO19, pins 33 and 35; change `LEFT_GPIO`/`RIGHT_GPIO` and
`motor_test.py` in the same commit, then re-run `python haptics.py`), or fit a ~10 kΩ resistor
from each module's signal input to ground. Today: if the buzz at boot is the only symptom, start
`server.py` right after boot and leave the wiring alone.

## Suspicions the reviewers could not confirm

- `live_captions.py:367-372`: the choice between session start and the previous commit as the
  base for Scribe's word times is a heuristic; if Scribe's times are session-relative, the second
  piece of a session can pick the wrong base when the first commit landed within about 2 s of the
  start, shifting its words onto silence and dropping them as quiet.
- Two `committed_transcript` messages before one `committed_transcript_with_timestamps` would
  overwrite `state["info"]`; depends on Scribe's message ordering.
- Shutdown: `asyncio.run` waits for default-executor threads (a Scribe call up to 30 s, Gemini
  15 s) before `_stop_doa()` and `haptics.stop()` run, so Ctrl+C can take up to 30 s.
- `_refine_loop` keeps a stale `backoff` (up to 30 s) through the no-client branch, so the first
  request after a page opens can wait that long.
