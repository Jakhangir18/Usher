"""live_captions.CaptionAudio: the audio clock and loudness trail that word timestamps map onto."""

import asyncio
import time

import numpy as np

import live_captions


def test_feed_keeps_clock_levels_and_a_bounded_queue():
    async def run():
        audio = live_captions.CaptionAudio(asyncio.get_running_loop())
        chunk = (np.ones(1600, dtype=np.int16) * 3000)          # 100 ms, loud
        t = time.monotonic()
        audio.feed(chunk)
        await asyncio.sleep(0)
        assert audio.samples == 1600
        assert abs(audio.t0 + 1600 / live_captions.RATE - t) < 0.05   # sample clock anchored to now
        assert len(audio.levels) == 5                                   # one RMS per 20 ms
        assert all(abs(r - 3000 / 32768) < 1e-3 for _, r in audio.levels)
        assert audio.queue.qsize() == 1
        # Offline (nobody reads the queue): bounded, no growth. The bound is read from the loop's
        # queue, so the loop must get to run between chunks, as it does with a 64 ms mic callback.
        for _ in range(live_captions.QUEUE_DROP + 20):
            audio.feed(np.zeros(1600, dtype=np.int16))
            await asyncio.sleep(0)
        assert audio.queue.qsize() == live_captions.QUEUE_DROP
        # A gap in the audio (mic reopened) re-anchors the clock instead of lagging forever.
        audio.t0 -= 5.0
        audio.feed(chunk)
        assert abs(audio.t0 + audio.samples / live_captions.RATE - time.monotonic()) < 0.05

    asyncio.run(run())
