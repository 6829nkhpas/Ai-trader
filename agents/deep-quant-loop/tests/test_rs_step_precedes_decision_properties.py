"""Property-based test for relative-strength verification-step / decision
ordering (stream_events.py, task 10.3).

Feature: relative-strength-context

This module implements design **Property 24: The relative-strength verification
step precedes the DECISION event**:

    For any decision, the event sequence emitted by ``decision_events`` places
    the relative-strength ``VERIFICATION_STEP`` (the step carrying the stable
    check id ``relative-strength``) BEFORE the ``DECISION`` event of that run.

Validates: Requirements 9.6.

The implementation under test lives in ``stream_events.py``:
  - ``decision_events(decision)`` — yields ``(event_name, payload)`` tuples:
    every ``VERIFICATION_STEP`` (built by ``build_verification_steps``) first,
    then the single ``DECISION`` event (built by ``build_decision_event``).
  - ``build_verification_steps`` derives exactly one relative-strength step from
    the decision's ``defensibility`` record via ``_relative_strength_step``
    (check id ``relative-strength``), in both FIND mode (no ``validator_checks``)
    and VERIFY mode (``validator_checks`` present).

No LLM / graph / Rust server is invoked — ``decision_events`` is a pure function
of the decision dict. The decision's ``defensibility`` record is generated with
a ``relative_strength`` entry in both the available form (carrying an alignment)
and the unavailable form (``available`` falsy / a non-dict / missing), covering
the full outcome space (R9.2-R9.5). ``validator_checks`` are sometimes included
so the VERIFY-mode branch of ``build_verification_steps`` is exercised too.

The sys.path / import pattern mirrors
``tests/test_regime_step_ordering_properties.py``: the service directory (one
level up) is prepended to ``sys.path`` so ``stream_events`` is importable when
pytest is run from anywhere.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (stream_events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import (  # noqa: E402
    decision_events,
    VERIFICATION_STEP,
    DECISION,
)

RELATIVE_STRENGTH_CHECK_ID = "relative-strength"


# ── Strategies ───────────────────────────────────────────────────────────────
_index_direction = st.sampled_from(["up", "down", "flat"])
_relative_strength_state = st.sampled_from(["leader", "inline", "laggard"])
_alignment = st.sampled_from(["aligned", "misaligned", "neutral"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# A measure value is a finite number or null (None), per the RS contract.
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _available_rs_entry(draw):
    """An available relative-strength entry carrying an alignment (usable label)."""
    return {
        "available": True,
        "alignment": draw(_alignment),
        "index_direction": draw(_index_direction),
        "relative_strength_state": draw(_relative_strength_state),
        "benchmark": draw(st.sampled_from(["NIFTY", "BANKNIFTY", "SPX"])),
        "measures": {
            "rs_ratio_slope": draw(_measure_value),
            "relative_return": draw(_measure_value),
            "correlation": draw(_measure_value),
            "beta": draw(_measure_value),
            "index_return": draw(_measure_value),
        },
    }


# Unavailable relative strength: an explicit marker, a non-dict, or a missing
# entry — all of which drive the RS step to ``not-evaluable`` (R9.5) without
# changing the ordering guarantee under test.
_unavailable_rs_entry = st.one_of(
    st.just({"available": False, "reason": "insufficient aligned candles: 5 < 20 required"}),
    st.just({"available": False}),
    st.just({"alignment": "unrecognized-value", "available": True}),  # bad alignment
    st.none(),  # entry omitted entirely
    st.just("not-a-dict"),  # non-dict entry
)

_rs_entry = st.one_of(_available_rs_entry(), _unavailable_rs_entry)


@st.composite
def _validator_check(draw):
    """A single VERIFY-mode validator check (never the RS check itself)."""
    return {
        "check": draw(st.sampled_from(["risk-reward", "volatility-stop", "level-alignment"])),
        "outcome": draw(st.sampled_from(["pass", "fail", "informational"])),
    }


@st.composite
def _decision(draw):
    """Draw a decision dict whose defensibility record carries an RS entry."""
    rs = draw(_rs_entry)
    record = {}
    if rs is not None:
        record["relative_strength"] = rs

    # Sometimes include VERIFY-mode validator_checks so build_verification_steps
    # exercises the VERIFY branch (which appends the RS step) as well as the
    # FIND branch (which derives all checks including the RS step).
    if draw(st.booleans()):
        record["validator_checks"] = draw(st.lists(_validator_check(), min_size=0, max_size=4))

    return {
        "action": draw(_action),
        "conviction_score": draw(st.integers(min_value=0, max_value=10)),
        "setup_validation": draw(st.text(max_size=40)),
        "reason": draw(st.text(max_size=40)),
        "source": "declare_trade",
        "defensibility": record,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 24: the relative-strength verification step precedes the DECISION event
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 24: The relative-strength verification step precedes the DECISION event
@settings(max_examples=100, deadline=None)
@given(decision=_decision())
def test_property_24_rs_step_precedes_decision_event(decision):
    """Validates: Requirements 9.6

    For any decision, ``decision_events`` emits exactly one relative-strength
    ``VERIFICATION_STEP`` (check id ``relative-strength``) and exactly one
    ``DECISION`` event, and the relative-strength step is ordered strictly before
    the ``DECISION`` event of that run.
    """
    events = list(decision_events(decision))

    # Indices of the RS VERIFICATION_STEP(s) and the DECISION event(s).
    rs_indices = [
        i
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == RELATIVE_STRENGTH_CHECK_ID
    ]
    decision_indices = [i for i, (name, _) in enumerate(events) if name == DECISION]

    # Exactly one relative-strength step exists (R9.1) and exactly one DECISION.
    assert len(rs_indices) == 1, (
        f"expected exactly one relative-strength step, found {len(rs_indices)} in {events!r}"
    )
    assert len(decision_indices) == 1, (
        f"expected exactly one DECISION event, found {len(decision_indices)} in {events!r}"
    )

    # The relative-strength VERIFICATION_STEP precedes the DECISION event (R9.6).
    assert rs_indices[0] < decision_indices[0], (
        f"relative-strength step at index {rs_indices[0]} must precede DECISION "
        f"at index {decision_indices[0]} in {events!r}"
    )
