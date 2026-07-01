"""Property-based test for telemetry suspended time (telemetry.py, task 4.6).

Feature: session-telemetry

This module implements design **Property 8: Suspended time sums observable watch
intervals and is bounded**:

    For any Session with observable watch->resume timestamps, ``suspended_s``
    equals the sum of its suspend intervals and satisfies
    ``0 <= suspended_s <= time_to_decision_s``; when no suspend interval is
    observable, ``suspended_s`` is ``null``.

Validates: Requirements 3.2.

``finalize_session`` derives ``suspended_s`` by pairing, in order, each watch-cycle
start recorded on ``SessionState.watch_starts`` with the timestamp of the
``resumed`` Funnel_Event that ended it (``zip(watch_starts, resume_ts)`` where
``resume_ts`` is the finite ``ts`` of the funnel's ``FUNNEL_RESUMED`` events in
order), summing the non-negative intervals and bounding the total into
``[0, time_to_decision_s]``. When no non-negative interval is observable (no watch
starts, no resume timestamps, or every interval negative) and no real pre-computed
value was supplied, ``suspended_s`` is ``None``.

This test builds ``SessionState`` inputs directly against that ACTUAL contract:
non-overlapping watch/suspend segments laid out along a monotonic
``started_at -> ended_at`` timeline (so every interval is non-negative and their
sum never exceeds the time-to-decision), plus explicit no-observable-interval
cases. The sys.path / import pattern mirrors
``tests/test_telemetry_funnel_counters_properties.py``.
"""

import os
import sys

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry import (  # noqa: E402
    FUNNEL_REASONING_TURN,
    FUNNEL_RESUMED,
    FUNNEL_WATCH_REGISTERED,
    FunnelEvent,
    SessionState,
    WATCH_TOOL_NAME,
    finalize_session,
)

# Moderate float ranges keep the constructed timeline free of precision noise
# while still spanning the input space (Requirement 3.2 timings are wall-clock s).
_start = st.floats(min_value=0.0, max_value=1e5, allow_nan=False, allow_infinity=False)
_gap = st.floats(min_value=0.0, max_value=1e3, allow_nan=False, allow_infinity=False)


@st.composite
def _observable_session(draw):
    """Build a Session with observable, non-negative watch->resume intervals.

    Lays ``n`` non-overlapping watch cycles along a strictly forward-marching
    timeline: from ``started_at`` each cycle is preceded by some active (pre-)gap,
    then suspends for ``suspend_i`` seconds (the observable interval), and a final
    active gap runs out to ``ended_at``. Because the segments never overlap and sit
    inside ``[started_at, ended_at]``, every interval is non-negative and their sum
    is <= ``ended_at - started_at`` by construction — so the natural suspended-time
    sum is already within bounds without any clamping.
    """
    started_at = draw(_start)
    n = draw(st.integers(min_value=1, max_value=6))
    pre_gaps = draw(st.lists(_gap, min_size=n, max_size=n))
    suspends = draw(st.lists(_gap, min_size=n, max_size=n))
    final_gap = draw(_gap)

    cursor = started_at
    watch_starts = []
    resume_ts = []
    for i in range(n):
        cursor += pre_gaps[i]      # active reasoning before the tripwire is placed
        watch_starts.append(cursor)  # watch cycle begins (suspended)
        cursor += suspends[i]      # time spent suspended in this watch cycle
        resume_ts.append(cursor)   # the resume that ended this watch cycle
    ended_at = cursor + final_gap

    return started_at, ended_at, watch_starts, resume_ts


def _build_state(started_at, ended_at, watch_starts, resume_ts):
    """Assemble a SessionState the way the writer would, for finalize_session.

    ``watch_starts`` carries the watch-cycle start timestamps; the funnel carries
    a ``resumed`` event (finite ``ts``) per resume in order, interleaved with
    ``watch_registered`` and ``reasoning_turn`` noise events (which finalize_session
    must ignore when pairing intervals). ``suspended_s`` is left ``None`` so the
    result is derived purely from the observable timestamps, never a fallback.
    """
    funnel = []
    seq = 0
    for i, (ws, rs) in enumerate(zip(watch_starts, resume_ts)):
        funnel.append(
            FunnelEvent(seq=seq, kind=FUNNEL_WATCH_REGISTERED, ts=ws, tool_name=WATCH_TOOL_NAME)
        )
        seq += 1
        funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_REASONING_TURN, ts=ws))
        seq += 1
        funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_RESUMED, ts=rs, trigger_kind="target"))
        seq += 1

    return SessionState(
        thread_id="t-suspend",
        symbol="RELIANCE",
        timeframe="15m",
        mode="FIND",
        started_at=started_at,
        ended_at=ended_at,
        watch_cycles=len(watch_starts),
        resume_count=len(resume_ts),
        watch_starts=list(watch_starts),
        funnel=funnel,
        suspended_s=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 8 (task 4.6): Suspended time sums observable watch intervals & is bounded
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 8: Suspended time sums observable watch intervals and is bounded
@settings(max_examples=100, deadline=None)
@given(session=_observable_session())
def test_property_8_suspended_time_sums_observable_intervals(session):
    """Feature: session-telemetry, Property 8: Suspended time sums observable watch
    intervals and is bounded — for a Session with observable watch->resume
    timestamps, ``suspended_s`` equals the sum of its (non-negative) suspend
    intervals and satisfies ``0 <= suspended_s <= time_to_decision_s``.

    Validates: Requirements 3.2
    """
    started_at, ended_at, watch_starts, resume_ts = session
    state = _build_state(started_at, ended_at, watch_starts, resume_ts)

    record = finalize_session(state)

    # Independent oracle: the sum of the observable (non-negative) suspend
    # intervals, paired in order exactly as the definition prescribes.
    intervals = [rs - ws for ws, rs in zip(watch_starts, resume_ts)]
    assert all(iv >= 0.0 for iv in intervals)  # construction guarantees observability
    expected_sum = sum(intervals)

    time_to_decision_s = ended_at - started_at

    # An observable interval exists -> suspended_s is a real number, not null.
    assert record.suspended_s is not None

    # suspended_s equals the sum of the observable suspend intervals.
    assert record.suspended_s == pytest.approx(expected_sum, abs=1e-6, rel=1e-9)

    # 0 <= suspended_s <= time_to_decision_s (bounded).
    assert record.suspended_s >= 0.0
    assert record.suspended_s <= time_to_decision_s + 1e-6

    # time_to_decision is itself the consistent, non-negative wall-clock span.
    assert record.time_to_decision_s == pytest.approx(time_to_decision_s, abs=1e-6, rel=1e-9)


@st.composite
def _no_observable_session(draw):
    """Build a Session in which NO watch->resume suspend interval is observable.

    Covers each distinct way ``finalize_session`` sees no usable interval:
      * ``no_watch_starts`` — resumes present but no watch-cycle starts recorded;
      * ``no_resumes``      — watch starts present but no ``resumed`` funnel events;
      * ``resume_ts_none``  — resumed events present but with no (``None``) ts;
      * ``negative``        — paired starts/resumes where every resume PRECEDES its
                              watch start, so every interval is negative (skipped).
    ``suspended_s`` is left ``None`` so no pre-computed fallback can apply.
    """
    started_at = draw(_start)
    ended_at = started_at + draw(_gap)
    kind = draw(st.sampled_from(["no_watch_starts", "no_resumes", "resume_ts_none", "negative"]))
    ts_pool = st.floats(min_value=0.0, max_value=1e5, allow_nan=False, allow_infinity=False)

    watch_starts = []
    funnel = []
    seq = 0

    if kind == "no_watch_starts":
        for rs in draw(st.lists(ts_pool, min_size=1, max_size=5)):
            funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_RESUMED, ts=rs, trigger_kind="target"))
            seq += 1
    elif kind == "no_resumes":
        watch_starts = draw(st.lists(ts_pool, min_size=1, max_size=5))
        for ws in watch_starts:
            funnel.append(
                FunnelEvent(seq=seq, kind=FUNNEL_WATCH_REGISTERED, ts=ws, tool_name=WATCH_TOOL_NAME)
            )
            seq += 1
    elif kind == "resume_ts_none":
        watch_starts = draw(st.lists(ts_pool, min_size=1, max_size=5))
        n = len(watch_starts)
        for _ in range(n):
            funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_RESUMED, ts=None, trigger_kind="target"))
            seq += 1
    else:  # "negative": every resume precedes its paired watch start
        n = draw(st.integers(min_value=1, max_value=5))
        bases = draw(st.lists(st.floats(min_value=100.0, max_value=1e5, allow_nan=False, allow_infinity=False), min_size=n, max_size=n))
        deltas = draw(st.lists(st.floats(min_value=1.0, max_value=99.0, allow_nan=False, allow_infinity=False), min_size=n, max_size=n))
        for i in range(n):
            ws = bases[i]
            rs = bases[i] - deltas[i]  # resume strictly before the watch start
            watch_starts.append(ws)
            funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_RESUMED, ts=rs, trigger_kind="target"))
            seq += 1

    state = SessionState(
        thread_id="t-noobs",
        started_at=started_at,
        ended_at=ended_at,
        watch_starts=list(watch_starts),
        funnel=funnel,
        suspended_s=None,
    )
    return state


# Feature: session-telemetry, Property 8: Suspended time sums observable watch intervals and is bounded
@settings(max_examples=100, deadline=None)
@given(state=_no_observable_session())
def test_property_8_suspended_time_null_when_no_observable_interval(state):
    """Feature: session-telemetry, Property 8: Suspended time sums observable watch
    intervals and is bounded — when no suspend interval is observable (no watch
    starts, no resume timestamps, or every interval negative) and no real
    pre-computed value was supplied, ``suspended_s`` is ``null``.

    Validates: Requirements 3.2
    """
    record = finalize_session(state)
    assert record.suspended_s is None
