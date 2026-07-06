"""Property-based test for telemetry aggregation determinism (telemetry.py, task 6.11).

Feature: session-telemetry

This module implements design **Property 16: Aggregation is deterministic**:

    For any set of Session_Records and configuration, invoking ``aggregate``
    twice produces deeply-equal Telemetry_Reports.

Validates: Requirements 8.2.

The sys.path / import pattern mirrors
``tests/test_telemetry_outcome_rates_properties.py``.

The generator builds a varied set of Session_Records — a mix of terminal
outcomes and still-open (``outcome is None``) sessions, varied
symbol/timeframe/mode, funnel counters, cost proxies, and timings — plus an
arbitrary ``TelemetryConfig`` and an optional ``now_ref``. Open sessions plus a
supplied ``now_ref`` exercise the ``incomplete``-horizon classification path, so
determinism is checked across the full aggregation surface, not just the
fully-classified subset.
"""

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
    OUTCOME_HOLD,
    SESSION_OUTCOMES,
    SessionRecord,
    TelemetryConfig,
    aggregate,
)

# Small, bounded pools so groups (by symbol/timeframe/mode) actually collide and
# the breakdown paths are exercised, while keeping examples cheap.
_SYMBOLS = st.sampled_from([None, "RELIANCE", "TCS", "INFY"])
_TIMEFRAMES = st.sampled_from([None, "5m", "15m", "1h"])
_MODES = st.sampled_from([None, "FIND", "MANAGE"])

# An outcome drawn from the five terminal Session_Outcomes OR ``None`` (a
# still-open session, eligible for the incomplete-horizon classification).
_OUTCOME_OR_OPEN = st.one_of(st.none(), st.sampled_from(list(SESSION_OUTCOMES)))


@st.composite
def _session_records(draw):
    """Build a list of varied, well-formed SessionRecords.

    Mixes terminal and still-open outcomes, varied grouping keys, funnel
    counters, cost proxies (including nullable ``tokens``), and nullable timings,
    so ``aggregate`` traverses its outcome-rate, invalidation-rate, distribution,
    cost, breakdown, and incomplete-horizon paths.
    """
    n = draw(st.integers(min_value=0, max_value=25))
    records = []
    for i in range(n):
        started_at = draw(st.floats(min_value=0.0, max_value=1_000_000.0,
                                    allow_nan=False, allow_infinity=False))
        outcome = draw(_OUTCOME_OR_OPEN)

        # An open session has no ended_at / time_to_decision; a terminal one does.
        if outcome is None:
            ended_at = None
            time_to_decision_s = None
        else:
            duration = draw(st.floats(min_value=0.0, max_value=100_000.0,
                                      allow_nan=False, allow_infinity=False))
            ended_at = started_at + duration
            time_to_decision_s = duration

        target_events = draw(st.integers(min_value=0, max_value=6))
        invalidation_events = draw(st.integers(min_value=0, max_value=6))
        resume_count = target_events + invalidation_events

        tool_calls_by_name = draw(
            st.dictionaries(
                st.sampled_from(["watch_price_condition", "get_ohlcv", "get_quote"]),
                st.integers(min_value=1, max_value=5),
                max_size=3,
            )
        )
        tool_calls_total = sum(tool_calls_by_name.values())

        records.append(
            SessionRecord(
                session_id=f"t{i}:{started_at}",
                thread_id=f"t{i}",
                symbol=draw(_SYMBOLS),
                timeframe=draw(_TIMEFRAMES),
                mode=draw(_MODES),
                started_at=started_at,
                ended_at=ended_at,
                outcome=outcome,
                hold_reason=(telemetry.HOLD_VOLUNTARY if outcome == OUTCOME_HOLD else None),
                watch_cycles=draw(st.integers(min_value=0, max_value=8)),
                target_events=target_events,
                invalidation_events=invalidation_events,
                resume_count=resume_count,
                reasoning_turns=draw(st.integers(min_value=0, max_value=10)),
                tool_calls_total=tool_calls_total,
                tool_calls_by_name=tool_calls_by_name,
                model_turns=draw(st.integers(min_value=0, max_value=10)),
                tokens=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=100_000))),
                time_to_decision_s=time_to_decision_s,
                suspended_s=draw(st.one_of(
                    st.none(),
                    st.floats(min_value=0.0, max_value=100_000.0,
                              allow_nan=False, allow_infinity=False),
                )),
                funnel=[],
            )
        )
    return records


_configs = st.builds(
    TelemetryConfig,
    db_path=st.just("unused.db"),
    weak_prior_min_sessions=st.integers(min_value=1, max_value=50),
    incomplete_horizon_seconds=st.floats(min_value=1.0, max_value=1_000_000.0,
                                         allow_nan=False, allow_infinity=False),
)

# An optional reference clock: ``None`` (no ageing) or a finite wall-clock.
_now_refs = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=2_000_000.0, allow_nan=False, allow_infinity=False),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 16 (task 6.11): Aggregation is deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 16: Aggregation is deterministic
@settings(max_examples=100, deadline=None)
@given(records=_session_records(), config=_configs, now_ref=_now_refs)
def test_property_16_aggregation_is_deterministic(records, config, now_ref):
    """Feature: session-telemetry, Property 16: Aggregation is deterministic —
    invoking ``aggregate`` twice with identical inputs yields deeply-equal
    Telemetry_Reports.

    Validates: Requirements 8.2
    """
    first = aggregate(records, config, now_ref)
    second = aggregate(records, config, now_ref)

    # Deep structural equality of the two reports (dicts of dicts/lists/scalars).
    assert first == second
