"""Property-based test for telemetry breakdown partitioning (telemetry.py, task 6.8).

Feature: session-telemetry

This module implements design **Property 12: Breakdowns partition the sessions
by their grouping key**:

    For any set of Session_Records, the ``by_symbol`` (respectively
    ``by_timeframe``, ``by_mode``) groups are disjoint by key, every session
    belongs to exactly one group, the sum of the groups' ``session_count`` equals
    the total ``session_count``, and every member of a group shares that group's
    key.

Validates: Requirements 4.5.

The sys.path / import pattern mirrors
``tests/test_telemetry_outcome_rates_properties.py``.

Every generated Session_Record carries a recognized terminal ``outcome`` so
``aggregate``'s classifier counts each record exactly once (``session_count ==
len(records)``, no open-session / horizon ambiguity, ``now_ref`` left unset). The
records' ``symbol`` / ``timeframe`` / ``mode`` are drawn from small pools that
include ``None`` so the ``None``-keyed collapse branch of the partition is
exercised.
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

# Small pools (including ``None``) so groups collide and the None-keyed collapse
# branch of the partition is exercised.
_SYMBOLS = [None, "RELIANCE", "TCS", "INFY"]
_TIMEFRAMES = [None, "5m", "15m", "1h"]
_MODES = [None, "FIND", "MANAGE"]

# The report field names for the three breakdowns and the attribute each groups by.
_BREAKDOWNS = (
    (telemetry.REPORT_BY_SYMBOL, "symbol"),
    (telemetry.REPORT_BY_TIMEFRAME, "timeframe"),
    (telemetry.REPORT_BY_MODE, "mode"),
)


def _make_record(index, outcome, symbol, timeframe, mode):
    """Build a minimal, well-formed SessionRecord tagged with a terminal outcome.

    Only the grouping attributes (``symbol`` / ``timeframe`` / ``mode``) and the
    terminal ``outcome`` matter here; the counters/timings are benign valid values
    so ``aggregate`` treats the record as a fully classified session.
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


# A single record spec: a terminal outcome plus its grouping attributes.
_record_spec = st.tuples(
    st.sampled_from(list(SESSION_OUTCOMES)),
    st.sampled_from(_SYMBOLS),
    st.sampled_from(_TIMEFRAMES),
    st.sampled_from(_MODES),
)

# A list of classified Session_Records with varied grouping keys. ``min_size=0``
# so the empty-scope (no groups) branch is exercised too.
_records = st.lists(_record_spec, min_size=0, max_size=40).map(
    lambda specs: [
        _make_record(i, outcome, symbol, timeframe, mode)
        for i, (outcome, symbol, timeframe, mode) in enumerate(specs)
    ]
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 12 (task 6.8): Breakdowns partition the sessions by their grouping key
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 12: Breakdowns partition the sessions by their grouping key
@settings(max_examples=100, deadline=None)
@given(records=_records)
def test_property_12_breakdowns_partition_sessions(records):
    """Feature: session-telemetry, Property 12: Breakdowns partition the sessions
    by their grouping key — for each of by_symbol / by_timeframe / by_mode the
    group keys are distinct (disjoint), the sum of the groups' session_count
    equals the report session_count, and each group's session_count equals the
    number of records carrying that key value.

    Validates: Requirements 4.5
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

    for report_field, attr in _BREAKDOWNS:
        groups = report[report_field]

        # ── Group keys are distinct => the groups are disjoint (Property 12) ────
        keys = [group[telemetry.GROUP_KEY] for group in groups]
        assert len(keys) == len(set(keys)), (
            f"{report_field}: duplicate group keys {keys}"
        )

        # ── Every group's session_count equals the number of records with that
        #    attribute value, and every member shares that key (Property 12) ─────
        expected_by_key = {}
        for r in records:
            value = getattr(r, attr)
            expected_by_key[value] = expected_by_key.get(value, 0) + 1

        # Same set of keys: exactly the observed attribute values, none dropped.
        assert set(keys) == set(expected_by_key), (
            f"{report_field}: group keys {set(keys)} != observed values "
            f"{set(expected_by_key)}"
        )

        total = 0
        for group in groups:
            key = group[telemetry.GROUP_KEY]
            count = group["session_count"]
            assert count == expected_by_key[key], (
                f"{report_field}: group {key!r} count {count} != "
                f"{expected_by_key[key]}"
            )
            total += count

        # ── The groups partition the sessions: their counts sum to the total ────
        assert total == session_count, (
            f"{report_field}: group counts sum to {total} != {session_count}"
        )
