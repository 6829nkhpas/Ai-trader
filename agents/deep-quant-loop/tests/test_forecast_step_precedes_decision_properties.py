"""Property-based test for forecast verification-step / decision ordering
(stream_events.py, task 11.3).

Feature: volatility-aware-forecaster

This module implements design **Property 27: The forecast verification step
precedes the DECISION event**:

    For any decision, the event sequence emitted by ``decision_events`` places
    the forecast ``VERIFICATION_STEP`` (the step carrying the stable check id
    ``forecast``) BEFORE the ``DECISION`` event of that run.

Validates: Requirements 10.6.

The implementation under test lives in ``stream_events.py``:
  - ``decision_events(decision)`` — yields ``(event_name, payload)`` tuples:
    every ``VERIFICATION_STEP`` (built by ``build_verification_steps``) first,
    then the single ``DECISION`` event (built by ``build_decision_event``).
  - ``build_verification_steps`` derives exactly one forecast step from the
    decision's ``defensibility`` record via ``_forecast_step`` (check id
    ``forecast``), in both FIND mode (no ``validator_checks``) and VERIFY mode
    (``validator_checks`` present).

No LLM / graph / Rust server is invoked — ``decision_events`` is a pure function
of the decision dict. The decision's ``defensibility`` record is generated with
a ``forecast`` entry in both the available form (carrying a Forecast_Alignment)
and the unavailable form (``available`` falsy / a non-dict / missing / bad
alignment), covering the full outcome space (R10.2-R10.5). ``validator_checks``
are sometimes included so the VERIFY-mode branch of ``build_verification_steps``
is exercised too.

The sys.path / import pattern mirrors
``tests/test_rs_step_precedes_decision_properties.py``: the service directory
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

FORECAST_CHECK_ID = "forecast"


# ── Strategies ───────────────────────────────────────────────────────────────
_projected_direction = st.sampled_from(["up", "down", "flat"])
_alignment = st.sampled_from(["aligned", "misaligned", "neutral"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# A measure value is a finite number or null (None), per the forecast contract.
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)

_probability = st.floats(
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _available_forecast_entry(draw):
    """An available forecast entry carrying a Forecast_Alignment (usable label)."""
    return {
        "available": True,
        "forecast_alignment": draw(_alignment),
        "projected_direction": draw(_projected_direction),
        "up_probability": draw(_probability),
        "expected_move_atr": draw(_measure_value),
        "forecast_confidence": draw(_probability),
        "measures": {
            "drift": draw(_measure_value),
            "volatility": draw(_measure_value),
            "standardized_drift": draw(_measure_value),
            "atr": draw(_measure_value),
        },
    }


# Unavailable forecast: an explicit marker, a non-dict, a missing entry, or an
# available entry with an unrecognized alignment — all of which drive the
# forecast step to ``not-evaluable`` (R10.5) without changing the ordering
# guarantee under test.
_unavailable_forecast_entry = st.one_of(
    st.just({"available": False, "reason": "insufficient valid candles: 5 < 30 required"}),
    st.just({"available": False}),
    st.just({"available": True, "forecast_alignment": "unrecognized-value"}),  # bad alignment
    st.none(),  # entry omitted entirely
    st.just("not-a-dict"),  # non-dict entry
)

_forecast_entry = st.one_of(_available_forecast_entry(), _unavailable_forecast_entry)


@st.composite
def _validator_check(draw):
    """A single VERIFY-mode validator check (never the forecast check itself)."""
    return {
        "check": draw(st.sampled_from(["risk-reward", "volatility-stop", "level-alignment"])),
        "outcome": draw(st.sampled_from(["pass", "fail", "informational"])),
    }


@st.composite
def _decision(draw):
    """Draw a decision dict whose defensibility record carries a forecast entry."""
    forecast = draw(_forecast_entry)
    record = {}
    if forecast is not None:
        record["forecast"] = forecast

    # Sometimes include VERIFY-mode validator_checks so build_verification_steps
    # exercises the VERIFY branch (which appends the forecast step) as well as
    # the FIND branch (which derives all checks including the forecast step).
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
# Property 27: the forecast verification step precedes the DECISION event
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 27: The forecast verification step precedes the DECISION event
@settings(max_examples=200, deadline=None)
@given(decision=_decision())
def test_property_27_forecast_step_precedes_decision_event(decision):
    """Validates: Requirements 10.6

    For any decision, ``decision_events`` emits exactly one forecast
    ``VERIFICATION_STEP`` (check id ``forecast``) and exactly one ``DECISION``
    event, and the forecast step is ordered strictly before the ``DECISION``
    event of that run.
    """
    events = list(decision_events(decision))

    # Indices of the forecast VERIFICATION_STEP(s) and the DECISION event(s).
    forecast_indices = [
        i
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == FORECAST_CHECK_ID
    ]
    decision_indices = [i for i, (name, _) in enumerate(events) if name == DECISION]

    # Exactly one forecast step exists (R10.1) and exactly one DECISION.
    assert len(forecast_indices) == 1, (
        f"expected exactly one forecast step, found {len(forecast_indices)} in {events!r}"
    )
    assert len(decision_indices) == 1, (
        f"expected exactly one DECISION event, found {len(decision_indices)} in {events!r}"
    )

    # The forecast VERIFICATION_STEP precedes the DECISION event (R10.6).
    assert forecast_indices[0] < decision_indices[0], (
        f"forecast step at index {forecast_indices[0]} must precede DECISION "
        f"at index {decision_indices[0]} in {events!r}"
    )
