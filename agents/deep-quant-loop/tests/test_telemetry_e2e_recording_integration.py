"""End-to-end recording integration test for Session Telemetry (telemetry.py, task 10.2).

Feature: session-telemetry

This is an EXAMPLE-BASED integration test (the design's Testing Strategy covers the
recording layer's wiring with integration tests rather than PBT). It drives a
synthetic ``/run`` + ``/resume`` SSE sequence through the real ``observe_stream``
passthrough tee and the real background ``SessionWriter``, then — after the writer
drains — loads the persisted Session_Record from a fresh temp store and asserts it
matches the expected funnel counters and terminal outcome.

It exercises the full recording path end-to-end:

  * ``observe_stream`` re-yields every source frame unchanged (passthrough), and
    enqueues an ``Observation`` per frame plus the entry-boundary markers onto the
    background writer (Requirement 2.5, 6.1).
  * ``SessionWriter`` folds each entry's frames via ``interpret_events`` into a
    per-``thread_id`` ``SessionState`` — opening a Session on the ``/run`` and
    FOLDING the ``/resume`` into the SAME Session keyed by ``thread_id``
    (Requirement 1.1, 1.2) — updating funnel counters and cost proxies
    (Requirement 2.1, 2.2), and on the terminal ``DECISION`` classifying the
    outcome and stamping ``ended_at`` (Requirement 1.3).
  * ``save`` / ``load_sessions`` persist and reload the folded record.

Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2.

Determinism: the writer drains on a background daemon thread, so the test uses
``writer.flush()`` (which blocks until the bounded queue is fully drained) before
loading, then ``writer.stop()``. The single writer thread processes observations
in FIFO order, so the ``/run`` entry is folded (persisted as an OPEN record) before
the ``/resume`` entry folds into the same Session and closes it — the final
persisted record therefore reflects the terminal HOLD with the folded counters
(``save`` is an ``INSERT OR REPLACE`` UPSERT on the stable
``session_id = f"{thread_id}:{started_at}"``, so ``load_sessions`` returns exactly
one record for the thread).

The sys.path / import pattern mirrors the other deep-quant-loop telemetry tests
(e.g. ``tests/test_telemetry_passthrough_tee_properties.py``).
"""

import asyncio
import json
import os
import sys
from unittest import mock

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import telemetry  # noqa: E402
from telemetry import (  # noqa: E402
    EVENT_DECISION,
    EVENT_REASONING,
    EVENT_RUN_FINISHED,
    EVENT_RUN_STARTED,
    EVENT_TOOL_CALL_START,
    OUTCOME_HOLD,
    WATCH_TOOL_NAME,
    RunEntry,
    SessionWriter,
    TelemetryConfig,
    load_sessions,
    observe_stream,
    save,
)


def _sse(event_name, payload):
    """Build one SSE frame in the ``event: NAME\\ndata: {json}\\n\\n`` shape.

    Mirrors ``stream_events.format_sse`` so the frames the tee observes parse
    exactly as the live stream's frames do.
    """
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


async def _async_source(frames):
    """An async source generator yielding the given SSE frames in order."""
    for frame in frames:
        yield frame


async def _drive(thread_id, entry, frames):
    """Drive one stream through ``observe_stream`` and collect the yielded frames."""
    collected = []
    async for frame in observe_stream(thread_id, entry, _async_source(frames)):
        collected.append(frame)
    return collected


def test_e2e_run_resume_folds_into_one_session_with_terminal_hold(tmp_path):
    """A synthetic /run + /resume SSE sequence records ONE folded Session ending HOLD.

    Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2
    """
    # ── Fresh, isolated store pointing at a temp DB (never the module default). ──
    db_path = os.path.join(str(tmp_path), "telemetry_e2e.db")
    config = TelemetryConfig(
        db_path=db_path,
        weak_prior_min_sessions=20,
        incomplete_horizon_seconds=float(24 * 3600),
    )

    thread_id = "e2e-thread-1"
    symbol, timeframe, mode = "RELIANCE", "15m", "FIND"

    # ── The synthetic /run SSE frame sequence (RUN_STARTED, REASONING x3, a
    #    watch_price_condition TOOL_CALL_START, RUN_FINISHED paused). ────────────
    run_reasoning_count = 3
    run_frames = [_sse(EVENT_RUN_STARTED, {"symbol": symbol, "timeframe": timeframe})]
    run_frames += [
        _sse(EVENT_REASONING, {"text": f"run-reasoning-{i}"}) for i in range(run_reasoning_count)
    ]
    run_frames += [
        _sse(EVENT_TOOL_CALL_START, {"tool": WATCH_TOOL_NAME, "args": {"level": 100}}),
        _sse(EVENT_RUN_FINISHED, {"status": "paused"}),
    ]

    # ── The synthetic /resume SSE frame sequence for the SAME thread_id
    #    (REASONING, a terminal DECISION action HOLD, RUN_FINISHED completed). ────
    resume_reasoning_count = 1
    resume_frames = [
        _sse(EVENT_REASONING, {"text": "resume-reasoning-0"}),
        _sse(EVENT_DECISION, {"action": "HOLD"}),
        _sse(EVENT_RUN_FINISHED, {"status": "completed"}),
    ]

    total_reasoning = run_reasoning_count + resume_reasoning_count

    run_entry = RunEntry(kind="run", symbol=symbol, timeframe=timeframe, mode=mode)
    resume_entry = RunEntry(
        kind="resume",
        symbol=symbol,
        timeframe=timeframe,
        mode=mode,
        trigger_kind="invalidation",
    )

    # ── Dedicated writer bound to the temp store; NOT the module singleton. Patch
    #    ``get_session_writer`` so ``observe_stream`` enqueues onto THIS writer. ──
    writer = SessionWriter(cfg=config, save_fn=save).start()
    try:
        with mock.patch.object(telemetry, "get_session_writer", return_value=writer):
            # Passthrough assertion: the tee re-yields every source frame unchanged.
            run_out = asyncio.run(_drive(thread_id, run_entry, run_frames))
            assert run_out == run_frames

            resume_out = asyncio.run(_drive(thread_id, resume_entry, resume_frames))
            assert resume_out == resume_frames

        # Block until the background thread has drained every observation, then stop.
        assert writer.flush(timeout=5.0) is True
    finally:
        writer.stop(timeout=5.0)

    # ── Load the persisted record(s) from the store and assert the folded shape. ─
    loaded = load_sessions(config)

    # R1.1 / R1.2: the /run and its /resume fold into EXACTLY ONE Session keyed by
    # thread_id (the resume did not open a second Session).
    records_for_thread = [r for r in loaded if r.thread_id == thread_id]
    assert len(records_for_thread) == 1
    record = records_for_thread[0]

    # Identity captured from the opening /run (R1.1).
    assert record.thread_id == thread_id
    assert record.symbol == symbol
    assert record.timeframe == timeframe
    assert record.mode == mode

    # R2.2: exactly one resume, tagged as an invalidation.
    assert record.resume_count == 1
    assert record.invalidation_events == 1
    assert record.target_events == 0

    # R2.1: one watch_price_condition registration => one Watch_Cycle (and a
    # corresponding per-tool cost proxy).
    assert record.watch_cycles == 1
    assert record.tool_calls_by_name.get(WATCH_TOOL_NAME) == 1

    # Reasoning turns accumulate across BOTH the /run and the /resume fragments.
    assert record.reasoning_turns == total_reasoning

    # R1.3: the terminal DECISION (HOLD) closes the Session — outcome hold, a
    # hold sub-reason recorded, and a stamped end timestamp with a consistent,
    # non-negative time-to-decision.
    assert record.outcome == OUTCOME_HOLD
    assert record.hold_reason is not None
    assert record.ended_at is not None
    assert record.ended_at >= record.started_at
    assert record.time_to_decision_s is not None
    assert record.time_to_decision_s >= 0.0
