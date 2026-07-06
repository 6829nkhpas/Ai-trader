"""Off-hot-path smoke test for the telemetry recording layer (telemetry.py, task 9.5).

Feature: session-telemetry

These are EXAMPLE-BASED smoke tests (not property tests) covering the
infrastructure guarantee the design excludes from PBT — that recording stays
strictly OFF the hot path:

    Assert ``observe_stream`` performs no SQLite I/O on the streaming coroutine and
    that a saturated queue drops rather than blocks.

Validates: Requirements 6.3 (writes happen off the hot path so recording never
stalls the run or the live event stream).

Design facts these tests pin to (see ``telemetry.observe_stream`` /
``telemetry.SessionWriter`` / ``telemetry.get_session_writer``):

* ``observe_stream`` is a passthrough tee: it re-yields every source SSE frame
  unchanged and in order, and only ever performs an O(1), non-blocking
  ``put_nowait`` of a lightweight ``Observation`` onto the ``SessionWriter``'s
  bounded queue. ALL SQLite work happens on the background drain thread, which an
  UNSTARTED ``SessionWriter`` never runs — so with an unstarted writer the queue
  simply accumulates observations and no DB connection is ever opened on the
  streaming coroutine.
* ``SessionWriter.enqueue`` uses ``put_nowait``: the first put onto a
  ``maxsize=1`` queue is accepted (returns ``True``); once full, every further put
  is DROPPED (returns ``False``) and never blocks or raises.

The sys.path / import pattern mirrors
``tests/test_telemetry_store_roundtrip_properties.py``. Async coroutines are
driven with ``asyncio.run`` from plain (synchronous) test functions.
"""

import asyncio
import json
import os
import sys

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import telemetry  # noqa: E402
from telemetry import (  # noqa: E402
    EVENT_DECISION,
    EVENT_REASONING,
    EVENT_RUN_STARTED,
    EVENT_TOOL_CALL_START,
    RunEntry,
    SessionWriter,
    WATCH_TOOL_NAME,
)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _sse(event_name: str, payload: dict) -> str:
    """Build one SSE frame in the ``format_sse`` shape the tee parses.

    Mirrors ``stream_events.format_sse``: ``"event: {name}\\ndata: {json}\\n\\n"``.
    """
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


def _sample_frames() -> list:
    """A small, representative sequence of SSE frames for one ``/run`` stream."""
    return [
        _sse(EVENT_RUN_STARTED, {"symbol": "RELIANCE", "timeframe": "15m", "mode": "FIND"}),
        _sse(EVENT_REASONING, {"text": "considering the setup"}),
        _sse(EVENT_TOOL_CALL_START, {"tool": WATCH_TOOL_NAME, "args": {"level": 100.0}}),
        _sse(EVENT_DECISION, {"action": "HOLD"}),
    ]


async def _async_source(frames):
    """Yield the given frames as an async iterator (a stand-in event_generator)."""
    for frame in frames:
        yield frame


async def _collect(agen):
    """Drain an async generator into a list, preserving order."""
    out = []
    async for frame in agen:
        out.append(frame)
    return out


# ── Test 1: no SQLite I/O on the streaming coroutine ──────────────────────────
def test_observe_stream_performs_no_sqlite_io_on_hot_path(monkeypatch):
    """The streaming coroutine opens no DB connection; frames still pass through.

    Drives ``observe_stream`` over a small async source of SSE frames with an
    UNSTARTED ``SessionWriter`` (no drain thread) wired in via ``get_session_writer``.
    Because nothing drains the queue, no SQLite work can occur on the streaming
    coroutine: ``sqlite3.connect`` / ``telemetry._connect`` must NOT be called, yet
    every frame is re-yielded unchanged and the writer's queue accumulates the
    observations (Requirement 6.3).
    """
    # Count every attempt to open a SQLite connection through either seam.
    connect_calls = {"sqlite3_connect": 0, "_connect": 0}

    def _counting_sqlite_connect(*args, **kwargs):
        connect_calls["sqlite3_connect"] += 1
        raise AssertionError(
            "sqlite3.connect must not be called on the streaming coroutine"
        )

    def _counting_connect(*args, **kwargs):
        connect_calls["_connect"] += 1
        raise AssertionError(
            "telemetry._connect must not be called on the streaming coroutine"
        )

    monkeypatch.setattr(telemetry.sqlite3, "connect", _counting_sqlite_connect)
    monkeypatch.setattr(telemetry, "_connect", _counting_connect)

    # An UNSTARTED writer: no daemon drain thread runs, so nothing reaches SQLite.
    writer = SessionWriter()
    assert writer._queue.qsize() == 0  # nothing buffered yet
    monkeypatch.setattr(telemetry, "get_session_writer", lambda: writer)

    frames = _sample_frames()
    entry = RunEntry("run", symbol="RELIANCE", timeframe="15m", mode="FIND")

    out = asyncio.run(_collect(telemetry.observe_stream("thread-hot-path", entry, _async_source(frames))))

    # 1. The tee is an exact passthrough: every frame, unchanged and in order.
    assert out == frames

    # 2. NO SQLite I/O happened on the streaming coroutine (DB writes only ever
    #    happen on the background drain thread, which an unstarted writer omits).
    assert connect_calls == {"sqlite3_connect": 0, "_connect": 0}

    # 3. Observations accumulated on the writer's queue instead of being persisted:
    #    a start marker + one per frame + an end marker.
    assert writer._queue.qsize() == len(frames) + 2


# ── Test 2: a saturated queue drops rather than blocks ────────────────────────
def test_saturated_queue_drops_and_stream_still_passes_through(monkeypatch):
    """A full queue drops observations (never blocks/raises); frames still flow.

    First exercises ``enqueue`` directly on a ``maxsize=1`` UNSTARTED writer: the
    first put is accepted and every subsequent put on the full queue is dropped
    (``False``) without blocking or raising. Then drives ``observe_stream`` with
    that saturated writer wired in and asserts the tee STILL yields ALL source
    frames in order — observations may be dropped, but source frames never are
    (Requirement 6.3).
    """
    # A bounded queue of size 1, no drain thread — so it stays full once filled.
    writer = SessionWriter(maxsize=1)

    # First enqueue is accepted; the queue is now full.
    assert writer.enqueue("obs-0") is True

    # Every subsequent enqueue on the full queue is DROPPED (False), never blocks
    # and never raises.
    for i in range(1, 50):
        assert writer.enqueue(f"obs-{i}") is False
    assert writer._queue.qsize() == 1  # still exactly the one accepted item

    # Now the saturated writer backs the tee. Observations will all be dropped,
    # but the stream must be unaffected.
    monkeypatch.setattr(telemetry, "get_session_writer", lambda: writer)

    frames = _sample_frames()
    entry = RunEntry("run", symbol="RELIANCE", timeframe="15m", mode="FIND")

    out = asyncio.run(
        _collect(telemetry.observe_stream("thread-saturated", entry, _async_source(frames)))
    )

    # The tee yielded every source frame, in order, despite the saturated queue.
    assert out == frames
    # The queue never grew past its bound: extra observations were dropped.
    assert writer._queue.qsize() == 1
