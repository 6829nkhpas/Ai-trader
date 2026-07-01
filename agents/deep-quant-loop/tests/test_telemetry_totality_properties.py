"""Property-based test for telemetry aggregation totality (telemetry.py, task 6.12).

Feature: session-telemetry

This module implements design **Property 17: Aggregation is total and represents
unavailable metrics as null**:

    For any set of Session_Records — including degenerate input such as zero
    sessions, sessions with zero watch cycles, or sessions with null timings —
    ``aggregate`` never raises and represents every unavailable metric (a ratio
    with a zero denominator, a distribution over an empty sample) as ``null``.

Validates: Requirements 8.3.

The sys.path / import pattern mirrors
``tests/test_telemetry_outcome_rates_properties.py``.

The generator here deliberately builds DEGENERATE and arbitrary records: the empty
list, records with zero counts, records with ``None`` timings / tokens, records
with ``None`` identity fields, records that are still open (``outcome`` ``None``),
and records carrying an unrecognized ``outcome`` string. Both ``now_ref`` unset and
an explicit ``now_ref`` are exercised. The assertions pin ``aggregate``'s totality
and null-representation contract against the actual report structure (see
``telemetry.aggregate`` / ``telemetry._distribution`` / ``telemetry._outcomes_block``
/ ``telemetry._invalidation_rate``).
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
    SESSION_OUTCOMES,
    OUTCOME_HOLD,
    SessionRecord,
    TelemetryConfig,
    aggregate,
)


# ── Helpers mirroring aggregate's classification / distribution contract ───────

def _is_finite_number(value):
    """Mirror telemetry._finite_number: a real, finite, non-bool int/float."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _effective_outcome(record, horizon, now_ref):
    """Replicate telemetry._effective_outcome for expected-value computation."""
    if record.outcome in SESSION_OUTCOMES:
        return record.outcome
    if now_ref is not None and _is_finite_number(now_ref):
        if _is_finite_number(record.started_at):
            if (float(now_ref) - float(record.started_at)) > horizon:
                return telemetry.OUTCOME_INCOMPLETE
    return None


def _classified(records, horizon, now_ref):
    return [r for r in records if _effective_outcome(r, horizon, now_ref) is not None]


# ── Strategies: degenerate / arbitrary Session_Records ─────────────────────────

# Identity fields may be null (Requirement 8.3 degenerate input).
_opt_str = st.one_of(
    st.none(),
    st.sampled_from(["RELIANCE", "INFY", "NIFTY"]),
    st.text(min_size=0, max_size=4),
)

# Outcome: open (None), a recognized terminal outcome, or an unrecognized string.
_opt_outcome = st.one_of(
    st.none(),
    st.sampled_from(list(SESSION_OUTCOMES)),
    st.sampled_from(["", "weird", "TRADE", "unknown-outcome"]),
)

# Timings may be null or finite floats (Requirement 8.3 null timings).
_opt_time = st.one_of(st.none(), st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))

# Non-negative integer counters, including zero (degenerate zero-count sessions).
_count = st.integers(min_value=0, max_value=5)


@st.composite
def _session_records(draw):
    """Build one degenerate/arbitrary SessionRecord."""
    index = draw(st.integers(min_value=0, max_value=10_000))
    started_at = draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))
    outcome = draw(_opt_outcome)
    return SessionRecord(
        session_id=f"t{index}:{started_at}",
        thread_id=f"t{index}",
        symbol=draw(_opt_str),
        timeframe=draw(_opt_str),
        mode=draw(_opt_str),
        started_at=started_at,
        ended_at=draw(_opt_time),
        outcome=outcome,
        hold_reason=(telemetry.HOLD_VOLUNTARY if outcome == OUTCOME_HOLD else None),
        watch_cycles=draw(_count),
        target_events=draw(_count),
        invalidation_events=draw(_count),
        resume_count=draw(_count),
        reasoning_turns=draw(_count),
        tool_calls_total=draw(_count),
        tool_calls_by_name=draw(st.dictionaries(st.sampled_from(["a", "b", "c"]), _count, max_size=3)),
        model_turns=draw(_count),
        tokens=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=10_000))),
        time_to_decision_s=draw(_opt_time),
        suspended_s=draw(_opt_time),
        funnel=[],
    )


# ``min_size=0`` so the zero-session (empty scope) branch is exercised.
_records = st.lists(_session_records(), min_size=0, max_size=25)

# now_ref: unset (None) or an explicit finite reference clock.
_now_ref = st.one_of(st.none(), st.floats(min_value=0.0, max_value=2e6, allow_nan=False, allow_infinity=False))

# Horizon: a small positive value so an explicit now_ref can age open sessions out,
# plus the documented large default so most stay open.
_horizon = st.sampled_from([1.0, 3600.0, float(24 * 3600)])


def _assert_distribution_null_contract(dist):
    """A Distribution reports null summaries exactly when its sample is empty."""
    assert set(dist.keys()) == {"mean", "median", "max", "count"}
    if dist["count"] == 0:
        assert dist["mean"] is None
        assert dist["median"] is None
        assert dist["max"] is None
    else:
        assert dist["mean"] is not None
        assert dist["median"] is not None
        assert dist["max"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# Property 17 (task 6.12): Aggregation is total and represents unavailable metrics as null
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 17: Aggregation is total and represents unavailable metrics as null
@settings(max_examples=100, deadline=None)
@given(records=_records, now_ref=_now_ref, horizon=_horizon)
def test_property_17_aggregation_total_and_null(records, now_ref, horizon):
    """Feature: session-telemetry, Property 17: Aggregation is total and
    represents unavailable metrics as null — for any (degenerate) set of records,
    aggregate never raises; on an empty classified scope the outcome rates and
    invalidation_rate are null and every distribution's mean/median/max are null;
    invalidation_rate is null when inv+target over the classified sessions is zero;
    and any distribution over an empty sample reports null mean/median/max.

    Validates: Requirements 8.3
    """
    config = TelemetryConfig(
        db_path="unused.db",
        weak_prior_min_sessions=20,
        incomplete_horizon_seconds=horizon,
    )

    # (1) aggregate never raises on any (degenerate) input.
    report = aggregate(records, config, now_ref=now_ref)

    # Determine the classified scope the report is computed over.
    effective_now = float(now_ref) if (now_ref is not None and _is_finite_number(now_ref)) else None
    classified = _classified(records, horizon, effective_now)
    session_count = report["session_count"]
    assert session_count == len(classified)

    outcomes = report["outcomes"]
    invalidation_rate = report["invalidation_rate"]

    # (2) Empty classified scope => every outcome rate and invalidation_rate null,
    #     and every distribution reports null mean/median/max.
    if session_count == 0:
        assert outcomes["conversion_rate"] is None
        assert outcomes["hold_rate"] is None
        assert outcomes["error_rate"] is None
        assert outcomes["incomplete_rate"] is None
        assert invalidation_rate is None
        for dist in (
            report["watch_cycles"],
            report["time_to_decision_s"],
            report["cost"]["tool_calls"],
            report["cost"]["model_turns"],
            report["cost"]["resume_count"],
        ):
            assert dist["count"] == 0
            assert dist["mean"] is None
            assert dist["median"] is None
            assert dist["max"] is None

    # (3) invalidation_rate is null exactly when inv+target over the classified
    #     sessions is zero (a ratio with a zero denominator is unavailable).
    inv_total = sum(r.invalidation_events for r in classified)
    target_total = sum(r.target_events for r in classified)
    if inv_total + target_total == 0:
        assert invalidation_rate is None
    else:
        assert invalidation_rate is not None
        assert 0.0 <= invalidation_rate <= 1.0

    # (4) Every distribution in the report honors the null-on-empty-sample contract.
    for dist in (
        report["watch_cycles"],
        report["time_to_decision_s"],
        report["cost"]["tool_calls"],
        report["cost"]["model_turns"],
        report["cost"]["resume_count"],
    ):
        _assert_distribution_null_contract(dist)

    # The time-to-decision distribution is a distribution over an EMPTY sample
    # whenever no classified session exposes a finite time_to_decision_s.
    finite_ttd = [r for r in classified if _is_finite_number(r.time_to_decision_s)]
    if not finite_ttd:
        ttd = report["time_to_decision_s"]
        assert ttd["mean"] is None
        assert ttd["median"] is None
        assert ttd["max"] is None
        assert ttd["count"] == 0

    # Group breakdowns must also stay total and honor the null contract.
    for group_key in ("by_symbol", "by_timeframe", "by_mode"):
        for group in report[group_key]:
            g_outcomes = group["outcomes"]
            if group["session_count"] == 0:
                assert g_outcomes["conversion_rate"] is None
                assert group["invalidation_rate"] is None
            _assert_distribution_null_contract(group["watch_cycles"])
