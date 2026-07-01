"""Property-based test for telemetry terminal-outcome timing (telemetry.py, task 4.4).

Feature: session-telemetry

This module implements design **Property 2: A terminal outcome closes the Session
with a consistent time-to-decision**:

    For any Session whose observed lifecycle contains a terminal event (a
    ``DECISION`` or an ``ERROR``), the finalized Session_Record has a non-null
    ``ended_at`` with ``ended_at >= started_at``, and its ``time_to_decision_s``
    equals ``ended_at - started_at`` (a non-negative number).

Validates: Requirements 1.3, 3.1.

``finalize_session`` is the pure boundary that folds the mutable ``SessionState``
the background writer builds up into an immutable ``SessionRecord``. The writer
stamps ``ended_at`` (and the terminal ``outcome``) when the run reaches a terminal
event and appends the terminal ``FunnelEvent`` (a ``decision`` or an ``error``) to
the funnel. This test reconstructs exactly that accumulator — a ``started_at``, a
monotonic ``ended_at >= started_at``, and a funnel that ends in a terminal event —
via Hypothesis with monotonically non-decreasing timestamps, calls the real
``finalize_session``, and asserts the timing invariants. The sys.path / import
pattern mirrors ``tests/test_telemetry_config_robustness_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry import (  # noqa: E402
    FUNNEL_DECISION,
    FUNNEL_ERROR,
    FUNNEL_REASONING_TURN,
    FUNNEL_RESUMED,
    FUNNEL_SESSION_STARTED,
    FUNNEL_TOOL_CALL,
    FUNNEL_WATCH_REGISTERED,
    OUTCOME_ERROR,
    OUTCOME_HOLD,
    OUTCOME_TRADE_BUY,
    OUTCOME_TRADE_SELL,
    TRIGGER_INVALIDATION,
    TRIGGER_TARGET,
    FunnelEvent,
    SessionState,
    finalize_session,
)

# ── Strategies over Sessions that reached a terminal event ────────────────────
# A terminal event is a DECISION (trade_buy / trade_sell / hold) or an ERROR
# (Requirement 1.3). The writer stamps ``ended_at`` on that terminal event, so a
# terminal Session always carries an observable ``started_at`` and a monotonic
# ``ended_at >= started_at``.

_thread_id = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=1,
    max_size=16,
)
_symbol = st.one_of(st.none(), st.sampled_from(["RELIANCE", "INFY", "TCS"]))
_timeframe = st.one_of(st.none(), st.sampled_from(["5m", "15m", "1h"]))
_mode = st.one_of(st.none(), st.sampled_from(["FIND", "MANAGE"]))

# The terminal funnel event kind paired with the outcome the writer records.
_terminal_choice = st.sampled_from(
    [
        (FUNNEL_DECISION, OUTCOME_TRADE_BUY),
        (FUNNEL_DECISION, OUTCOME_TRADE_SELL),
        (FUNNEL_DECISION, OUTCOME_HOLD),
        (FUNNEL_ERROR, OUTCOME_ERROR),
    ]
)

# A non-terminal intermediate funnel event kind (no terminal semantics).
_intermediate_kind = st.sampled_from(
    [
        FUNNEL_REASONING_TURN,
        FUNNEL_WATCH_REGISTERED,
        FUNNEL_TOOL_CALL,
        FUNNEL_RESUMED,
    ]
)


@st.composite
def _terminal_sessions(draw):
    """Build a ``SessionState`` for a Session that reached a terminal event.

    Mirrors what the background writer accumulates: a ``started_at``, a monotonic
    ``ended_at >= started_at`` (stamped on the terminal event), and an ordered
    funnel that opens with ``session_started`` and ends in a terminal ``decision``
    / ``error`` event, with any intermediate events carrying non-decreasing,
    in-range timestamps. Some intermediate timestamps are left ``None`` (the funnel
    ``ts`` is optional) to exercise partial observability.
    """
    started_at = draw(st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False))
    # A non-negative duration keeps ended_at >= started_at (monotonic timestamps).
    duration = draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))
    ended_at = started_at + duration

    terminal_kind, outcome = draw(_terminal_choice)
    hold_reason = "voluntary" if outcome == OUTCOME_HOLD else None

    # Intermediate timestamps: a sorted (non-decreasing) sample within
    # [started_at, ended_at], some replaced by None (ts is optional).
    n_intermediate = draw(st.integers(min_value=0, max_value=6))
    raw_ts = sorted(
        draw(
            st.lists(
                st.floats(
                    min_value=started_at,
                    max_value=ended_at,
                    allow_nan=False,
                    allow_infinity=False,
                ),
                min_size=n_intermediate,
                max_size=n_intermediate,
            )
        )
    )

    funnel = [FunnelEvent(seq=0, kind=FUNNEL_SESSION_STARTED, ts=started_at)]
    seq = 1
    resume_count = 0
    target_events = 0
    invalidation_events = 0
    watch_cycles = 0
    reasoning_turns = 0
    for ts in raw_ts:
        kind = draw(_intermediate_kind)
        keep_ts = draw(st.booleans())
        event_ts = ts if keep_ts else None
        if kind == FUNNEL_RESUMED:
            trigger = draw(st.sampled_from([TRIGGER_TARGET, TRIGGER_INVALIDATION]))
            funnel.append(FunnelEvent(seq=seq, kind=kind, ts=event_ts, trigger_kind=trigger))
            resume_count += 1
            if trigger == TRIGGER_INVALIDATION:
                invalidation_events += 1
            else:
                target_events += 1
        elif kind == FUNNEL_WATCH_REGISTERED:
            funnel.append(FunnelEvent(seq=seq, kind=kind, ts=event_ts, tool_name="watch_price_condition"))
            watch_cycles += 1
        elif kind == FUNNEL_REASONING_TURN:
            funnel.append(FunnelEvent(seq=seq, kind=kind, ts=event_ts))
            reasoning_turns += 1
        else:  # FUNNEL_TOOL_CALL
            funnel.append(FunnelEvent(seq=seq, kind=kind, ts=event_ts, tool_name="get_candles"))
        seq += 1

    # The terminal event closes the Session; the writer stamps ended_at here.
    funnel.append(FunnelEvent(seq=seq, kind=terminal_kind, ts=ended_at))

    state = SessionState(
        thread_id=draw(_thread_id),
        symbol=draw(_symbol),
        timeframe=draw(_timeframe),
        mode=draw(_mode),
        started_at=started_at,
        ended_at=ended_at,
        outcome=outcome,
        hold_reason=hold_reason,
        watch_cycles=watch_cycles,
        target_events=target_events,
        invalidation_events=invalidation_events,
        resume_count=resume_count,
        reasoning_turns=reasoning_turns,
        funnel=funnel,
    )
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Property 2 (task 4.4): A terminal outcome closes the Session with a consistent
# time-to-decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 2: A terminal outcome closes the Session with a consistent time-to-decision
@settings(max_examples=100, deadline=None)
@given(state=_terminal_sessions())
def test_property_2_terminal_outcome_consistent_time_to_decision(state):
    """Feature: session-telemetry, Property 2: A terminal outcome closes the
    Session with a consistent time-to-decision — for any Session whose lifecycle
    contains a terminal event (a DECISION or an ERROR), the finalized
    Session_Record has a non-null ``ended_at`` with ``ended_at >= started_at`` and
    a ``time_to_decision_s`` equal to ``ended_at - started_at`` (a non-negative
    number).

    Validates: Requirements 1.3, 3.1
    """
    record = finalize_session(state)

    # The terminal event closes the Session: ended_at is non-null (Requirement 1.3).
    assert record.ended_at is not None

    # A terminal Session has an observable start, and the close never precedes it.
    assert record.started_at is not None
    assert record.ended_at >= record.started_at

    # time_to_decision_s is the wall-clock start->terminal duration (Requirement 3.1).
    assert record.time_to_decision_s is not None
    assert record.time_to_decision_s == record.ended_at - record.started_at

    # A duration is non-negative.
    assert record.time_to_decision_s >= 0.0
