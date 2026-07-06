"""Property-based test for telemetry filtering (telemetry.py, task 6.10).

Feature: session-telemetry

This module implements design **Property 14: Filtering selects exactly the
records satisfying every predicate**:

    For any set of Session_Records and any combination of symbol / timeframe /
    mode / time-range filters, the filtered result contains exactly those records
    that match all supplied filters (equality on symbol/timeframe/mode and
    ``started_at`` within the ``[since, until]`` range) and no others.

Validates: Requirements 5.3.

The oracle below mirrors ``telemetry.filter_sessions``'s actual contract exactly:

  * ``symbol`` / ``timeframe`` / ``mode`` — equality; a ``None`` filter imposes no
    constraint on that attribute.
  * ``since`` / ``until`` — an INCLUSIVE ``[since, until]`` bound on ``started_at``
    (``since`` alone => lower bound only, ``until`` alone => upper bound only). A
    supplied bound is only usable when it is a finite number; otherwise it imposes
    no constraint.
  * A record whose ``started_at`` is not an observable finite number is EXCLUDED
    whenever any (finite) time bound is supplied, and unconstrained otherwise.

The sys.path / import pattern mirrors
``tests/test_telemetry_outcome_rates_properties.py``.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry import SessionRecord, filter_sessions  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Generators
# ─────────────────────────────────────────────────────────────────────────────

# Small, overlapping pools so filters frequently match (and frequently exclude).
# ``None`` is included as a possible RECORD attribute value so a ``None`` record
# attribute vs a supplied filter is exercised.
_SYMBOLS = ["RELIANCE", "TCS", "INFY", None]
_TIMEFRAMES = ["1m", "15m", "1h", None]
_MODES = ["FIND", "MANAGE", None]

# ``started_at`` spans ordinary finite timestamps plus non-finite values (NaN,
# +/-inf) so the "finite started_at required under a time bound" clause is tested.
_started_at = st.one_of(
    st.floats(min_value=-1_000.0, max_value=10_000.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([float("nan"), float("inf"), float("-inf")]),
)


def _make_record(index, symbol, timeframe, mode, started_at):
    """Build a well-formed SessionRecord; only the filtered attributes vary."""
    return SessionRecord(
        session_id=f"t{index}:{index}",
        thread_id=f"t{index}",
        symbol=symbol,
        timeframe=timeframe,
        mode=mode,
        started_at=started_at,
        ended_at=None,
        outcome=None,
        hold_reason=None,
        watch_cycles=0,
        target_events=0,
        invalidation_events=0,
        resume_count=0,
        reasoning_turns=0,
        tool_calls_total=0,
        tool_calls_by_name={},
        model_turns=0,
        tokens=None,
        time_to_decision_s=None,
        suspended_s=None,
        funnel=[],
    )


_record_specs = st.lists(
    st.tuples(
        st.sampled_from(_SYMBOLS),
        st.sampled_from(_TIMEFRAMES),
        st.sampled_from(_MODES),
        _started_at,
    ),
    min_size=0,
    max_size=40,
).map(
    lambda specs: [
        _make_record(i, sym, tf, md, sa) for i, (sym, tf, md, sa) in enumerate(specs)
    ]
)

# Each filter is optionally None (no constraint). Time bounds are either None or a
# finite float within/around the started_at range so both matches and misses occur.
_filter_symbol = st.sampled_from(["RELIANCE", "TCS", "INFY", None])
_filter_timeframe = st.sampled_from(["1m", "15m", "1h", None])
_filter_mode = st.sampled_from(["FIND", "MANAGE", None])
_filter_bound = st.one_of(
    st.none(),
    st.floats(min_value=-500.0, max_value=10_500.0, allow_nan=False, allow_infinity=False),
)


def _finite(value):
    """Mirror telemetry._finite_number: finite real number, not a bool."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _oracle(records, symbol, timeframe, mode, since, until):
    """Independent reimplementation of filter_sessions's exact contract."""
    low = float(since) if _finite(since) else None
    high = float(until) if _finite(until) else None
    time_constrained = low is not None or high is not None

    selected = []
    for r in records:
        if symbol is not None and r.symbol != symbol:
            continue
        if timeframe is not None and r.timeframe != timeframe:
            continue
        if mode is not None and r.mode != mode:
            continue
        if time_constrained:
            sa = r.started_at
            if not _finite(sa):
                continue
            sa = float(sa)
            if low is not None and sa < low:
                continue
            if high is not None and sa > high:
                continue
        selected.append(r)
    return selected


# ─────────────────────────────────────────────────────────────────────────────
# Property 14 (task 6.10): Filtering selects exactly the records satisfying every predicate
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 14: Filtering selects exactly the records satisfying every predicate
@settings(max_examples=100, deadline=None)
@given(
    records=_record_specs,
    symbol=_filter_symbol,
    timeframe=_filter_timeframe,
    mode=_filter_mode,
    since=_filter_bound,
    until=_filter_bound,
)
def test_property_14_filtering_selects_exactly_matching_records(
    records, symbol, timeframe, mode, since, until
):
    """Feature: session-telemetry, Property 14: Filtering selects exactly the
    records satisfying every predicate — the filtered result contains exactly the
    records matching every supplied filter (equality on symbol/timeframe/mode and
    started_at within the inclusive [since, until] range) and no others, in
    original relative order.

    Validates: Requirements 5.3
    """
    result = filter_sessions(
        records,
        symbol=symbol,
        timeframe=timeframe,
        mode=mode,
        since=since,
        until=until,
    )
    expected = _oracle(records, symbol, timeframe, mode, since, until)

    # Compare by object identity so we assert EXACT selection AND preserved order,
    # independent of any value-equality collisions between records.
    assert [id(r) for r in result] == [id(r) for r in expected]

    # The result is a subsequence of the input (nothing added, nothing reordered).
    input_ids = [id(r) for r in records]
    result_ids = [id(r) for r in result]
    it = iter(input_ids)
    assert all(rid in it for rid in result_ids)

    # Every selected record actually satisfies every supplied predicate.
    low = float(since) if _finite(since) else None
    high = float(until) if _finite(until) else None
    time_constrained = low is not None or high is not None
    for r in result:
        if symbol is not None:
            assert r.symbol == symbol
        if timeframe is not None:
            assert r.timeframe == timeframe
        if mode is not None:
            assert r.mode == mode
        if time_constrained:
            assert _finite(r.started_at)
            if low is not None:
                assert float(r.started_at) >= low
            if high is not None:
                assert float(r.started_at) <= high
