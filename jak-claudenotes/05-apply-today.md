# Apply today, or leave it? One line per finding

Rules for touching code on judging day:

1. Rehearse first with the code as it is (see `02-demo-runbook.md`). Only then change anything.
2. One change at a time, its own commit, followed by the two-minute check listed below. A bad
   change is then one `git revert` away.
3. Prefer the `.env` knob where one exists: no code, no restart surprises beyond the restart itself.
4. After any code change: `PY=.venv/bin/python jak-claudenotes/check.sh` on the Pi
   (`pip install pytest` once). The xfail tests for the fixed finding turn into failures that say
   "XPASS(strict)": remove the `xfail` marker on that test and run again.

| Id | Apply at the hackathon? | Why | Change | Two-minute check on the Pi |
|---|---|---|---|---|
| R1 wearer's "Hi, I'm Jax" wakes | **Yes** | the wearer will introduce the persona to judges; a false buzz toward themselves plus a catch-up of their own words looks broken | `WAKE_MAX_GAP=0` in `.env` (no code), or the 4-line patch below | wearer says "Hi, I'm Jax": no buzz, no `wake phrase` line in the terminal. Teammate says "Hello Jax": buzz + arrow |
| R2 OLED captions gone after a reconnect | **Yes** if the hotspot drops at all; otherwise know the workaround | the headset's main visible feature goes blank while everything else looks healthy | 3-line patch below in `live_captions.py` | with captions running, switch the hotspot off for 10 s, wait for `live captions: listening`, talk: words appear on the OLED within a second. Workaround without the patch: restart `server.py` |
| R4 voice-ID budget lasts 4-7 minutes of talk | **Yes** (config only) | judges arrive in groups over hours; after the cap the web app shows only live lines | `REFINE_BUDGET_SEC=0` in `.env` (no cap, watch the ElevenLabs balance) or a larger number; restart `server.py` between groups | no `refine: spending cap reached` line during a 10-minute conversation with the page open |
| R3 page reload after the cap sticks in voice-ID mode | Optional, 1 line | only bites after the cap and a reload; with R4 handled it should not happen | in `app.js`, set `S.refine = !!m.refine;` again right after `m.captions.forEach(receive);` (keep the one before it: on a fresh load with voice-ID on, the replayed live lines must stay faint) | two checks. Fresh load while people talk with voice-ID on: replayed lines faint, "No one heard yet" until a VOICE-ID line arrives. Then `REFINE_BUDGET_SEC=30`, talk with the page open until the cap line, reload, talk: new lines normal. Restore the budget |
| F1 + R6 "hey Jake" / "hey Jack" buzz through Whisper | Only if a Jake, Jack or Jackie will be near the table | rare otherwise; the ElevenLabs path already ignores them | 3-line patch below (keeps "jack" for Whisper's mishearing, drops jake/jackie), or remove `jack` from `WAKE_EXTRA_NAMES` (then Whisper alone misses "Hello Jax" when it hears "jack"; the caption path still catches it) | "hey Jake" from a teammate: nothing. "Hello Jax": buzz |
| R5 duplicate lines in the web app transcript | **No**, watch for it | cosmetic; the patch touches refine bookkeeping that has not run on hardware since `f48118a` | 6 lines in `01-findings.md` R5 | if the rehearsal shows the same sentence twice (faint + VOICE-ID), apply R5 and re-run a 5-minute conversation |
| R7 backup wake points at the previous speaker | No | needs the live guess and Whisper to both miss the phrase first | after the hackathon | |
| R8 `pending` desync after a throttled commit | No | needs a slow uplink and a specific message order; the fix changes the commit bookkeeping | after the hackathon; a reconnect clears it (restart `server.py` if speaker labels look consistently wrong) | |
| R9, R10, R11, R12 | No | rare timing paths, low impact | after the hackathon | |
| R13, R14, R15, F2, F4, F5, F6, F7, F8 | No | cosmetic, legacy or lint | after the hackathon | |
| R16 motors on pull-up pins GPIO4/5 | Check only | unverified without hardware; only matters if the temples buzz from power-on until `server.py` starts | none today; later move to pull-down pins or add gate pull-downs | cold boot with motors wired, no program running: temples silent; `pinctrl get 4-5` |
| F3 ElevenLabs balance via the dashboard | n/a | the key has no `user_read`, by design | check the dashboard before judging | |

## The patches, ready to paste

Validated offline against the current module with the test cases from `tests/test_wake.py`
plus "hey is Jax here" (no wake) and "Hello, jacks, we saved you a seat" (wake, Whisper's spelling).

### R1 + F1 together (`server.py`, replace `_is_wake_name` and the loop in `wake_phrase_detected`)

```python
# Words that mean the wearer is talking about themselves: "hi, I'm Jax" is not a call to Jax.
_SELF_WORDS = ("im", "i", "am", "its", "it", "is", "this", "me")


def _is_wake_name(word, targets, phonetic_targets):
    """Exact spelling for every target; same Metaphone code only for the real name and aliases."""
    if word in targets:
        return True
    try:
        import jellyfish
        code = jellyfish.metaphone(word)
        return any(code == jellyfish.metaphone(t) for t in phonetic_targets)
    except Exception:
        return False


def wake_phrase_detected(text_or_alts, extra_names=True):
    if not text_or_alts:
        return False
    alternatives = [text_or_alts] if isinstance(text_or_alts, str) else list(text_or_alts)
    phonetic = _name_targets()
    targets = phonetic + (list(WAKE_EXTRA_NAMES) if extra_names else [])
    for alt in alternatives:
        words = _WORD_RE.findall((alt or "").lower().replace("'", ""))
        for i, word in enumerate(words):
            if word in WAKE_GREETINGS or not _is_wake_name(word, targets, phonetic):
                continue
            before = words[max(0, i - 1 - WAKE_MAX_GAP):i]
            if before and before[-1] in _SELF_WORDS:
                continue
            if any(b in WAKE_GREETINGS for b in before):
                return True
    return False
```

If only R1 is wanted, keep the old `_is_wake_name` and add just the `_SELF_WORDS` check.

### R2 (`live_captions.py`)

```python
import itertools
_FRAME_SEQ = itertools.count(1)   # module level: frame numbers keep rising across reconnects
```

and in `redraw()` inside `run()`:

```python
        if shown:
            seq = next(_FRAME_SEQ)
            threading.Thread(target=display_cue.show_live,
                             args=(people.display(state["speaker"]) or "", shown, state["where"], seq),
                             daemon=True).start()
```

(`"seq": 0` in `state` can go.)

### R3 (`frontend/static/app.js`, snapshot branch of `receive`)

```js
    if (m.type === 'snapshot') {
      if (m.session_id && S.session && m.session_id !== S.session) resetTranscript();
      if (m.session_id) S.session = m.session_id;
      S.refine = !!m.refine;   // before the replay: replayed live lines stay faint when voice-ID is on
      Array.isArray(m.captions) && m.captions.forEach(receive);
      S.refine = !!m.refine;   // and after it: a replayed voice-ID line must not switch the mode back on
      return render();
    }
```
