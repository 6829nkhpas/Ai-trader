"""Property-based test for regime verification-step / decision ordering
(stream_events.py, task 10.3).

Feature: regime-detection-gate

This module implements design **Property 22: The regime verification step
precedes the DECISION event**:

    For any decision, the event sequence emitted by ``decision_events`` places
    the regime ``VERIFICATION_STEP`` (the step carrying the stable check id
    ``market-regime``) BEFORE the ``DECISION`` event of that run.

Validates: Requirements 8.6.

The implementation under test lives in ``stream_events.py``:
  - ``decision_events(decision)`` — yields ``(event_name, payload)`` tuples:
    every ``VERIFICATION_STEP`` (built by ``build_verification_steps``) first,
    then the single ``DECISION`` event (built by ``build_decision_event``).
  - ``build_verification_steps`` derives exactly one regime step from the
    decision's ``defensibility`` record via ``_regime_step`` (check id
    ``market-regime``), in both FIND mode (no ``validator_checks``) and VERIFY
    mode (``validator_checks`` present).

No LLM / graph / Rust server is invoked — ``decision_events`` is a pure function
of the decision dict. The decision's ``defensibility`` record is generated with
a ``regime`` entry in both the available form (carrying a favorability) and the
unavailable form (``available`` falsy / a non-dict / missing), covering the full
outcome space (R8.2-R8.5). ``validator_checks`` are sometimes included so the
VERIFY-mode branch of ``build_verification_steps`` is exercised too.

The sys.path / import pattern mirrors
``tests/test_regime_defensibility_mirror_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``stream_events`` is importable
when pytest is run from anywhere.
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

REGIME_CHECK_ID = "market-regime"


# ── Strategies ───────────────────────────────────────────────────────────────
_trend_state = st.sampled_from(["trending", "ranging", "transitional"])
_volatility_state = st.sampled_from(["low", "normal", "high"])
_favorability = st.sampled_from(["favorable", "unfavorable", "neutral"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# A measure value is a finite number or null (None), per the regime contract.
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _available_regime_entry(draw):
    """An available regime entry carrying a favorability (the usable label)."""
    return {
        "available": True,
        "favorability": draw(_favorability),
        "trend_state": draw(_trend_state),
        "volatility_state": draw(_volatility_state),
        "measures": {
            "directional_strength": draw(_measure_value),
            "choppiness": draw(_measure_value),
            "efficiency_ratio": draw(_measure_value),
            "atr_percentile": draw(_measure_value),
            "bb_width": draw(_measure_value),
        },
    }


# Unavailable regime: an explicit marker, a non-dict, or a missing entry — all
# of which drive the regime step to ``not-evaluable`` (R8.5) without changing
# the ordering guarantee under test.
_unavailable_regime_entry = st.one_of(
    st.just({"available": False, "reason": "insufficient data: 10 < 30 required"}),
    st.just({"available": False}),
    st.just({"favorability": "unrecognized-value", "available": True}),  # bad favorability
    st.none(),  # entry omitted entirely
    st.just("not-a-dict"),  # non-dict entry
)

_regime_entry = st.one_of(_available_regime_entry(), _unavailable_regime_entry)


@st.composite
def _validator_check(draw):
    """A single VERIFY-mode validator check (never the regime check itself)."""
    return {
        "check": draw(st.sampled_from(["risk-reward", "volatility-stop", "level-alignment"])),
        "outcome": draw(st.sampled_from(["pass", "fail", "informational"])),
    }


@st.composite
def _decision(draw):
    """Draw a decision dict whose defensibility record carries a regime entry."""
    regime = draw(_regime_entry)
    record = {}
    if regime is not None:
        record["regime"] = regime

    # Sometimes include VERIFY-mode validator_checks so build_verification_steps
    # exercises the VERIFY branch (which appends the regime step) as well as the
    # FIND branch (which derives all checks including the regime step).
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
# Property 22: the regime verification step precedes the DECISION event
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 22
@settings(max_examples=200, deadline=None)
@given(decision=_decision())
def test_property_22_regime_step_precedes_decision_event(decision):
    """Validates: Requirements 8.6

    For any decision, ``decision_events`` emits exactly one regime
    ``VERIFICATION_STEP`` (check id ``market-regime``) and exactly one
    ``DECISION`` event, and the regime step is ordered strictly before the
    ``DECISION`` event of that run.
    """
    events = list(decision_events(decision))

    # Indices of the regime VERIFICATION_STEP(s) and the DECISION event(s).
    regime_indices = [
        i
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == REGIME_CHECK_ID
    ]
    decision_indices = [i for i, (name, _) in enumerate(events) if name == DECISION]

    # Exactly one regime step exists (R8.1) and exactly one DECISION event.
    assert len(regime_indices) == 1, (
        f"expected exactly one regime step, found {len(regime_indices)} in {events!r}"
    )
    assert len(decision_indices) == 1, (
        f"expected exactly one DECISION event, found {len(decision_indices)} in {events!r}"
    )

    # The regime VERIFICATION_STEP precedes the DECISION event (R8.6).
    assert regime_indices[0] < decision_indices[0], (
        f"regime step at index {regime_indices[0]} must precede DECISION at "
        f"index {decision_indices[0]} in {events!r}"
    )
