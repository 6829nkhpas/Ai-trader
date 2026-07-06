"""Property-based test for telemetry outcome classification (telemetry.py, task 4.2).

Feature: session-telemetry

This module implements design **Property 3: Outcome classification is total and
yields exactly one enum value**:

    For any combination of decision record, run status, and error flag,
    ``classify_outcome`` returns exactly one Session_Outcome from the fixed set
    ``{trade_buy, trade_sell, hold, error, incomplete}``, and returns a
    ``hold_reason`` in ``{voluntary, forced, data-gated}`` if and only if the
    outcome is ``hold``.

Validates: Requirements 1.4.

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
    HOLD_REASONS,
    OUTCOME_HOLD,
    SESSION_OUTCOMES,
    classify_outcome,
)

# ── Strategies spanning the whole documented input space ──────────────────────
# ``action`` is drawn from the recognized directional/hold tokens in varied case,
# plus garbage / non-string / absent, so both the recognized-terminal-decision
# branches and the "no recognized action -> incomplete" branch are exercised.
_action_token = st.one_of(
    st.sampled_from(
        [
            "BUY", "SELL", "HOLD",
            "buy", "sell", "hold",
            "  Buy  ", "SeLl", " hold\t",
            "LONG", "SHORT", "WAIT", "", "   ", "garbage", "b u y",
        ]
    ),
    st.text(max_size=8),
    st.integers(),
    st.none(),
    st.booleans(),
)

# Forced / data-gated markers the hold-reason classifier recognizes, mixed with
# arbitrary / absent values so every hold sub-reason branch (and none) is hit.
_source_token = st.one_of(
    st.sampled_from(["forced_hold", "force_hold", "FORCED_HOLD", "was forced", "analysis"]),
    st.text(max_size=12),
    st.none(),
)
_reason_token = st.one_of(
    st.sampled_from(
        [
            "no-decision-reached",
            "directional-data-unavailable",
            "data unavailable",
            "data gated",
            "forced by budget",
            "chose to stand aside",
        ]
    ),
    st.text(max_size=16),
    st.none(),
)
_flag_token = st.one_of(st.booleans(), st.none(), st.sampled_from(["yes", "no", 1, 0]))


@st.composite
def _decision_records(draw):
    """A decision record: ``None``, a non-dict, or a dict with arbitrary/absent
    action and forced/gated markers (keys themselves optionally present)."""
    shape = draw(st.integers(min_value=0, max_value=2))
    if shape == 0:
        return None
    if shape == 1:
        # A non-dict decision (must still classify without raising).
        return draw(st.one_of(st.text(max_size=8), st.integers(), st.lists(st.integers(), max_size=3)))

    record = {}
    if draw(st.booleans()):
        record["action"] = draw(_action_token)
    if draw(st.booleans()):
        record["source"] = draw(_source_token)
    if draw(st.booleans()):
        record["reason"] = draw(_reason_token)
    if draw(st.booleans()):
        record["forced"] = draw(_flag_token)
    if draw(st.booleans()):
        record["data_gated"] = draw(_flag_token)
    if draw(st.booleans()):
        record["gated"] = draw(_flag_token)
    return record


_run_status = st.one_of(
    st.none(),
    st.sampled_from(["completed", "paused", "error", "running", "", "finished"]),
    st.text(max_size=10),
    st.integers(),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 3 (task 4.2): Outcome classification is total and yields exactly one enum value
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 3: Outcome classification is total and yields exactly one enum value
@settings(max_examples=100, deadline=None)
@given(decision=_decision_records(), run_status=_run_status, errored=st.booleans())
def test_property_3_outcome_classification_total_and_single_enum(decision, run_status, errored):
    """Feature: session-telemetry, Property 3: Outcome classification is total and
    yields exactly one enum value — for any decision record, run status, and error
    flag, ``classify_outcome`` (1) never raises, (2) returns an outcome in
    ``SESSION_OUTCOMES``, and (3) returns a ``hold_reason`` in ``HOLD_REASONS`` if
    and only if the outcome is ``hold`` (non-None iff hold, None otherwise).

    Validates: Requirements 1.4
    """
    # (1) Total: never raises on any tolerated input.
    result = classify_outcome(decision, run_status, errored)

    # The result is exactly a (outcome, hold_reason) pair.
    assert isinstance(result, tuple)
    assert len(result) == 2
    outcome, hold_reason = result

    # (2) The outcome is exactly one member of the fixed Session_Outcome set.
    assert outcome in SESSION_OUTCOMES

    # (3) hold_reason is a valid HOLD_REASONS member IFF the outcome is hold.
    if outcome == OUTCOME_HOLD:
        assert hold_reason in HOLD_REASONS
    else:
        assert hold_reason is None
