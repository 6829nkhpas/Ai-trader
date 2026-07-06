"""Property-based test for swallowed recording failures (telemetry.py, task 9.4).

Feature: session-telemetry

This module implements design **Property 21: Recording failures are swallowed
and never break the stream**:

    For any injected failure in the observation or writer logic, observe_stream
    still yields every source frame and no exception escapes into the caller, and
    the recording entry points return normally (log-only) rather than raising.

Validates: Requirements 6.2.

The sys.path / import pattern mirrors
``tests/test_telemetry_store_roundtrip_properties.py`` and the other
deep-quant-loop property tests.

``observe_stream`` is an async passthrough tee: for each source SSE frame it
re-yields the frame FIRST and only THEN performs its (guarded) observation work.
This test injects failures into every point on the observation/writer path and
proves the tee still yields every source frame, in order, unchanged, and that no
telemetry exception escapes into the caller:

* the shared writer's ``enqueue`` raises on every call;
* ``get_session_writer`` itself raises (no writer available);
* the frame parser ``_parse_sse_frame`` raises on every frame;
* the ``Observation`` marker/frame constructors raise;
* combinations of the above.

The failures are injected by monkeypatching the telemetry module internals
directly inside each Hypothesis example (saving and restoring the originals in a
``finally`` block) so no pytest function-scoped fixture is shared across examples.

It also checks the writer's own producer-side guarantee directly (Requirement
6.2 / 6.3): ``SessionWriter.enqueue`` on a saturated bounded queue returns
``False`` (drop-on-full) and NEVER raises.
"""

import asyncio
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import telemetry  # noqa: E402
from telemetry import (  # noqa: E402
    ENTRY_KIND_RESUME,
    ENTRY_KIND_RUN,
    RunEntry,
    SessionWriter,
    observe_stream,
)


# ── Source-frame generators ───────────────────────────────────────────────────
# The live SSE stream is a sequence of string frames. We mix well-formed SSE
# frames (an ``event:`` line + a JSON ``data:`` line, the shape
# ``stream_events.format_sse`` produces) with arbitrary text so the tee is
# exercised on both parseable and unparseable frames. The passthrough contract is
# "yield exactly what the source yielded", so the frames' content is irrelevant to
# the assertion — only their identity and order matter.

_WELL_FORMED_FRAMES = [
    'event: RUN_STARTED\ndata: {"ts": 1.0}\n\n',
    'event: REASONING\ndata: {"text": "thinking"}\n\n',
    'event: TOOL_CALL_START\ndata: {"tool": "watch_price_condition"}\n\n',
    'event: TOOL_CALL_START\ndata: {"tool": "get_ohlcv"}\n\n',
    'event: DECISION\ndata: {"action": "BUY"}\n\n',
    'event: ERROR\ndata: {"message": "boom"}\n\n',
    'event: RUN_FINISHED\ndata: {"status": "paused"}\n\n',
]

_frame = st.one_of(
    st.sampled_from(_WELL_FORMED_FRAMES),
    st.text(max_size=40),                       # arbitrary / malformed frames
    st.just(""),                                # empty frame
    st.just("event: DECISION"),                 # event line, no data
    st.just("data: not-json\n\n"),              # data line, not JSON
)

_frames = st.lists(_frame, max_size=20)

_entries = st.one_of(
    st.builds(
        RunEntry,
        kind=st.just(ENTRY_KIND_RUN),
        symbol=st.one_of(st.none(), st.sampled_from(["RELIANCE", "TCS"])),
        timeframe=st.one_of(st.none(), st.sampled_from(["5m", "15m"])),
        mode=st.one_of(st.none(), st.sampled_from(["FIND", "MANAGE"])),
    ),
    st.builds(
        RunEntry,
        kind=st.just(ENTRY_KIND_RESUME),
        symbol=st.one_of(st.none(), st.sampled_from(["RELIANCE", "TCS"])),
        timeframe=st.one_of(st.none(), st.sampled_from(["5m", "15m"])),
        mode=st.one_of(st.none(), st.sampled_from(["FIND", "MANAGE"])),
        trigger_kind=st.one_of(st.none(), st.sampled_from(["target", "invalidation"])),
    ),
)

# The distinct failure-injection scenarios (see module docstring).
_SCENARIOS = [
    "writer_enqueue_raises",
    "get_writer_raises",
    "parse_raises",
    "observation_raises",
    "writer_none",
    "parse_and_enqueue_raise",
]
_scenarios = st.sampled_from(_SCENARIOS)


class _RaisingWriter:
    """A stand-in writer whose ``enqueue`` / ``put`` always raise (Requirement 6.2)."""

    def enqueue(self, observation):
        raise RuntimeError("injected enqueue failure")

    put = enqueue


class _RaisingObservation:
    """A stand-in ``Observation`` whose every constructor raises (Requirement 6.2)."""

    @staticmethod
    def start(*args, **kwargs):
        raise RuntimeError("injected Observation.start failure")

    @staticmethod
    def frame(*args, **kwargs):
        raise RuntimeError("injected Observation.frame failure")

    @staticmethod
    def end(*args, **kwargs):
        raise RuntimeError("injected Observation.end failure")


def _install_failure(scenario):
    """Monkeypatch the telemetry module for ``scenario``; return an undo callable.

    All patches are applied to the ``telemetry`` module globals that
    ``observe_stream`` resolves at call time, so the injected failure is exercised
    by the real ``observe_stream`` code path. The returned callable restores every
    original attribute.
    """
    orig_get_writer = telemetry.get_session_writer
    orig_parse = telemetry._parse_sse_frame
    orig_observation = telemetry.Observation

    def undo():
        telemetry.get_session_writer = orig_get_writer
        telemetry._parse_sse_frame = orig_parse
        telemetry.Observation = orig_observation

    def _raise_get_writer():
        raise RuntimeError("injected get_session_writer failure")

    def _raise_parse(_frame):
        raise RuntimeError("injected _parse_sse_frame failure")

    if scenario == "writer_enqueue_raises":
        telemetry.get_session_writer = lambda: _RaisingWriter()
    elif scenario == "get_writer_raises":
        telemetry.get_session_writer = _raise_get_writer
    elif scenario == "parse_raises":
        telemetry.get_session_writer = lambda: _RaisingWriter()
        telemetry._parse_sse_frame = _raise_parse
    elif scenario == "observation_raises":
        telemetry.get_session_writer = lambda: _RaisingWriter()
        telemetry.Observation = _RaisingObservation
    elif scenario == "writer_none":
        telemetry.get_session_writer = lambda: None
    elif scenario == "parse_and_enqueue_raise":
        telemetry.get_session_writer = lambda: _RaisingWriter()
        telemetry._parse_sse_frame = _raise_parse
        telemetry.Observation = _RaisingObservation

    return undo


def _drive(entry, frames):
    """Run ``observe_stream`` over ``frames`` and collect what it yields.

    Uses ``asyncio.run`` over a fresh async source generator (the safest way to
    drive an async generator from a synchronous property test). Any exception that
    escapes ``observe_stream`` propagates out of this helper and fails the test.
    """

    async def _source():
        for frame in frames:
            yield frame

    collected = []

    async def _run():
        async for out in observe_stream("thread-1", entry, _source()):
            collected.append(out)

    asyncio.run(_run())
    return collected


# ─────────────────────────────────────────────────────────────────────────────
# Property 21 (task 9.4): Recording failures are swallowed and never break the stream
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 21: Recording failures are swallowed and never break the stream
@settings(max_examples=100, deadline=None)
@given(entry=_entries, frames=_frames, scenario=_scenarios)
def test_property_21_recording_failures_are_swallowed(entry, frames, scenario):
    """Feature: session-telemetry, Property 21: Recording failures are swallowed
    and never break the stream — for any injected failure on the observation /
    writer path, ``observe_stream`` still yields EVERY source frame, in order and
    unchanged, and NO telemetry exception escapes into the caller.

    Validates: Requirements 6.2
    """
    undo = _install_failure(scenario)
    try:
        # If any injected telemetry failure leaked, _drive would raise here and the
        # test would fail — that is exactly the property under test.
        collected = _drive(entry, frames)
    finally:
        undo()

    # ── The tee is an exact passthrough despite the injected failure ────────────
    # Same frames, same order, none added, removed, reordered, or altered — the
    # live stream is byte-for-byte identical (Requirement 6.2 keeps 6.1 intact).
    assert collected == frames
    assert len(collected) == len(frames)
    for observed, source in zip(collected, frames):
        assert observed is source


# Feature: session-telemetry, Property 21: Recording failures are swallowed and never break the stream
@settings(max_examples=100, deadline=None)
@given(observations=st.lists(st.integers(), min_size=2, max_size=30))
def test_property_21_saturated_writer_queue_drops_without_raising(observations):
    """Feature: session-telemetry, Property 21: Recording failures are swallowed
    and never break the stream — the writer's producer-side guarantee: enqueuing
    onto a saturated bounded queue DROPS (returns ``False``) rather than blocking
    or raising, so a full queue can never break the caller (Requirement 6.2, 6.3).

    Validates: Requirements 6.2
    """
    # An UNSTARTED writer with a maxsize-1 queue: nothing drains it, so it fills
    # after exactly one accepted observation and every subsequent enqueue drops.
    writer = SessionWriter(maxsize=1)

    results = [writer.enqueue(obs) for obs in observations]

    # The first enqueue is accepted; every subsequent enqueue onto the now-full
    # queue is dropped. Crucially, NONE of them raised (we got here).
    assert results[0] is True
    assert all(r is False for r in results[1:])
    # Bounded: at most one observation was ever accepted (drop-on-full holds).
    assert results.count(True) == 1
