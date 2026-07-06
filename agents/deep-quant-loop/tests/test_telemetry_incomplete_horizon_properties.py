"""Property-based test for incomplete classification past the horizon (task 6.4).

Feature: session-telemetry

This module implements design **Property 4: Sessions with no terminal outcome
past the horizon are classified incomplete**:

    For any set of Session_Records and any explicit reference time ``now_ref``, a
    record with no terminal outcome whose age (``now_ref - started_at``) exceeds
    the configured ``incomplete_horizon_seconds`` is counted as ``incomplete`` in
    the report, while an open record within the horizon is never counted under any
    terminal outcome.

Validates: Requirements 1.5.

The sys.path / import pattern and the SessionRecord builder mirror
``tests/test_telemetry_outcome_rates_properties.py``.

This pins the contract in ``telemetry._effective_outcome`` / ``telemetry.aggregate``:
an OPEN record (``outcome is None``) is classified ``incomplete`` ONLY when an
explicit ``now_ref`` is supplied and ``now_ref - started_at`` STRICTLY exceeds the
configured horizon; an open record within the horizon is UNCLASSIFIED — excluded
from ``session_count`` and counted under no terminal outcome.
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
    OUTCOME_ERROR,
    OUTCOME_HOLD,
    OUTCOME_INCOMPLETE,
    OUTCOME_TRADE_BUY,
    OUTCOME_TRADE_SELL,
    SessionRecord,
    TelemetryConfig,
    aggregate,
)

# Recognized terminal outcomes that are NOT ``incomplete`` — used for the always-
# classified records so the only source of an ``incomplete`` count is an open
# record that has aged past the horizon (isolates Property 4's behavior).
_TERMINAL_OUTCOMES = (OUTCOME_TRADE_BUY, OUTCOME_TRADE_SELL, OUTCOME_HOLD, OUTCOME_ERROR)


def _make_record(index, outcome, started_at):
    """Build a minimal, well-formed SessionRecord.

    ``outcome`` is a recognized terminal outcome for an already-closed session, or
    ``None`` for an open (incomplete-eligible) session. ``started_at`` fixes the
    record's age relative to the aggregation's ``now_ref``. Only the fields the
    horizon classifier reads (``outcome``, ``started_at``) drive this property; the
    rest are benign valid values.
    """
    open_session = outcome is None
    return SessionRecord(
        session_id=f"t{index}:{started_at}",
        thread_id=f"t{index}",
        symbol="RELIANCE",
        timeframe="15m",
        mode="FIND",
        started_at=started_at,
        ended_at=(None if open_session else started_at + 1.0),
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
        time_to_decision_s=(None if open_session else 1.0),
        suspended_s=None,
        funnel=[],
    )


@st.composite
def _scenario(draw):
    """Draw a horizon, an explicit now_ref, and a mix of record specs.

    Each spec is one of:
      * ``("terminal", outcome)`` — an already-closed session (always classified).
      * ``("open_past", excess)`` — an open session aged ``horizon + excess`` past
        now_ref, with ``excess >= 1.0`` so it STRICTLY exceeds the horizon
        (=> ``incomplete``).
      * ``("open_within", frac)`` — an open session aged ``frac * horizon`` with
        ``frac <= 0.9`` so it stays comfortably within the horizon (=> excluded).

    The margins (``excess >= 1.0``, age ``<= 0.9 * horizon``) sit far from the
    horizon boundary so double-precision subtraction can never flip a record's
    side of the ``>`` comparison.
    """
    horizon = draw(
        st.floats(min_value=100.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
    )
    now_ref = draw(
        st.floats(min_value=10_000_000.0, max_value=1_000_000_000.0, allow_nan=False, allow_infinity=False)
    )
    specs = draw(
        st.lists(
            st.one_of(
                st.tuples(st.just("terminal"), st.sampled_from(_TERMINAL_OUTCOMES)),
                st.tuples(
                    st.just("open_past"),
                    st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False),
                ),
                st.tuples(
                    st.just("open_within"),
                    st.floats(min_value=0.0, max_value=0.9, allow_nan=False, allow_infinity=False),
                ),
            ),
            min_size=0,
            max_size=30,
        )
    )
    return horizon, now_ref, specs


# ─────────────────────────────────────────────────────────────────────────────
# Property 4 (task 6.4): Sessions with no terminal outcome past the horizon are
# classified incomplete
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 4: Sessions with no terminal outcome past the horizon are classified incomplete
@settings(max_examples=100, deadline=None)
@given(scenario=_scenario())
def test_property_4_incomplete_past_horizon(scenario):
    """Feature: session-telemetry, Property 4: Sessions with no terminal outcome
    past the horizon are classified incomplete — an open record aged past the
    configured horizon (relative to the explicit now_ref) is counted as
    ``incomplete``, while an open record within the horizon is counted under no
    terminal outcome and is excluded from ``session_count`` entirely.

    Validates: Requirements 1.5
    """
    horizon, now_ref, specs = scenario

    config = TelemetryConfig(
        db_path="unused.db",
        weak_prior_min_sessions=20,
        incomplete_horizon_seconds=horizon,
    )

    records = []
    expected_terminal = {o: 0 for o in _TERMINAL_OUTCOMES}
    open_past_count = 0
    open_within_count = 0

    for index, (kind, param) in enumerate(specs):
        if kind == "terminal":
            outcome = param
            expected_terminal[outcome] += 1
            records.append(_make_record(index, outcome, now_ref - 1.0))
        elif kind == "open_past":
            excess = param
            open_past_count += 1
            # age = horizon + excess  (STRICTLY exceeds the horizon)
            records.append(_make_record(index, None, now_ref - horizon - excess))
        else:  # open_within
            frac = param
            open_within_count += 1
            # age = frac * horizon <= 0.9 * horizon  (within the horizon)
            records.append(_make_record(index, None, now_ref - (frac * horizon)))

    report = aggregate(records, config, now_ref=now_ref)

    session_count = report["session_count"]
    counts = report["outcomes"]["counts"]

    terminal_total = sum(expected_terminal.values())

    # ── Open-past-horizon records are counted as incomplete ─────────────────────
    # No terminal record is itself ``incomplete``, so the incomplete count equals
    # exactly the number of open records that aged past the horizon.
    assert counts[OUTCOME_INCOMPLETE] == open_past_count

    # ── Each closed session is counted under its own terminal outcome ───────────
    for outcome in _TERMINAL_OUTCOMES:
        assert counts[outcome] == expected_terminal[outcome]

    # ── Open-within-horizon records are excluded from session_count entirely ────
    # session_count == classified sessions == terminals + open-past; the
    # open-within records contribute to no count.
    assert session_count == terminal_total + open_past_count
    assert session_count == len(records) - open_within_count

    # ── Every counted session lands under exactly one outcome bucket ────────────
    assert sum(counts.values()) == session_count

    # ── The incomplete rate matches its frequency (null only on an empty scope) ─
    incomplete_rate = report["outcomes"]["incomplete_rate"]
    if session_count == 0:
        assert incomplete_rate is None
    else:
        assert incomplete_rate == open_past_count / float(session_count)
