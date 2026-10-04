"""
Voice-identified transcript for the web app (its "ideal version").

After each live caption finishes, server.py sends batch Scribe (diarize) the audio since the last
line it sent, and the app swaps the faint live text for these lines once the speaker's turn ends.
Speakers are told apart by voice, so the wearer turning their head doesn't create new people (the
live captions tell people apart by direction, which does).

Batch diarization numbers speakers per request (speaker_0 in one call can be speaker_1 in the
next), so each request starts with a short reference clip of every voice heard so far: whichever
speaker the diarizer puts on V2's reference is V2. Words that overlap the previous request's words
vote too, which covers voices that don't have a reference yet.

Keeping the number of voices honest (first hardware test: 4 people became 16 voices):
- every known voice gets a reference clip in every request (not just the most recent few);
- a new voice needs MIN_NEW_SEC of clear speech; quiet speakers (distant chatter) can't create one;
- at most max_voices voices: past that, an unmatched speaker joins the voice from the nearest
  direction. A short unmatched bit joins a voice within NEAR_DEG, or the turn around it.

Pure logic (no audio device, no network) so refine_test.py can check it anywhere.
"""

import math

import numpy as np

import scribe

GAP_SEC = 0.6        # silence between the reference clips and the live window
REF_SEC = 2.5        # reference clip length per voice (all voices get one in every request)
REF_MIN_SEC = 1.5    # a turn must be at least this long to become a voice's reference
HOLD_SEC = 0.6       # a turn still running this close to the end of the audio waits for the next pass
MIN_VOTE_SEC = 0.3   # overlap needed to tie a diarized speaker to a known voice
MIN_NEW_SEC = 1.5    # clear speech needed before an unmatched speaker becomes a new voice
NEAR_DEG = 45        # a short unmatched bit joins a known voice whose direction is this close
PAUSE_SPLIT_SEC = 1.0  # a pause this long starts a new line, even for the same voice
MAX_HOLD_SEC = 8.0   # a turn held longer than this sends its finished words (long monologues)


def _overlap(s1, e1, s2, e2):
    return max(0.0, min(e1, e2) - max(s1, s2))


def _angle_diff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _blend(old, new, weight=0.3):
    """Move angle `old` toward `new` on the circle (a running direction per voice)."""
    if old is None:
        return new
    x = (1 - weight) * math.cos(math.radians(old)) + weight * math.cos(math.radians(new))
    y = (1 - weight) * math.sin(math.radians(old)) + weight * math.sin(math.radians(new))
    return math.degrees(math.atan2(y, x)) % 360


class Voice:
    def __init__(self, vid, number):
        self.id = vid          # "V1", "V2", ... (stable for the whole session)
        self.number = number
        self.name = None       # from a self-introduction or Gemini
        self.ref = None        # reference audio (float32) sent at the start of each request
        self.last_heard = 0.0  # clock time of their latest word
        self.angle = None      # usual direction (head-relative, drifts): only a tie-breaker

    @property
    def label(self):
        return self.name or f"Speaker {self.number}"


class Refiner:
    def __init__(self, rate=16000, max_voices=6):
        self.rate = rate
        self.max_voices = max(1, max_voices)
        self.voices = {}        # "V1" -> Voice
        self.prev = []          # [(start, end, vid)] labelled words of the last request (clock time)
        self.done_until = 0.0   # clock time of the last word already handled (sent or dropped)
        self.dropped = 0        # quiet turns/speakers dropped by the last process() call

    def _new_voice(self):
        number = len(self.voices) + 1
        voice = Voice(f"V{number}", number)
        self.voices[voice.id] = voice
        return voice

    def _nearest(self, angle, exclude=()):
        """(vid, degrees) of the known voice whose usual direction is closest to `angle`, or None."""
        best = None
        for v in self.voices.values():
            if v.id in exclude or v.angle is None or angle is None:
                continue
            d = _angle_diff(v.angle, angle)
            if best is None or d < best[1]:
                best = (v.id, d)
        return best

    def build_clip(self, window):
        """
        The audio to send: a reference clip of every known voice (most recently heard first), then
        the live window. Returns (audio, ref_spans [(vid, start, end) in clip seconds], window_offset_sec).
        """
        gap = np.zeros(int(GAP_SEC * self.rate), dtype=np.float32)
        refs = sorted((v for v in self.voices.values() if v.ref is not None),
                      key=lambda v: -v.last_heard)[:self.max_voices]
        parts, spans, t = [], [], 0.0
        for v in refs:
            parts += [v.ref, gap]
            spans.append((v.id, t, t + len(v.ref) / self.rate))
            t += (len(v.ref) + len(gap)) / self.rate
        parts.append(np.asarray(window, dtype=np.float32))
        return np.concatenate(parts), spans, t

    def process(self, result, ref_spans, offset, window, window_start, min_rms=0.0, angle_of=None):
        """
        Map a diarized Scribe result onto stable voices.

        window_start: clock time of the window's first sample (offset seconds into the clip).
        min_rms: speakers and turns quieter than this (RMS of `window`) are dropped as distant chatter.
        angle_of(spans): direction (degrees) heard during [(start, end), ...] clock spans, or None.
        Returns (turns, pending_start): turns = [(vid, start, end, text)] finished since the last
        call, in clock time; pending_start = clock time of the first word held back (a turn still
        running at the end of the audio), or None.
        """
        window_end = window_start + len(window) / self.rate
        self.dropped = 0
        votes, live, speakers, last = {}, [], [], None
        for w in result.get("words") or []:
            kind = w.get("type")
            if kind not in ("word", "audio_event") or w.get("start") is None:
                continue
            sp = w.get("speaker_id") or last  # a sound tag without a speaker belongs to whoever was talking
            if sp is None:
                continue
            last = sp
            s = float(w["start"])
            e = max(s, float(w.get("end") or s))
            tally = votes.setdefault(sp, {})
            if s < offset - GAP_SEC / 2:  # inside the reference clips
                for vid, rs, re_ in ref_spans:
                    tally[vid] = tally.get(vid, 0.0) + _overlap(s, e, rs, re_)
                continue
            cs, ce = window_start + (s - offset), window_start + (e - offset)
            for ps, pe, vid in self.prev:
                tally[vid] = tally.get(vid, 0.0) + _overlap(cs, ce, ps, pe)
            text = (w.get("text") or "").strip()
            is_word = kind == "word"
            live.append([sp, cs, ce, text if is_word else f"[{text.strip('()[]')}]", is_word])
            if sp not in speakers:
                speakers.append(sp)

        # A speaker quiet across all their words is distant chatter: drop them before they can
        # become a voice (the live captions apply their own loudness rule on the headset).
        if min_rms:
            quiet = {sp for sp in speakers
                     if self._rms(window, window_start, [(w[1], w[2]) for w in live if w[0] == sp]) < min_rms}
            if quiet:
                self.dropped += len(quiet)
                live = [w for w in live if w[0] not in quiet]
                speakers = [sp for sp in speakers if sp not in quiet]
        if not live:
            return [], None  # nothing heard: keep self.prev so the next request can still vote

        mapping = self._assign(votes, speakers, live, angle_of)
        for word in live:
            word[0] = mapping[word[0]]
        self._fill_gaps(live, angle_of)
        live = [w for w in live if w[0] is not None]  # only sound tags, nobody to give them to
        if not live:
            return [], None

        self.prev = [(s, e, vid) for vid, s, e, *_ in live]
        for vid, s, e, *_ in live:
            self.voices[vid].last_heard = max(self.voices[vid].last_heard, e)

        # Group the words not yet sent into turns (a long pause starts a new one).
        fresh = [w for w in live if (w[1] + w[2]) / 2 > self.done_until + 0.05]
        turns = []  # [vid, start, end, [words]]
        for w in fresh:
            if turns and turns[-1][0] == w[0] and w[1] - turns[-1][2] < PAUSE_SPLIT_SEC:
                turns[-1][2] = w[2]
                turns[-1][3].append(w)
            else:
                turns.append([w[0], w[1], w[2], [w]])

        # Hold the last turn while it's still going (sound tags don't count as still talking);
        # a long monologue sends its finished words so it can't slide out of the window unsent.
        pending = None
        if turns:
            vid, s, e, words = turns[-1]
            spoken_end = max((w[2] for w in words if w[4]), default=s)
            if spoken_end > window_end - HOLD_SEC:
                cut = window_end - HOLD_SEC
                done = [w for w in words if w[2] <= cut]
                rest = [w for w in words if w[2] > cut]  # not empty: spoken_end > cut
                if e - s > MAX_HOLD_SEC and done:
                    turns[-1] = [vid, s, max(w[2] for w in done), done]
                    pending = rest[0][1]
                else:
                    turns.pop()
                    pending = s

        out = []
        for vid, s, e, words in turns:
            self.done_until = max(self.done_until, e)
            if not any(w[4] for w in words):
                continue  # only sound tags, e.g. "(background noise)"
            if min_rms and self._rms(window, window_start, [(s, e)]) < min_rms:
                self.dropped += 1
                continue
            if angle_of:
                angle = angle_of([(w[1], w[2]) for w in words if w[4]])
                if angle is not None:
                    self.voices[vid].angle = _blend(self.voices[vid].angle, angle)
            self._maybe_set_ref(vid, s, e, window, window_start)
            text = " ".join(scribe.clean_words([w[3] for w in words]))
            if text:
                out.append((vid, s, e, text))
        return out, pending

    def _rms(self, window, window_start, spans):
        """RMS of `window` over clock spans (inf if under 0.1 s: too short to judge, keep it)."""
        parts = []
        for start, end in spans:
            i = max(0, int((start - window_start) * self.rate))
            j = min(len(window), int((end - window_start) * self.rate))
            if j > i:
                parts.append(np.asarray(window[i:j], dtype=np.float32))
        if not parts or sum(len(p) for p in parts) < self.rate // 10:
            return float("inf")
        audio = np.concatenate(parts)
        return float(np.sqrt(np.mean(audio ** 2)))

    def _assign(self, votes, speakers, live, angle_of):
        """Diarized speaker -> stable voice id (None for a short unmatched bit: _fill_gaps merges it)."""
        pairs = sorted(((sec, sp, vid) for sp, tally in votes.items() for vid, sec in tally.items()
                        if sec >= MIN_VOTE_SEC and sp in speakers), reverse=True)
        mapping, used = {}, set()
        for _, sp, vid in pairs:
            if sp not in mapping and vid not in used:  # the diarizer said these are different people
                mapping[sp], used = vid, used | {vid}
        talk = {}
        for sp, s, e, _, is_word in live:
            if is_word:  # a speaker with only sound tags isn't a new person
                talk[sp] = talk.get(sp, 0.0) + (e - s)
        for sp in speakers:  # in order of first appearance
            if sp in mapping:
                continue
            angle = angle_of([(w[1], w[2]) for w in live if w[0] == sp and w[4]]) if angle_of else None
            full = len(self.voices) >= self.max_voices
            near = self._nearest(angle, exclude=used) or (self._nearest(angle) if full else None)
            if talk.get(sp, 0.0) >= MIN_NEW_SEC and not full:
                voice = self._new_voice()
                voice.angle = angle
                mapping[sp] = voice.id
            elif near and (full or near[1] <= NEAR_DEG):
                mapping[sp] = near[0]
            else:
                mapping[sp] = None
            if mapping[sp]:
                used.add(mapping[sp])
        return mapping

    def _fill_gaps(self, live, angle_of=None):
        """Words of a short unmatched speaker (often a mis-split) join the turn around them."""
        known = None
        for word in live:
            if word[0] is None:
                word[0] = known
            known = word[0]
        known = None
        for word in reversed(live):
            if word[0] is None:
                word[0] = known
            known = word[0]
        if live and live[0][0] is None and any(w[4] for w in live):  # nothing to merge into
            if len(self.voices) < self.max_voices:
                vid = self._new_voice().id
            else:  # full: the voice from the nearest direction, else whoever spoke last
                angle = angle_of([(w[1], w[2]) for w in live if w[4]]) if angle_of else None
                near = self._nearest(angle)
                vid = near[0] if near else max(self.voices.values(), key=lambda v: v.last_heard).id
            for word in live:
                word[0] = vid

    def _maybe_set_ref(self, vid, start, end, window, window_start):
        """Keep the longest clean turn (up to REF_SEC) as this voice's reference clip."""
        voice = self.voices[vid]
        length = min(end - start, REF_SEC)
        if length < REF_MIN_SEC or (voice.ref is not None and len(voice.ref) / self.rate >= length):
            return
        i = int((start - window_start) * self.rate)
        j = i + int(length * self.rate)
        if i >= 0 and j <= len(window):
            voice.ref = np.asarray(window[i:j], dtype=np.float32).copy()
