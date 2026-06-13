"""Property-based test for the forecast verification step (task 11.2).

Feature: volatility-aware-forecaster

This module implements design **Property 26: Exactly one forecast verification
step with the correct outcome mapping**:

    For any defensibility record, building the verification steps yields EXACTLY
    ONE step carrying the stable check id ``forecast``, whose outcome maps the
    recorded Forecast_Alignment as:

        aligned      -> pass            (R10.2)
        misaligned   -> fail            (R10.3)
        neutral      -> informational   (R10.4)
        unavailable  -> not-evaluable (carrying an 'unavailable' indication;
                                       no fabricated alignment, R10.5)

    and the single step is present for every forecast shape (R10.1).

Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5.

The implementation under test lives in ``stream_events.py``:
  - ``build_verification_steps(decision)`` — FIND-mode records (no
    ``validator_checks``) route to ``_derive_find_mode_steps`` which appends
    exactly one ``_forecast_step(record)``.
  - ``_forecast_step(record)`` — maps the defensibility ``forecast`` entry to a
    single step under the fixed check id ``forecast``.

The real LLM / graph is never invoked. The defensibility ``forecast`` entry is
built directly in the shape ``graph._forecast_entry`` produces: a usable label
``{"available": True, "forecast_alignment": ..., "projected_direction": ...,
"up_probability": ..., ...}`` or an Unavailable_Marker
``{"available": False, "reason": ...}``.

The sys.path / import pattern mirrors ``tests/test_rs_verification_step_properties.py``:
the service directory (one level up) is prepended to ``sys.path`` so
``stream_events`` is importable when pytest is run from anywhere.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (stream_events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import build_verification_steps  # noqa: E402

FORECAST_CHECK = "forecast"

# The Forecast_Alignment -> outcome mapping the step must implement (R10.2-R10.4).
_ALIGNMENT_OUTCOME = {
    "aligned": "pass",
    "misaligned": "fail",
    "neutral": "informational",
}
# Outcomes that would betray a fabricated alignment on the unavailable path.
_FABRICATED_OUTCOMES = set(_ALIGNMENT_OUTCOME.values())


# ── Strategies ───────────────────────────────────────────────────────────────
_projected_direction = st.sampled_from(["up", "down", "flat"])
_probability = st.floats(min_value=0.0, max_value=1.0,
                         allow_nan=False, allow_infinity=False)
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _available_forecast_entry(draw):
    """A usable forecast entry across all three Forecast_Alignment values (R10.2-10.4)."""
    alignment = draw(st.sampled_from(["aligned", "misaligned", "neutral"]))
    return {
        "available": True,
        "forecast_alignment": alignment,
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


# An Unavailable_Marker entry: available False, only a reason (no fields, R10.5).
_unavailable_reason = st.one_of(
    st.none(),
    st.sampled_from(
        [
            "insufficient data: 12 valid candles available, 31 required",
            "candle retrieval timed out",
            "no usable returns could be computed",
            "zero-range candle window",
        ]
    ),
)
_unavailable_forecast_entry = st.builds(
    lambda reason: ({"available": False, "reason": reason} if reason is not None
                    else {"available": False}),
    _unavailable_reason,
)

# An "available but unrecognized alignment" entry: must be treated as
# unavailable (not-evaluable), never fabricated into a pass/fail/informational.
_malformed_forecast_entry = st.builds(
    lambda bad: {
        "available": True,
        "forecast_alignment": bad,
        "projected_direction": "up",
        "up_probability": 0.5,
    },
    st.sampled_from([None, "", "bullish", "AGREE", "unknown"]),
)

_forecast_entry = st.one_of(
    _available_forecast_entry(),
    _unavailable_forecast_entry,
    _malformed_forecast_entry,
)

# Optional FIND-mode record fields the other checks read. Their presence/absence
# must not affect the single forecast step. Crucially the record carries NO
# ``validator_checks`` so it routes through FIND mode.
_find_mode_extras = st.fixed_dictionaries(
    {},
    optional={
        "risk_reward": st.floats(min_value=0.0, max_value=10.0,
                                 allow_nan=False, allow_infinity=False),
        "volatility_basis": st.sampled_from(["stop >= 1.5x ATR", "n/a"]),
        "macro_trend_conflict": st.sampled_from(["Aligned with 1D trend", "n/a"]),
    },
)


def _only_forecast_step(steps):
    """Return the single forecast step, asserting exactly one (R10.1)."""
    fc_steps = [s for s in steps if s.get("check") == FORECAST_CHECK]
    assert len(fc_steps) == 1, (
        f"expected exactly one '{FORECAST_CHECK}' step, got {len(fc_steps)}"
    )
    return fc_steps[0]


# ─────────────────────────────────────────────────────────────────────────────
# Property 26: exactly one forecast verification step + outcome mapping
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 26: Exactly one forecast verification step with the correct outcome mapping
@settings(max_examples=200, deadline=None)
@given(
    forecast=_forecast_entry,
    extras=_find_mode_extras,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
)
def test_property_26_forecast_verification_step_outcome_mapping(forecast, extras, action):
    """Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5

    For any forecast entry shape (each Forecast_Alignment value, unavailable, or
    an available-but-unrecognized alignment), building the FIND-mode verification
    steps yields exactly ONE step with check id ``forecast`` whose outcome maps
    Forecast_Alignment correctly, and for the unavailable/unrecognized case
    carries an 'unavailable' indication with no fabricated alignment.
    """
    record = dict(extras)
    record["forecast"] = forecast
    decision = {"action": action, "defensibility": record}

    steps = build_verification_steps(decision)

    # ── R10.1: exactly one forecast step under the stable check id ───────────
    step = _only_forecast_step(steps)
    assert step["check"] == FORECAST_CHECK
    outcome = step.get("outcome")
    assert outcome  # always present

    if forecast.get("available") and forecast.get("forecast_alignment") in _ALIGNMENT_OUTCOME:
        # ── R10.2 / R10.3 / R10.4: alignment maps to the exact outcome ───────
        expected = _ALIGNMENT_OUTCOME[forecast["forecast_alignment"]]
        assert outcome == expected, (
            f"forecast_alignment={forecast['forecast_alignment']} -> outcome "
            f"{outcome!r}, expected {expected!r}"
        )
    else:
        # ── R10.5: unavailable -> not-evaluable, no fabricated alignment ─────
        assert outcome.startswith("not-evaluable"), (
            f"unavailable forecast must report not-evaluable, got {outcome!r}"
        )
        assert "unavailable" in outcome, (
            f"unavailable forecast outcome must carry an 'unavailable' "
            f"indication, got {outcome!r}"
        )
        # No fabricated pass/fail/informational outcome on the unavailable path.
        assert outcome not in _FABRICATED_OUTCOMES
        # And the step never invents an alignment field.
        assert "forecast_alignment" not in step
        assert "alignment" not in step
