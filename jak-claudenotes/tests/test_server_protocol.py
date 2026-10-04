"""server.py WebSocket contract for the web app: snapshot, hello/refine opt-in, caption and voice-ID lines."""

import asyncio
import json

import server


class FakeWs:
    """Yields the given messages, then stays open until release() is called (like a live page)."""

    def __init__(self, incoming=()):
        self.sent, self._incoming = [], list(incoming)
        self.closed = asyncio.Event()

    async def send(self, msg):
        self.sent.append(json.loads(msg))

    def release(self):
        self.closed.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._incoming:
            return self._incoming.pop(0)
        await self.closed.wait()
        raise StopAsyncIteration


def test_client_gets_snapshot_and_only_hello_refine_clients_pay_for_voice_id():
    async def run():
        ws = FakeWs(["not json", json.dumps({"type": "hello", "refine": True})])
        seen = {}

        async def spy():
            for _ in range(200):        # let handle_client send the snapshot and read the hello
                await asyncio.sleep(0)
                if ws in server.REFINE_CLIENTS:
                    break
            seen["during"] = ws in server.REFINE_CLIENTS and ws in server.CLIENTS
            ws.release()

        await asyncio.gather(server.handle_client(ws), spy())
        return ws, seen

    ws, seen = asyncio.run(run())
    assert ws.sent[0]["type"] == "snapshot"
    assert ws.sent[0]["session_id"] == server._SESSION_ID
    assert ws.sent[0]["refine"] is False and ws.sent[0]["captions"] == []
    assert seen["during"] is True
    assert ws not in server.CLIENTS and ws not in server.REFINE_CLIENTS   # cleaned up on close

    plain = FakeWs([json.dumps({"type": "hello"})])
    plain.release()
    asyncio.run(server.handle_client(plain))
    assert plain not in server.REFINE_CLIENTS


def test_caption_and_refined_lines_replace_live_ones_and_feed_gemini_log(monkeypatch):
    import refine

    monkeypatch.setattr(server, "_live_people", None)
    monkeypatch.setattr(server, "_recent_captions", type(server._recent_captions)(maxlen=200))
    monkeypatch.setattr(server, "_live_unrefined", type(server._live_unrefined)(maxlen=200))
    monkeypatch.setattr(server, "_transcript_log", type(server._transcript_log)(maxlen=200))
    refiner = refine.Refiner(server.SAMPLE_RATE)
    v1 = refiner._new_voice()
    monkeypatch.setattr(server, "_refiner", refiner)

    async def run():
        server._refine_wake = asyncio.Event()
        ws = FakeWs()
        server.CLIENTS.add(ws)
        try:
            server._publish_caption("1-0", "?", "hello jax we saved", False)   # live guess
            server._publish_caption("1-0", "?", "hello jax we saved you a seat", True)
            server._publish_caption("1-1", "?", "", True)                     # all chatter: clears only
            assert server._refine_wake.is_set()
            t_end = server._live_unrefined[0][1]
            server._publish_refined("V1", t_end - 2.0, t_end - 0.5, "Hello Jax, we saved you a seat.", None)
            await asyncio.sleep(0.05)   # broadcasts run as tasks
        finally:
            server.CLIENTS.discard(ws)
        return ws

    ws = asyncio.run(run())
    kinds = [(m["type"], m.get("is_final")) for m in ws.sent]
    assert kinds == [("caption", False), ("caption", True), ("caption", True), ("refined", True)]
    refined = ws.sent[-1]
    assert refined["speaker_label"] == "Speaker 1" and refined["replaces"] == ["live-1-0"]
    assert refined["segment_id"].startswith("voice-") and refined["source"] == "scribe_v2"
    # The replaced live line is gone from the snapshot and Gemini's log; the voice line remains.
    assert [c["segment_id"] for c in server._recent_captions] == [refined["segment_id"]]
    assert [line[0] for line in server._transcript_log] == [refined["segment_id"]]
    assert server._live_unrefined == type(server._live_unrefined)()
    assert v1.name is None   # no self-introduction in that line


def test_gemini_prompt_lists_labels_with_directions(monkeypatch):
    import live_captions

    people = live_captions.People()
    people.add(270.0)
    people.set_name("?", "Sam")
    monkeypatch.setattr(server, "_live_people", people)
    monkeypatch.setattr(server, "_refiner", None)
    prompt = server._gemini_prompt([("live-1", "?", "hello jax", 270.0), ("live-2", "??", "hi", None)])
    assert "[?/SAM, left] hello jax" in prompt
    assert "[??, unknown direction] hi" in prompt
    assert prompt.rstrip().endswith("hi")
    assert '"names"' in prompt and '"summary"' in prompt
