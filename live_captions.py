"""
Live captions on the OLED with ElevenLabs Scribe v2 Realtime (WebSocket, ~150 ms).

    python live_captions.py

Streams the reSpeaker's mic channel to Scribe and shows the words as they come in.
Realtime Scribe has no speaker labels, so the mic array tells people apart by direction:
each direction is a person ("?", "??", "???" in order of appearance). When the talker's
direction changes, or speech stops, we tell Scribe to finalise ("commit"). Each finished piece
then comes back with word timestamps; word directions can override the commit-time speaker for
3+ words in a row. Runs of quiet words (below CAPTION_MIN_RMS) are dropped as background
chatter; fillers ("uh", "um") and stutters are removed. On screen:

    yellow band:  arrow + who is talking now
    blue area:    the words (option B: no speaker tags; LIVE_SPEAKER_TAGS=1 adds them)

server.py runs this same engine (run()) alongside "Hello Jax" when LIVE_CAPTIONS=1.
This file on its own is for testing captions alone: stop server.py first, since only one
program can use the mic and the display. Ctrl+C to stop. Needs ELEVENLABS_API_KEY in .env.
"""

import asyncio
import base64
import collections
import json
import math
import os
import threading
import time
import traceback
import urllib.parse

import sounddevice as sd
import websockets

import display_cue
import doa_reader
import scribe
from scribe import load_env

HERE = os.path.dirname(os.path.abspath(__file__))
load_env(os.path.join(HERE, ".env"))  # before the settings below are read (standalone runs too)
RATE = 16000
CHUNK = 1600              # 100 ms of audio per message
DISPLAY_EVERY = 0.2       # redraw at most 5 times a second
KEEP_SEGMENTS = 4         # finished pieces kept on screen above the live one

DIRECTION_WINDOW = 0.6    # seconds of speech readings averaged for "who is talking now"
SAME_PERSON_DEG = 50      # directions closer than this are the same person
SWITCH_HOLD = 0.4         # a new direction must hold this long before it counts as a switch
NEW_PERSON_HOLD = 0.8     # an unknown direction must hold this long before it becomes a new person
QUIET_RUN_WORDS = 2       # only drop quiet words in runs this long (short words like "to" are naturally quiet)
SILENCE_COMMIT = 0.7      # finalise the text after this long without speech
MAX_PIECE_SEC = 6.0       # finalise anyway after this long (loud halls can keep the speech flag on)
QUEUE_DROP = 100          # chunks (~6 s) queued while disconnected before new audio is dropped
QUEUE_BEHIND = 30         # chunks (~2-3 s) backlog while connected = uplink too slow: resync

# Words quieter than this (RMS, 0-1) are treated as background chatter and dropped. People near
# the wearer measured ~0.04-0.13 in testing, the hall's background ~0.01. Tune with the
# "heard:" lines in the terminal (dropped words shown in red with their level).
CAPTION_MIN_RMS = float(os.environ.get("CAPTION_MIN_RMS", "0.02"))

RED, DIM, RESET = "\033[31m", "\033[2m", "\033[0m"   # terminal colours for the log
WORD_PAD = 0.15           # seconds either side of a word when looking up its direction/loudness
MIN_OVERRIDE_WORDS = 3    # word directions only re-split a piece for this many words in a row

# Option B (default): no speaker tags in the scrolling text, only "who is talking now" in the
# yellow band. Direction-based tags for past text were too often wrong; a wrong label misleads
# more than no label. Accurate who-said-what lives in the catch-up (batch Scribe voice ID).
# Set LIVE_SPEAKER_TAGS=1 to bring the inverted tags back.
LIVE_SPEAKER_TAGS = os.environ.get("LIVE_SPEAKER_TAGS", "0").strip() == "1"

# Live captions read the chip's processed beam (ch0): in testing it kept nearby voices >= 0.034
# with background below 0.02, while the raw mic (ch2) put the second speaker under the cut-off.
# (server.py's "Hello Jax" Whisper path stays on AUDIO_CHANNEL, tested on ch2.)
CAPTION_AUDIO_CHANNEL = int(os.environ.get("CAPTION_AUDIO_CHANNEL", "0"))


def logical_angle(raw):
    """Same flip + offset as server.py."""
    a = raw % 360.0
    if os.environ.get("DOA_FLIP_LEFT_RIGHT", "1").strip().lower() in ("1", "true", "yes", "on"):
        a = (360.0 - a) % 360.0
    return (a - float(os.environ.get("DOA_OFFSET_DEG", "184"))) % 360.0


def angle_gap(a, b):
    return abs((a - b + 180) % 360 - 180)


def circular_mean(angles):
    x = sum(math.cos(math.radians(a)) for a in angles)
    y = sum(math.sin(math.radians(a)) for a in angles)
    return math.degrees(math.atan2(y, x)) % 360


class Direction:
    """Polls the mic's direction in a thread; keeps angles taken while the chip heard speech."""

    def __init__(self):
        xvf = os.environ.get("XVF_HOST", os.path.expanduser(
            "~/Documents/reSpeaker_XVF3800_USB_4MIC_ARRAY/host_control/rpi_64bit/xvf_host"))
        self.reader = doa_reader.open_doa(os.environ.get("DOA_SOURCE", "auto"), xvf)
        self.history = collections.deque(maxlen=1200)  # ~60 s of speech readings
        self.last_speech = 0.0
        self.lock = threading.Lock()
        threading.Thread(target=self._poll, daemon=True).start()

    def _poll(self):
        while True:
            try:
                sample = self.reader.read()
                if sample and sample[1]:
                    now = time.monotonic()
                    with self.lock:
                        self.history.append((now, logical_angle(sample[0])))
                        self.last_speech = now
            except Exception as exc:
                print(f"WARNING direction: {exc!r}")
                time.sleep(1.0)
            time.sleep(0.05)

    def current(self):
        """Mean angle of the last DIRECTION_WINDOW seconds of speech, or None if nobody is talking."""
        cutoff = time.monotonic() - DIRECTION_WINDOW
        with self.lock:
            angles = [a for t, a in self.history if t >= cutoff]
        return circular_mean(angles) if angles else None

    def silent_for(self):
        with self.lock:
            return time.monotonic() - self.last_speech

    def angles_between(self, start, end):
        """Speech angles read between two monotonic times."""
        with self.lock:
            return [a for t, a in self.history if start <= t <= end]


class People:
    """Each direction is a person: '?', '??', '???' in order of appearance.

    Only track_speakers() (0.6 s averages, new direction held NEW_PERSON_HOLD) may add people;
    per-word lookups only pick among existing ones, so jumpy single readings can't invent people.
    """

    def __init__(self):
        self.angles = {}   # label -> running mean angle

    def nearest(self, angle):
        """Existing person within SAME_PERSON_DEG of `angle`, or None."""
        best = min(self.angles, key=lambda k: angle_gap(self.angles[k], angle), default=None)
        if best is not None and angle_gap(self.angles[best], angle) <= SAME_PERSON_DEG:
            return best
        return None

    def update(self, label, angle):
        # Drift slowly with the person (heads and speakers move a bit).
        self.angles[label] = circular_mean([self.angles[label]] * 4 + [angle])

    def add(self, angle):
        label = "?" * (len(self.angles) + 1)
        self.angles[label] = angle
        return label


class CaptionAudio:
    """
    Feed 16 kHz int16 mono chunks in from an audio callback (any thread) with feed().
    Keeps the audio clock and loudness that word timestamps are matched against:
    sample n of what we send was captured at t0 + n / RATE (monotonic). Scribe's word
    timestamps are in seconds of that same audio, so each word maps to the moment it was spoken.
    """

    def __init__(self, loop):
        self.loop = loop
        self.queue = asyncio.Queue()
        self.t0 = None
        self.samples = 0
        self.levels = collections.deque(maxlen=int(60 * RATE / 320))  # (monotonic time, RMS) per 20 ms

    def feed(self, mono_int16):
        frames = len(mono_int16)
        if self.t0 is None:
            self.t0 = time.monotonic() - frames / RATE
        for i in range(0, frames - 319, 320):
            block = mono_int16[i:i + 320].astype("float32") / 32768.0
            t = self.t0 + (self.samples + i + 320) / RATE
            self.levels.append((t, float(math.sqrt(float((block * block).mean())))))
        self.samples += frames
        # Bounded: offline (or a slow uplink) must not grow memory forever. Old audio is
        # discarded on reconnect anyway.
        if self.queue.qsize() < QUEUE_DROP:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, mono_int16.tobytes())


async def run(key, audio, direction, people, on_ready=None):
    """
    One Scribe Realtime session: stream `audio` (CaptionAudio), show captions on the display.
    `direction` needs current(), silent_for(), angles_between(); `people` is a People.
    Returns/raises when the connection ends; callers decide whether to reconnect.
    """
    query = urllib.parse.urlencode({
        "model_id": "scribe_v2_realtime",
        "audio_format": "pcm_16000",
        "language_code": "en",
        "commit_strategy": "manual",  # we commit on speaker switches and pauses (see track_speakers)
        "keyterms": "Jax",            # bias towards the user's name
        "include_timestamps": "true", # word times, so each word gets its own speaker and loudness
    })
    url = f"wss://api.elevenlabs.io/v1/speech-to-text/realtime?{query}"

    try:
        ws = await websockets.connect(url, additional_headers={"xi-api-key": key})
    except TypeError:  # websockets < 14
        ws = await websockets.connect(url, extra_headers={"xi-api-key": key})

    # Drop audio queued while we weren't connected (e.g. during a reconnect).
    while not audio.queue.empty():
        audio.queue.get_nowait()

    state = {
        "speaker": None,       # label of who is talking now
        "where": None,         # their arrow direction
        "partial": "",         # Scribe's live guess for the current piece
        "commit_next": False,  # ask Scribe to finalise with the next audio chunk
        "pending": collections.deque(),  # (speaker, audio sec at previous commit, audio sec at this commit)
        "last_mark": audio.samples / RATE,  # audio seconds at the previous commit (session start)
        "session_start": audio.samples / RATE,
        "info": None,          # pending entry for the commit currently being received
        "last_final_text": None,
        "provisional": False,  # last segment is a not-yet-re-split committed_transcript
        "dirty": False,        # new partial text since the last commit we asked for
        "piece_start": 0.0,    # audio seconds when the current piece's first words arrived
        "seq": 0,              # display frame number, so late frames don't overwrite newer ones
        "last_draw": 0.0,
    }
    segments = collections.deque(maxlen=KEEP_SEGMENTS)  # finished (label, text)

    def redraw(force=False):
        now = time.monotonic()
        if not force and now - state["last_draw"] < DISPLAY_EVERY:
            return
        state["last_draw"] = now
        # Only show the live guess while someone nearby is loud enough; otherwise it's probably
        # background chatter (finished pieces are filtered word by word in attribute()).
        partial = scribe.clean_text(state["partial"]) if recent_level() >= CAPTION_MIN_RMS else ""
        live = [(state["speaker"] or "?", partial)] if partial else []
        shown = list(segments) + live
        if not LIVE_SPEAKER_TAGS:
            # Option B (chosen): just the words; the yellow band shows who is talking now.
            # Past text carries no speaker tags, so nothing can be wrongly attributed.
            shown = [(None, text) for _, text in shown]
        if shown:
            state["seq"] += 1
            threading.Thread(target=display_cue.show_live,
                             args=(state["speaker"] or "", shown, state["where"], state["seq"]),
                             daemon=True).start()

    def recent_level(seconds=0.6):
        """Loudest 20 ms RMS in the last `seconds` of audio."""
        cutoff = time.monotonic() - seconds
        return max((r for t, r in list(audio.levels)[-100:] if t >= cutoff), default=0.0)

    def request_commit():
        mark = audio.samples / RATE
        # Only if there's new text since the last commit we asked for (a slow reply must not
        # trigger a second, empty commit that knocks `pending` out of step), and Scribe ignores
        # commits with < 0.3 s of new audio ("commit_throttled").
        if state["dirty"] and not state["commit_next"] and mark - state["last_mark"] >= 0.4:
            state["commit_next"] = True
            state["dirty"] = False
            state["pending"].append((state["speaker"] or "?", state["last_mark"], mark))
            state["last_mark"] = mark

    def heard_line(heard, kept_levels):
        """Everything Scribe heard, in order, with dropped (too quiet) parts marked in red in place."""
        if not any(not kept for kept, _, _ in heard):
            return None  # nothing dropped: the caption lines say it all
        parts, group = [], []

        def flush():
            if group:
                words = " ".join(w for w, _ in group)
                parts.append(f"{RED}[✗ {words} {max(l for _, l in group):.3f}]{RESET}")
                group.clear()

        for kept, word, level in heard:
            if kept:
                flush()
                parts.append(word)
            else:
                group.append((word, level))
        flush()
        quietest = f"{min(kept_levels):.3f}" if kept_levels else "-"
        return f"     heard: {' '.join(parts)}   {DIM}(quietest kept {quietest}, cut-off {CAPTION_MIN_RMS}){RESET}"

    def next_commit_info():
        return state["pending"].popleft() if state["pending"] else (state["speaker"] or "?", 0.0, None)

    def attribute(words, info):
        """Split a committed piece into (speaker, text) runs by each word's own direction; drop quiet words."""
        _, mark_start, mark_end = info
        timed = [w for w in words
                 if w.get("type", "word") == "word" and w.get("start") is not None and w.get("end") is not None]
        if not timed or audio.t0 is None:
            return None, None
        # Word times count from the start of this session's audio, or from the previous commit.
        # Pick whichever puts the last word nearest the moment we asked for this commit.
        base = state["session_start"]
        offset = base
        if mark_end is not None:
            last_end = timed[-1]["end"]
            if abs(last_end - (mark_end - mark_start)) < abs(last_end - (mark_end - base)):
                offset = mark_start
        runs, labelled = [], []   # labelled: [label, word] per kept word
        kept_levels = []
        heard = []   # everything in order: (kept?, word, level), for the "heard:" log line

        # Pass 1: drop fillers/stutters, measure each word's loudness and direction.
        entries, previous = [], None
        with_levels = list(audio.levels)
        for w in timed:
            n = scribe._norm(w["text"])
            if not n or scribe.is_filler(w["text"]) or n == previous:
                continue  # "uh", "um", and stutters ("I, I, I") make captions hard to read
            previous = n
            s = audio.t0 + offset + w["start"]
            e = audio.t0 + offset + w["end"]
            level = max((r for t, r in with_levels if s - 0.05 <= t <= e + 0.05), default=0.0)
            entries.append({"text": w["text"], "s": s, "e": e, "level": level,
                            "quiet": level < CAPTION_MIN_RMS})

        # Pass 2: a lone quiet word between louder ones is part of the sentence ("to", "it"),
        # so only runs of QUIET_RUN_WORDS+ quiet words count as background.
        i = 0
        while i < len(entries):
            j = i
            while j < len(entries) and entries[j]["quiet"] == entries[i]["quiet"]:
                j += 1
            if entries[i]["quiet"] and j - i < QUIET_RUN_WORDS and len(entries) > j - i:
                for k in range(i, j):
                    entries[k]["quiet"] = False
            i = j

        # Pass 3: speaker per kept word, chosen only among people we already know.
        for entry in entries:
            heard.append((not entry["quiet"], entry["text"], entry["level"]))
            if entry["quiet"]:
                continue
            kept_levels.append(entry["level"])
            angles = direction.angles_between(entry["s"] - WORD_PAD, entry["e"] + WORD_PAD)
            label = people.nearest(circular_mean(angles)) if angles else None
            if label is None:
                label = labelled[-1][0] if labelled else info[0]
            labelled.append([label, entry["text"]])

        # The speaker at the moment of the commit is the main rule (it tested "mostly right").
        # Word-level directions can be a fraction of a second off (audio buffering), so they only
        # override it for a stretch of at least MIN_OVERRIDE_WORDS words in a row.
        i = 0
        while i < len(labelled):
            j = i
            while j < len(labelled) and labelled[j][0] == labelled[i][0]:
                j += 1
            if labelled[i][0] != info[0] and j - i < MIN_OVERRIDE_WORDS:
                for k in range(i, j):
                    labelled[k][0] = info[0]
            i = j

        for label, text in labelled:
            if runs and runs[-1][0] == label:
                runs[-1][1].append(text)
            else:
                runs.append([label, [text]])
        return [(label, " ".join(ws)) for label, ws in runs], heard_line(heard, kept_levels)

    async def send_audio():
        while True:
            if audio.queue.qsize() > QUEUE_BEHIND:
                # Uplink can't keep up; captions would fall further and further behind.
                raise RuntimeError("caption uplink behind, resyncing")
            chunk = await audio.queue.get()
            commit, state["commit_next"] = state["commit_next"], False
            await ws.send(json.dumps({
                "message_type": "input_audio_chunk",
                "audio_base_64": base64.b64encode(chunk).decode("ascii"),
                "commit": commit,
                "sample_rate": RATE,
            }))

    async def track_speakers():
        candidate, since = None, 0.0
        new_since = None   # when an unknown direction first appeared
        while True:
            await asyncio.sleep(0.1)
            if state["dirty"] and audio.samples / RATE - state["piece_start"] >= MAX_PIECE_SEC:
                request_commit()  # don't let one piece grow forever in a loud room
            angle = direction.current()
            if angle is None:
                new_since = None
                if direction.silent_for() >= SILENCE_COMMIT:
                    request_commit()  # pause: finalise what was said
                continue
            label = people.nearest(angle)
            if label is None:
                # Unknown direction: only a new person once it has held NEW_PERSON_HOLD.
                now = time.monotonic()
                if new_since is None:
                    new_since = now
                if now - new_since < NEW_PERSON_HOLD:
                    continue
                label, new_since = people.add(angle), None
            else:
                new_since = None
                people.update(label, angle)
            if state["speaker"] is None:
                state["speaker"], state["where"] = label, display_cue.direction(angle)
                redraw(force=True)
            elif label != state["speaker"]:
                if label != candidate:
                    candidate, since = label, time.monotonic()
                elif time.monotonic() - since >= SWITCH_HOLD:
                    request_commit()  # the old speaker's words end here
                    state["speaker"], state["where"] = label, display_cue.direction(angle)
                    candidate = None
                    redraw(force=True)
            else:
                candidate = None
                state["where"] = display_cue.direction(angle)

    async def guarded(coro):
        # If a helper dies, close the socket: that ends the receive loop below, so the caller's
        # reconnect loop takes over instead of captions silently stalling.
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"    live captions: {exc}")
            await ws.close()

    tasks = [asyncio.create_task(guarded(send_audio())), asyncio.create_task(guarded(track_speakers()))]
    try:
        async for raw in ws:
            msg = json.loads(raw)
            kind = msg.get("message_type")
            if kind == "session_started":
                print("    live captions: listening")
                if on_ready:
                    on_ready()
            elif kind == "partial_transcript":
                state["partial"] = msg.get("text", "")
                if state["partial"] and not state["dirty"]:
                    state["piece_start"] = audio.samples / RATE  # first words of a new piece
                state["dirty"] = bool(state["partial"])
                redraw()
            elif kind == "committed_transcript":
                # Provisional: labelled with whoever was talking at the commit. Replaced by the
                # word-by-word split when the with-timestamps version arrives.
                text = msg.get("text", "").strip()
                if text and text != state["last_final_text"]:
                    state["info"] = next_commit_info()  # kept for the with-timestamps version
                    segments.append((state["info"][0], scribe.clean_text(text)))
                    state["provisional"] = True
                state["partial"] = ""
                redraw(force=True)
            elif kind == "committed_transcript_with_timestamps":
                text = msg.get("text", "").strip()
                info, state["info"] = state["info"] or next_commit_info(), None
                try:
                    # "words" can be null (e.g. an empty/silent piece).
                    runs, heard = attribute(msg.get("words") or [], info)
                except Exception:
                    traceback.print_exc()  # never let one message kill the captions
                    runs, heard = None, None
                if runs is None:
                    continue  # no usable timestamps: keep the provisional version
                if state["provisional"] and segments:
                    segments.pop()
                state["provisional"] = False
                state["last_final_text"] = text
                for label, run in runs:
                    segments.append((label, run))
                    print(f"    {label:<4} {run}")
                if heard:
                    print(heard)
                state["partial"] = ""
                redraw(force=True)
            elif "error" in msg or (kind or "").endswith("error"):
                print(f"    Scribe error: {msg}")
    finally:
        for t in tasks:
            t.cancel()
        await ws.close()


async def main():
    """Standalone: own mic stream + direction reader. (server.py runs run() alongside "Hello Jax".)"""
    load_env(os.path.join(HERE, ".env"))
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set ELEVENLABS_API_KEY in .env first.")

    direction = Direction()
    people = People()
    audio = CaptionAudio(asyncio.get_running_loop())
    channels = int(os.environ.get("AUDIO_INPUT_CHANNELS", "6"))
    channel = int(os.environ.get("CAPTION_AUDIO_CHANNEL", str(CAPTION_AUDIO_CHANNEL)))

    def on_audio(indata, frames, time_info, status):
        if status:
            print(f"audio stream: {status}")
        audio.feed(indata[:, channel].copy())

    stream = sd.InputStream(samplerate=RATE, channels=channels, dtype="int16", blocksize=CHUNK,
                            device=os.environ.get("AUDIO_DEVICE", "XVF3800"), callback=on_audio,
                            latency="high")

    print("Connecting to Scribe Realtime... talk once it says 'listening'. Ctrl+C to stop.")
    with stream:
        await run(key, audio, direction, people)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
