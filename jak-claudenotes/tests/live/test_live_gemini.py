"""Live check (costs one small request): the production Gemini names+summary call parses as expected.

Run with GEMINI_API_KEY in the environment; skipped otherwise.
"""

import os

import pytest

import live_captions
import server

pytestmark = pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set")

LINES = [
    ("l1", "?", "Hey, are you coming to the cafe after the workshop?", 70.0),
    ("l2", "??", "Yeah, give me a minute to pack up.", 300.0),
    ("l3", "?", "Hello Jax, we saved you a seat over here. Hi, I'm Sam.", 70.0),
    ("l4", "??", "Of course, the more the merrier. This is Oliver, by the way.", 300.0),
]


def test_gemini_returns_names_and_summary_json(monkeypatch):
    people = live_captions.People()
    people.add(70.0)
    people.add(300.0)
    monkeypatch.setattr(server, "_live_people", people)
    monkeypatch.setattr(server, "_refiner", None)
    result = server._ask_gemini(server._gemini_prompt(LINES))
    print("gemini:", result)
    assert isinstance(result, dict)
    names, summary = result.get("names"), result.get("summary")
    assert isinstance(names, dict) and isinstance(summary, list)
    assert 2 <= len(summary) <= 4 and all(isinstance(b, str) for b in summary)
    assert names.get("?", "").lower() == "sam"
    assert all(n.lower() not in ("jax", "jack", "jacks") for n in names.values())
