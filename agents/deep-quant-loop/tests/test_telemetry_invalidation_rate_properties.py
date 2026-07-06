"""Property-based test for the telemetry invalidation rate (telemetry.py, task 6.6).

Feature: session-telemetry

This module implements design **Property 10: Invalidation rate is inv/(inv+target),
null when the total is zero**:

    For any set of Session_Records, ``invalidation_rate`` equals
    ``total_invalidation_events / (total_invalidation_events + total_target_events)``,
    lies in ``[0, 1]``, and is exactly ``null`` when
    ``total_invalidation_events + total_target_events`` is zero.

Validates: Requirements 4.3.

``aggregate`` computes the report-level ``invalidation_rate`` over its CLASSIFIED
records — those carrying a recognized ``outcome`` (a member of ``SESSION_OUTCOMES``)
or an open record aged past the horizon relative to ``now_ref``. To exercise the
invalidation-rate contract directly, every generated ``SessionRecord`` is given a
terminal ``outcome`` so it is classified and counted, and the records carry varied
``invalidation_events`` / ``target_events`` counts (including all-zero cases). The
oracle sums those counts independently of the module under test.

The sys.path / import pattern mirrors
``tests/test_telemetry_config_robustness_properties.py``.
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
    SESSION_OUTCOMES,
    REPORT_INVALIDATION_RATE,
    SessionRecord,
    TelemetryConfig,
    aggregate,
)

# A concrete, valid config (constructed directly, not resolved from env). The
# invalidation rate does not depend on the config, but ``aggregate`` requires one.
_CONFIG = TelemetryConfig(
    db_path=":memory:",
    weak_prior_min_sessions=20,
    incomplete_horizon_seconds=24 * 3600,
)


def _session_record(
    idx,
    invalidation_events,
    target_events,
    outcome,
    started_at,
):
    """Build a classified SessionRecord carrying the given event counts.

    Every field is populated with a valid, minimal value; ``outcome`` is drawn from
    ``SESSION_OUTCOMES`` so the record is CLASSIFIED and therefore fed into the
    report-level invalidation rate. Only ``invalidation_events`` / ``target_events``
    vary the metric under test.
    """
    return SessionRecord(
        session_id=f"t{idx}:{started_at}",
        thread_id=f"t{idx}",
        symbol="RELIANCE",
        timeframe="15m",
        mode="FIND",
        started_at=float(started_at),
        ended_at=float(started_at) + 1.0,
        outcome=outcome,
        hold_reason=None,
        watch_cycles=invalidation_events + target_events,
        target_events=target_events,
        invalidation_events=invalidation_events,
        resume_count=invalidation_events + target_events,
        reasoning_turns=0,
        tool_calls_total=0,
        tool_calls_by_name={},
        model_turns=0,
        tokens=None,
        time_to_decision_s=1.0,
        suspended_s=None,
        funnel=[],
    )


# ── Per-record generator: varied inv/target counts (including all-zero) ────────
# ``min_value=0`` deliberately includes records that contribute nothing to either
# sum, so a whole set can total zero and exercise the null branch (R4.3).
_record_fields = st.fixed_dictionaries(
    {
        "invalidation_events": st.integers(min_value=0, max_value=50),
        "target_events": st.integers(min_value=0, max_value=50),
        "outcome": st.sampled_from(SESSION_OUTCOMES),
        "started_at": st.floats(
            min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
        ),
    }
)

# A set of Session_Records, including the empty set (which totals zero => null).
_records_strategy = st.lists(_record_fields, min_size=0, max_size=25)


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (task 6.6): Invalidation rate is inv/(inv+target), null when zero
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 10: Invalidation rate is inv/(inv+target), null when the total is zero
@settings(max_examples=100, deadline=None)
@given(raw_records=_records_strategy)
def test_property_10_invalidation_rate(raw_records):
    """Feature: session-telemetry, Property 10: Invalidation rate is inv/(inv+target),
    null when the total is zero — for any set of Session_Records, the report's
    ``invalidation_rate`` equals ``inv/(inv+target)`` and lies in ``[0, 1]`` when the
    combined total is positive, and is exactly ``None`` when that total is zero.

    Validates: Requirements 4.3
    """
    records = [
        _session_record(
            idx,
            fields["invalidation_events"],
            fields["target_events"],
            fields["outcome"],
            fields["started_at"],
        )
        for idx, fields in enumerate(raw_records)
    ]

    # Independent oracle: sum the per-Session counts without the module's help.
    inv_sum = sum(f["invalidation_events"] for f in raw_records)
    target_sum = sum(f["target_events"] for f in raw_records)
    total = inv_sum + target_sum

    report = aggregate(records, _CONFIG)
    rate = report[REPORT_INVALIDATION_RATE]

    if total == 0:
        # Zero denominator => the rate is UNAVAILABLE and represented as null.
        assert rate is None
    else:
        assert rate is not None
        # Equals the invalidation share exactly.
        assert rate == inv_sum / total
        # Lies within the unit interval.
        assert 0.0 <= rate <= 1.0
