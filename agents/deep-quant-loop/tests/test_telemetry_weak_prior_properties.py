"""Property-based test for telemetry weak-prior flagging (telemetry.py, task 6.9).

Feature: session-telemetry

This module implements design **Property 13: Weak-prior flagging matches the
configured minimum**:

    For any set of Session_Records and configured ``weak_prior_min_sessions``, the
    report's ``weak_prior`` flag (and each group's ``weak_prior`` flag) is ``true``
    exactly when that scope's ``session_count`` is below the configured minimum;
    the report also always reports the total ``session_count`` and the configured
    minimum.

Validates: Requirements 4.6, 5.2.

The sys.path / import pattern mirrors
``tests/test_telemetry_outcome_rates_properties.py``.

To keep every scope's ``session_count`` deterministic and equal to the number of
records in that scope, every generated Session_Record carries a recognized
terminal ``outcome`` drawn from the five Session_Outcomes, so ``aggregate``'s
classifier counts each record exactly once (no open-session / horizon ambiguity,
``now_ref`` left unset). The symbol / timeframe / mode grouping keys are drawn
from small pools so the per-group breakdowns hold varying, meaningful counts that
straddle the configured minimum.
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

# Small pools of grouping keys so the by_symbol / by_timeframe / by_mode groups
# carry varying counts that straddle the configured minimum.
_SYMBOLS = ("RELIANCE", "TCS", "INFY")
_TIMEFRAMES = ("5m", "15m", "1h")
_MODES = ("FIND", "MANAGE")


def _make_record(index, outcome, symbol, timeframe, mode):
    """Build a minimal, well-formed SessionRecord carrying ``outcome`` and keys.

    Only the fields weak-prior flagging reads (the terminal ``outcome`` that makes
    the record count, and the symbol / timeframe / mode grouping keys) matter here;
    counters/timings are benign valid values so ``aggregate`` treats the record as
    a fully classified session.
    """
    started_at = 1_000.0 + float(index)
    return SessionRecord(
        session_id=f"t{index}:{started_at}",
        thread_id=f"t{index}",
        symbol=symbol,
        timeframe=timeframe,
        mode=mode,
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


# One record spec: a terminal outcome plus a (symbol, timeframe, mode) grouping.
_record_spec = st.tuples(
    st.sampled_from(list(SESSION_OUTCOMES)),
    st.sampled_from(_SYMBOLS),
    st.sampled_from(_TIMEFRAMES),
    st.sampled_from(_MODES),
)

# A list of Session_Records, each deterministically classified so every scope's
# session_count equals the number of records in that scope. ``min_size=0`` so the
# empty-scope branch is exercised.
_records = st.lists(_record_spec, min_size=0, max_size=40).map(
    lambda specs: [
        _make_record(i, outcome, symbol, timeframe, mode)
        for i, (outcome, symbol, timeframe, mode) in enumerate(specs)
    ]
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 13 (task 6.9): Weak-prior flagging matches the configured minimum
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 13: Weak-prior flagging matches the configured minimum
@settings(max_examples=100, deadline=None)
@given(records=_records, min_sessions=st.integers(min_value=1, max_value=50))
def test_property_13_weak_prior_matches_configured_minimum(records, min_sessions):
    """Feature: session-telemetry, Property 13: Weak-prior flagging matches the
    configured minimum — the report's weak_prior flag (and each group's flag) is
    true exactly when that scope's session_count is below the configured minimum,
    and the report always reports the total session_count and the configured
    minimum.

    Validates: Requirements 4.6, 5.2
    """
    config = TelemetryConfig(
        db_path="unused.db",
        weak_prior_min_sessions=min_sessions,
        incomplete_horizon_seconds=float(24 * 3600),
    )

    # ``now_ref`` deliberately left unset: every record already carries a terminal
    # outcome, so classification is unambiguous and each scope's session_count is
    # exactly the number of records in that scope.
    report = aggregate(records, config)

    # ── The report always reports the total session_count and configured min ────
    assert "session_count" in report
    session_count = report["session_count"]
    assert session_count == len(records)
    assert report["weak_prior_min_sessions"] == min_sessions

    # ── Top-level weak_prior is true exactly when below the configured minimum ──
    assert report["weak_prior"] == (session_count < min_sessions)

    # ── Each group's weak_prior matches its own session_count vs the minimum ────
    for group_key in ("by_symbol", "by_timeframe", "by_mode"):
        for group in report[group_key]:
            assert group["weak_prior"] == (group["session_count"] < min_sessions)
