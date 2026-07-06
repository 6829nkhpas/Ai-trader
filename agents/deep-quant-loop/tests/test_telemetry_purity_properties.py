"""Property-based test for telemetry purity (telemetry.py, task 6.13).

Feature: session-telemetry

This module implements design **Property 18: Aggregation and interpretation are
pure (no input mutation)**:

    For any set of Session_Records (and any observed event stream), invoking
    ``aggregate`` (respectively ``interpret_events`` / ``finalize_session``)
    produces no observable change to its input arguments.

Validates: Requirements 8.4.

The three pure entry points of the telemetry core each promise to read only their
arguments and never mutate them (Requirement 8.4). This property verifies that
promise directly: it deep-copies every input before the call and asserts each
input is still deeply equal to its pre-call snapshot afterward. ``SessionRecord``,
``RunEntry`` and ``FunnelEvent`` are frozen dataclasses and ``SessionState`` is a
mutable dataclass; all four support structural ``==``, and ``copy.deepcopy``
snapshots the mutable containers they carry (``tool_calls_by_name``, ``funnel``,
``watch_starts``). The sys.path / import pattern mirrors
``tests/test_telemetry_outcome_rates_properties.py``.
"""

import copy
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry import (  # noqa: E402
    EVENT_DECISION,
    EVENT_ERROR,
    EVENT_REASONING,
    EVENT_TOOL_CALL_START,
    FUNNEL_DECISION,
    FUNNEL_ERROR,
    FUNNEL_REASONING_TURN,
    FUNNEL_RESUMED,
    FUNNEL_SESSION_STARTED,
    FUNNEL_TOOL_CALL,
    FUNNEL_WATCH_REGISTERED,
    HOLD_VOLUNTARY,
    SESSION_OUTCOMES,
    WATCH_TOOL_NAME,
    FunnelEvent,
    RunEntry,
    SessionRecord,
    SessionState,
    TelemetryConfig,
    aggregate,
    finalize_session,
    interpret_events,
)

# ── Shared value strategies ───────────────────────────────────────────────────

_symbol = st.one_of(st.none(), st.sampled_from(["RELIANCE", "INFY", "TCS"]))
_timeframe = st.one_of(st.none(), st.sampled_from(["5m", "15m", "1h"]))
_mode = st.one_of(st.none(), st.sampled_from(["FIND", "MANAGE"]))
_opt_ts = st.one_of(
    st.none(), st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
)
_count = st.integers(min_value=0, max_value=25)
_funnel_kind = st.sampled_from(
    [
        FUNNEL_SESSION_STARTED,
        FUNNEL_RESUMED,
        FUNNEL_REASONING_TURN,
        FUNNEL_WATCH_REGISTERED,
        FUNNEL_TOOL_CALL,
        FUNNEL_DECISION,
        FUNNEL_ERROR,
    ]
)
_opt_tool = st.one_of(st.none(), st.sampled_from([WATCH_TOOL_NAME, "get_candles", "order_flow"]))
_opt_trigger = st.one_of(st.none(), st.sampled_from(["target", "invalidation"]))
_opt_extra = st.one_of(
    st.none(),
    st.dictionaries(
        st.sampled_from(["tier", "budget", "note"]),
        st.one_of(st.integers(-5, 5), st.text(max_size=4)),
        max_size=3,
    ),
)

_tool_calls_by_name = st.dictionaries(
    st.sampled_from([WATCH_TOOL_NAME, "get_candles", "order_flow", "options_snapshot"]),
    st.integers(min_value=1, max_value=10),
    max_size=4,
)


@st.composite
def _funnel_events(draw, max_size=6):
    """A short list of well-formed FunnelEvents with contiguous seq numbers."""
    length = draw(st.integers(min_value=0, max_value=max_size))
    events = []
    for seq in range(length):
        events.append(
            FunnelEvent(
                seq=seq,
                kind=draw(_funnel_kind),
                ts=draw(_opt_ts),
                trigger_kind=draw(_opt_trigger),
                tool_name=draw(_opt_tool),
                extra=draw(_opt_extra),
            )
        )
    return events


@st.composite
def _session_records(draw, max_size=8):
    """A list of arbitrary, well-formed SessionRecords (frozen; mutable members)."""
    length = draw(st.integers(min_value=0, max_value=max_size))
    records = []
    for i in range(length):
        started_at = draw(st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False))
        # ended_at may be open (None) so aggregate exercises the incomplete branch.
        ended_at = draw(st.one_of(st.none(), st.just(started_at + draw(st.floats(0.0, 1e5, allow_nan=False, allow_infinity=False)))))
        outcome = draw(st.one_of(st.none(), st.sampled_from(list(SESSION_OUTCOMES))))
        records.append(
            SessionRecord(
                session_id=f"t{i}:{started_at}",
                thread_id=f"t{i}",
                symbol=draw(_symbol),
                timeframe=draw(_timeframe),
                mode=draw(_mode),
                started_at=started_at,
                ended_at=ended_at,
                outcome=outcome,
                hold_reason=(HOLD_VOLUNTARY if outcome == "hold" else None),
                watch_cycles=draw(_count),
                target_events=draw(_count),
                invalidation_events=draw(_count),
                resume_count=draw(_count),
                reasoning_turns=draw(_count),
                tool_calls_total=draw(_count),
                tool_calls_by_name=draw(_tool_calls_by_name),
                model_turns=draw(_count),
                tokens=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=100000))),
                time_to_decision_s=draw(st.one_of(st.none(), st.floats(0.0, 1e5, allow_nan=False, allow_infinity=False))),
                suspended_s=draw(st.one_of(st.none(), st.floats(0.0, 1e5, allow_nan=False, allow_infinity=False))),
                funnel=draw(_funnel_events()),
            )
        )
    return records


_config = st.builds(
    lambda mins, horizon: TelemetryConfig(
        db_path="unused.db",
        weak_prior_min_sessions=mins,
        incomplete_horizon_seconds=horizon,
    ),
    st.integers(min_value=1, max_value=50),
    st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False),
)

_opt_now_ref = st.one_of(
    st.none(), st.floats(min_value=0.0, max_value=2e9, allow_nan=False, allow_infinity=False)
)


# ── interpret_events inputs (entry + observed event stream) ───────────────────

_tool_payload = st.builds(
    lambda tool, ts: ({"tool": tool} if tool is not None else {}) | ({"ts": ts} if ts is not None else {}),
    _opt_tool,
    _opt_ts,
)
_observed_event = st.one_of(
    st.tuples(st.just(EVENT_REASONING), st.just({})),
    st.tuples(st.just(EVENT_TOOL_CALL_START), _tool_payload),
    st.tuples(st.just(EVENT_DECISION), st.just({"action": "HOLD"})),
    st.tuples(st.just(EVENT_ERROR), st.just({})),
    st.tuples(st.sampled_from(["RUN_STARTED", "RUN_FINISHED", "TOOL_CALL_END"]), st.just({})),
)
_event_stream = st.lists(_observed_event, max_size=10)
_run_entry = st.one_of(
    st.builds(
        lambda s, t, m: RunEntry(kind="run", symbol=s, timeframe=t, mode=m),
        _symbol, _timeframe, _mode,
    ),
    st.builds(
        lambda tk: RunEntry(kind="resume", trigger_kind=tk),
        st.sampled_from(["target", "invalidation", None]),
    ),
)


# ── SessionState inputs (mutable accumulator) ─────────────────────────────────

@st.composite
def _session_states(draw):
    """A well-formed, mutable SessionState with populated lists/dicts."""
    started_at = draw(st.one_of(st.none(), st.floats(0.0, 1e9, allow_nan=False, allow_infinity=False)))
    return SessionState(
        thread_id=draw(st.text(min_size=1, max_size=6)),
        symbol=draw(_symbol),
        timeframe=draw(_timeframe),
        mode=draw(_mode),
        started_at=started_at,
        ended_at=draw(st.one_of(st.none(), st.floats(0.0, 1e9, allow_nan=False, allow_infinity=False))),
        outcome=draw(st.one_of(st.none(), st.sampled_from(list(SESSION_OUTCOMES)))),
        hold_reason=draw(st.one_of(st.none(), st.just(HOLD_VOLUNTARY))),
        watch_cycles=draw(_count),
        target_events=draw(_count),
        invalidation_events=draw(_count),
        resume_count=draw(_count),
        reasoning_turns=draw(_count),
        tool_calls_total=draw(_count),
        tool_calls_by_name=draw(_tool_calls_by_name),
        model_turns=draw(_count),
        tokens=draw(st.one_of(st.none(), st.integers(0, 100000))),
        funnel=draw(_funnel_events()),
        watch_starts=draw(st.lists(st.floats(0.0, 1e9, allow_nan=False, allow_infinity=False), max_size=5)),
        suspended_s=draw(st.one_of(st.none(), st.floats(0.0, 1e5, allow_nan=False, allow_infinity=False))),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 18 (task 6.13): Aggregation and interpretation are pure (no mutation)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 18: Aggregation and interpretation are pure (no input mutation)
@settings(max_examples=100, deadline=None)
@given(records=_session_records(), config=_config, now_ref=_opt_now_ref)
def test_property_18_aggregate_is_pure(records, config, now_ref):
    """Feature: session-telemetry, Property 18: Aggregation and interpretation are
    pure (no input mutation) — ``aggregate(records, config, now_ref)`` does not
    mutate its ``records`` list (nor any SessionRecord, including the nested
    ``tool_calls_by_name`` dict and ``funnel`` list) nor its ``config``.

    Validates: Requirements 8.4
    """
    records_snapshot = copy.deepcopy(records)
    config_snapshot = copy.deepcopy(config)

    aggregate(records, config, now_ref)

    assert records == records_snapshot
    assert config == config_snapshot


# Feature: session-telemetry, Property 18: Aggregation and interpretation are pure (no input mutation)
@settings(max_examples=100, deadline=None)
@given(entry=_run_entry, events=_event_stream)
def test_property_18_interpret_events_is_pure(entry, events):
    """Feature: session-telemetry, Property 18: Aggregation and interpretation are
    pure (no input mutation) — ``interpret_events(entry, events)`` does not mutate
    its ``entry`` nor its ``events`` stream (list of tuples with dict payloads).

    Validates: Requirements 8.4
    """
    entry_snapshot = copy.deepcopy(entry)
    events_snapshot = copy.deepcopy(events)

    interpret_events(entry, events)

    assert entry == entry_snapshot
    assert events == events_snapshot


# Feature: session-telemetry, Property 18: Aggregation and interpretation are pure (no input mutation)
@settings(max_examples=100, deadline=None)
@given(state=_session_states())
def test_property_18_finalize_session_is_pure(state):
    """Feature: session-telemetry, Property 18: Aggregation and interpretation are
    pure (no input mutation) — ``finalize_session(state)`` reads the mutable
    ``SessionState`` (including its ``tool_calls_by_name`` dict, ``funnel`` and
    ``watch_starts`` lists) without mutating it.

    Validates: Requirements 8.4
    """
    state_snapshot = copy.deepcopy(state)

    finalize_session(state)

    assert state == state_snapshot
