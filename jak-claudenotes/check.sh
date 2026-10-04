#!/usr/bin/env bash
# Offline checks for the Usher headset code on any machine (no Pi, mic, OLED or motors needed).
#
#   jak-claudenotes/check.sh            # static checks + offline tests
#   LIVE=1 jak-claudenotes/check.sh     # also the live API tests (needs GEMINI_API_KEY,
#                                       #   ELEVENLABS_API_KEY and USHER_TEST_WAV in the environment)
#
# First time: python3 -m venv .venv-vps && .venv-vps/bin/pip install numpy requests websockets \
#             flask flask-cors jellyfish pillow pytest ruff
# (see 04-dev-env.md). Run from anywhere; paths are resolved from this script.
set -u
cd "$(dirname "$0")/.."
PY="${PY:-.venv-vps/bin/python}"
[ -x "$PY" ] || { echo "no venv at $PY (see jak-claudenotes/04-dev-env.md)"; exit 2; }
rc=0

echo "== ruff (pyflakes + syntax errors) on the team code: report only"
"$PY" -m ruff check --select F,E9 --output-format concise \
  server.py live_captions.py refine.py scribe.py display_cue.py haptics.py doa_reader.py \
  doa_calibrate.py camera_server.py motor_test.py scribe_test.py refine_test.py || true

echo "== byte-compile every Python file"
"$PY" -m compileall -q . >/dev/null && echo "ok" || rc=1

if command -v node >/dev/null; then
  echo "== JavaScript syntax"
  for f in frontend/static/app.js static/usher.js; do node --check "$f" && echo "ok $f" || rc=1; done
fi

echo "== offline tests"
"$PY" -m pytest -q jak-claudenotes/tests --ignore=jak-claudenotes/tests/live -p no:cacheprovider || rc=1

if [ "${LIVE:-0}" = "1" ]; then
  echo "== live API tests (billed: ~11 s of audio to batch Scribe, ~15 s to Realtime, one Gemini request)"
  "$PY" -m pytest -q -s jak-claudenotes/tests/live -p no:cacheprovider || rc=1
fi

[ "$rc" = 0 ] && echo "ALL GREEN" || echo "FAILURES (rc=$rc)"
exit $rc
