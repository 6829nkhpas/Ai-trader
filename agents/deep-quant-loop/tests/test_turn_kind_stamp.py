"""Every SSE frame must say WHICH KIND OF TURN produced it.

A Q&A answer is streamed on the analysis thread — that is how it stays grounded in the
analysis — and it arrives as ordinary ``REASONING`` frames. So on the wire an answer to
"why is the stop there?" is indistinguishable from the agent narrating its own scan.

A client cannot route what it cannot tell apart. Without ``turn`` it must append a Q&A reply
to the glass-box transcript, while rehydrating the same session from the stored ``qa_answer``
message rows shows that reply as a chat bubble: one conversation looks different live than it
does after a reload.

These tests pin the marker on the real endpoint bodies. The graph is stubbed because the
subject is FRAMING, not analysis — a real run needs an LLM key, live market data and minutes
of wall clock — but ``/run`` and ``/qa`` here are the real handlers, driving the real
``event_generator`` and the real ``_run_events``.
"""

import json
import os

# Set before importing `main`: it opens its databases at IMPORT time, so a fixture would run
# far too late and the real paths would be touched. Same reason conftest does this at module
# scope.
os.environ.setdefault("SESSIONS_DB_PATH", ":memory:")

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client(monkeypatch):
    """A TestClient whose graph is stubbed but whose endpoints are real.

    ``LLM_API_KEY`` is pinned rather than inherited. Importing ``main`` pulls the repo
    ``.env`` in through ``graph.py``, so the service silently starts in SHARED-KEY mode on a
    developer machine and PER-USER mode on CI — the same test would exercise different
    branches depending on whose checkout ran it.
    """
    monkeypatch.setenv("SKU_ENFORCE", "0")
    monkeypatch.setenv("LLM_API_KEY", "test-shared-key")
    monkeypatch.setattr(main, "resolve_openrouter_key", lambda user_id: "test-key")
    monkeypatch.setattr(main, "set_run_llm_credentials", lambda *a, **k: None)

    class StubMessage:
        content = "The stop sits below the 15m swing low."
        additional_kwargs: dict = {}

    class StubState:
        next = ()
        values = {"messages": [StubMessage()]}

    async def stub_astream(*args, **kwargs):
        # One node update, so the stream contains a middle frame and not just the
        # RUN_STARTED/RUN_FINISHED bookends.
        yield {"market_analyst": {"messages": [StubMessage()]}}

    # Patched on `main.graph_module.graph`, not `main.graph`: the durable checkpointer can
    # only be built inside a running loop, so the FastAPI lifespan rebinds that attribute at
    # startup and `main` reads it at every call site.
    monkeypatch.setattr(main.graph_module.graph, "astream", stub_astream)
    monkeypatch.setattr(main.graph_module.graph, "get_state", lambda config: StubState())
    return TestClient(main.app)


def frames(response):
    """Parse an SSE body back into ``(event, payload)`` pairs.

    Parsed back off the wire rather than read from an internal list, so what is asserted is
    what a browser would actually receive.
    """
    out = []
    event = None
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            payload = line[len("data:"):].strip()
            try:
                out.append((event, json.loads(payload)))
            except json.JSONDecodeError:
                out.append((event, {}))
    return out


def test_run_frames_are_marked_as_analysis(client):
    response = client.post(
        "/run",
        json={
            "thread_id": "t-turn-run",
            "message": "analyse NIFTY",
            "mode": "FIND",
            "symbol": "NIFTY",
            "timeframe": "15m",
            "profile": "INTRADAY",
            "user_id": "user-1",
        },
    )
    parsed = frames(response)

    assert parsed, "the run produced no frames at all"
    unmarked = [name for name, payload in parsed if payload.get("turn") != "run"]
    assert not unmarked, f"frames missing turn='run': {unmarked}"


def test_qa_frames_are_marked_as_a_question(client):
    response = client.post(
        "/qa",
        json={"thread_id": "t-turn-qa", "question": "why is the stop there?", "user_id": "user-1"},
    )
    parsed = frames(response)

    assert parsed, "the Q&A turn produced no frames at all"
    unmarked = [name for name, payload in parsed if payload.get("turn") != "qa"]
    assert not unmarked, f"frames missing turn='qa': {unmarked}"


def test_the_marker_does_not_displace_the_routing_key(client):
    """``turn`` says what KIND of turn; ``thread_id`` says WHICH session. Both, on every frame.

    ``thread_id`` is the client's only routing mechanism, so a regression that overwrote it
    while adding ``turn`` would send every frame to the unroutable counter.
    """
    response = client.post(
        "/qa",
        json={"thread_id": "t-turn-both", "question": "and the target?", "user_id": "user-1"},
    )
    parsed = frames(response)

    assert parsed
    for name, payload in parsed:
        assert payload.get("thread_id") == "t-turn-both", f"{name} lost its thread_id"
        assert payload.get("turn") == "qa", f"{name} lost its turn"


def test_a_caller_supplied_turn_is_not_overwritten(client, monkeypatch):
    """The stamp fills a gap; it does not clobber a value the assembler already set.

    Written as ``if "turn" not in payload`` so a future event type can carry its own more
    specific marker. Pinned because the obvious "simplification" to an unconditional
    ``{**payload, "turn": kind}`` would silently discard it.
    """
    original = main.build_run_started_event

    def tagged(thread_id, *args, **kwargs):
        return {**original(thread_id, *args, **kwargs), "turn": "custom"}

    monkeypatch.setattr(main, "build_run_started_event", tagged)

    response = client.post(
        "/qa",
        json={"thread_id": "t-turn-keep", "question": "why?", "user_id": "user-1"},
    )
    started = [payload for name, payload in frames(response) if name == "RUN_STARTED"]

    assert started, "no RUN_STARTED frame"
    assert started[0]["turn"] == "custom"


def test_an_error_frame_is_routable(client, monkeypatch):
    """A run that FAILS must still be able to reach the session that started it.

    ``build_error_event`` returns only ``{"error": ...}``. The stamp used to be applied inside
    the node-update loop, so error frames — emitted from the terminal branches, outside it —
    went out with no ``thread_id`` at all. A client that routes strictly by ``thread_id``
    (which the multi-session store does, deliberately, with no active-session fallback) had to
    discard them: the run would appear to hang forever instead of showing why it stopped.

    This is the case where dropping a frame is least acceptable, so it is asserted directly
    rather than left to the all-frames check above.
    """

    async def exploding_astream(*args, **kwargs):
        raise RuntimeError("LLM stream failed")
        yield {}  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(main.graph_module.graph, "astream", exploding_astream)

    response = client.post(
        "/run",
        json={
            "thread_id": "t-turn-error",
            "message": "analyse NIFTY",
            "mode": "FIND",
            "symbol": "NIFTY",
            "timeframe": "15m",
            "profile": "INTRADAY",
            "user_id": "user-1",
        },
    )
    errors = [payload for name, payload in frames(response) if name == "ERROR"]

    assert errors, "a failed run emitted no ERROR frame"
    assert errors[0]["thread_id"] == "t-turn-error"
    assert errors[0]["turn"] == "run"
    # R17.5/R5.5: the failure surfaces, and nothing fabricates a plan after it.
    assert "LLM stream failed" in errors[0]["error"]
