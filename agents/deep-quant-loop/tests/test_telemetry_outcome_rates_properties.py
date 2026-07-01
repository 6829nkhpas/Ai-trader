"""Property-based test for telemetry outcome rates (telemetry.py, task 6.5).

Feature: session-telemetry

This module implements design **Property 9: Outcome rates equal their
frequencies, sum to one, and are null on empty**:

    For any set of Session_Records, ``conversion_rate`` equals
    ``(#trade_buy + #trade_sell) / session_count`` and the ``hold``, ``error``,
    and ``incomplete`` rates each equal their count over ``session_count``; every
    rate lies in ``[0, 1]``; the four rates sum to ``1.0`` (within floating-point
    tolerance) over the classified sessions; and every rate is ``null`` when
    ``session_count`` is zero.

Validates: Requirements 4.1, 4.2.

The sys.path / import pattern mirrors
``tests/test_telemetry_config_robustness_properties.py``.

To keep ``session_count`` deterministic and equal to ``len(records)``, every
generated Session_Record carries a recognized terminal ``outcome`` drawn from the
five Session_Outcomes, so ``aggregate``'s classifier counts each record exactly
once (no open-session / horizon ambiguity, ``now_ref`` left unset). See
``telemetry._effective_outcome`` / ``telemetry._outcomes_block`` for the contract
this test pins.
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

import telemetry  # noqa: E402
from telemetry import (  # noqa: E402
    OUTCOME_ERROR,
    OUTCOME_HOLD,
    OUTCOME_INCOMPLETE,
    OUTCOME_TRADE_BUY,
    OUTCOME_TRADE_SELL,
    SESSION_OUTCOMES,
    SessionRecord,
    TelemetryConfig,
    aggregate,
)

# A tolerance for the "four rates sum to 1.0" floating-point check.
_TOL = 1e-9


def _make_record(index, outcome):
    """Build a minimal, well-formed SessionRecord carrying ``outcome``.

    Only the fields the outcome-rate aggregation reads (``outcome`` and the
    surrogate identity) matter here; the counters/timings are set to benign,
    valid values so ``aggregate`` treats the record as a fully classified session.
    """
    started_at = 1_000.0 + float(index)
    return SessionRecord(
        session_id=f"t{index}:{started_at}",
        thread_id=f"t{index}",
        symbol="RELIANCE",
        timeframe="15m",
        mode="FIND",
        started_at=started_at,
        ended_at=started_at + 1.0,
        outcome=outcome,
        hold_reason=(telemetry.HOLD_VOLUNTARY if outcome == OUTCOME_HOLD else None),
        watch_cycles=0,
        target_events=0,
        invalidation_events=0,
        resume_count=0,
        reasoning_turns=0,
        tool_calls_total=0,
        tool_calls_by_name={},
        model_turns=0,
        tokens=None,
        time_to_decision_s=1.0,
        suspended_s=None,
        funnel=[],
    )


# A list of Session_Records, each tagged with a recognized terminal outcome so it
# is deterministically classified (session_count == len(records)). ``min_size=0``
# so the empty-scope (null-rate) branch is exercised.
_records = st.lists(
    st.sampled_from(list(SESSION_OUTCOMES)),
    min_size=0,
    max_size=40,
).map(lambda outcomes: [_make_record(i, o) for i, o in enumerate(outcomes)])


# ─────────────────────────────────────────────────────────────────────────────
# Property 9 (task 6.5): Outcome rates equal their frequencies, sum to one, null on empty
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 9: Outcome rates equal their frequencies, sum to one, and are null on empty
@settings(max_examples=100, deadline=None)
@given(records=_records)
def test_property_9_outcome_rates_equal_frequencies(records):
    """Feature: session-telemetry, Property 9: Outcome rates equal their
    frequencies, sum to one, and are null on empty — conversion_rate equals
    (buy+sell)/n, the hold/error/incomplete rates equal their count over n, each
    rate lies in [0,1], the four rates sum to 1.0 (within tolerance) over the
    classified sessions, and every rate is null when session_count is zero.

    Validates: Requirements 4.1, 4.2
    """
    config = TelemetryConfig(
        db_path="unused.db",
        weak_prior_min_sessions=20,
        incomplete_horizon_seconds=float(24 * 3600),
    )

    # ``now_ref`` deliberately left unset: every record already carries a terminal
    # outcome, so classification is unambiguous and session_count == len(records).
    report = aggregate(records, config)

    session_count = report["session_count"]
    assert session_count == len(records)

    outcomes = report["outcomes"]
    counts = outcomes["counts"]

    # ── The reported counts match the actual frequencies of each outcome ────────
    expected_counts = {o: 0 for o in SESSION_OUTCOMES}
    for r in records:
        expected_counts[r.outcome] += 1
    for outcome in SESSION_OUTCOMES:
        assert counts[outcome] == expected_counts[outcome]

    conversion_rate = outcomes["conversion_rate"]
    hold_rate = outcomes["hold_rate"]
    error_rate = outcomes["error_rate"]
    incomplete_rate = outcomes["incomplete_rate"]

    if session_count == 0:
        # ── Empty scope: every rate is null (Requirement 4.2, Property 9) ───────
        assert conversion_rate is None
        assert hold_rate is None
        assert error_rate is None
        assert incomplete_rate is None
        return

    n = float(session_count)
    converted = expected_counts[OUTCOME_TRADE_BUY] + expected_counts[OUTCOME_TRADE_SELL]

    # ── Each rate equals its frequency (Requirement 4.1, 4.2) ───────────────────
    assert conversion_rate == converted / n
    assert hold_rate == expected_counts[OUTCOME_HOLD] / n
    assert error_rate == expected_counts[OUTCOME_ERROR] / n
    assert incomplete_rate == expected_counts[OUTCOME_INCOMPLETE] / n

    # ── Every rate lies in [0, 1] ───────────────────────────────────────────────
    for rate in (conversion_rate, hold_rate, error_rate, incomplete_rate):
        assert 0.0 <= rate <= 1.0

    # ── The four rates sum to 1.0 over the classified sessions (within tol) ─────
    total = conversion_rate + hold_rate + error_rate + incomplete_rate
    assert math.isclose(total, 1.0, abs_tol=_TOL)
