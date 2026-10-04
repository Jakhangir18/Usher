# jak-claudenotes

Independent check of the Usher headset code at commit `f48118a` (Sunday 2026-10-04, 03:00-04:30
EDT), done off the Pi while Oliver tests on the device. Nothing in the team's files was changed;
every finding comes with a patch and, where it was possible offline, a failing test.

| File | What it is for |
|---|---|
| [`01-findings.md`](01-findings.md) | Ranked findings with file:line, scenario, patch, and the list of things checked and found correct |
| [`02-demo-runbook.md`](02-demo-runbook.md) | Pre-demo checklist, start order, terminal lines to look for, failure/recovery table, privacy answer |
| [`03-improvements.md`](03-improvements.md) | What to change after the hackathon, in order of value |
| [`04-dev-env.md`](04-dev-env.md) | Running the checks on any laptop; what is stubbed and why |
| [`check.sh`](check.sh) | One command: ruff, byte-compile, JS syntax, offline tests; `LIVE=1` adds the API tests |
| [`tests/`](tests/) | 144 offline checks, 3 live API checks, 1 heavy Whisper check |
| [`tools/make_test_wav.py`](tools/make_test_wav.py) | Makes the two-voice test clip with Gemini TTS |

## Results at a glance

| Check | Result |
|---|---|
| Offline tests (wake rule, angles, DOA lookups, catch-up helpers, people/names, haptics, display layout, WebSocket contract, refine) | 144 passed, 4 expected failures documenting finding F1 |
| ruff (pyflakes) on the team code | 1 nit (`server.py:368`, f-string without placeholders) |
| Byte-compile, `node --check` on both JS files | clean |
| Gemini key + `gemini-3.5-flash-lite` | valid; names + summary JSON parsed through the production call |
| ElevenLabs key | valid for speech-to-text; no `user_read`, so the balance is dashboard-only |
| Batch Scribe on an 11 s two-voice clip | 0.7-0.9 s, two speakers separated turn by turn, "Jax" spelled right, "I'm Sam" learned |
| Scribe Realtime through `live_captions.run()` | session, partial then final updates, caption wake fired on the live guess "Hello, Jax." |
| Whisper tiny.en on the same clip | "Hello, jacks, we saved you a seat" wakes; the window without the phrase does not |
| Git history | no API key ever committed |
| Two independent model reviewers (Opus) | see the G-section of `01-findings.md` |

## Key hygiene

Both API keys were pasted into a chat on 2026-10-04. They are not in the repository, but treat
them as exposed: rotate them after the hackathon. Keep them in the Pi's `.env` only; on a dev
machine pass them as environment variables (the code reads the environment first).

## Run the checks

```bash
python3 -m venv .venv-vps && .venv-vps/bin/pip install numpy requests websockets flask flask-cors jellyfish pillow pytest ruff
jak-claudenotes/check.sh
```
