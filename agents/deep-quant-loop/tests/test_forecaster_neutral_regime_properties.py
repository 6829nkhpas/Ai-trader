"""Property-based test for the neutral blend on a transitional / unavailable regime (forecaster.py, task 4.4).

Feature: volatility-aware-forecaster

This module implements design **Property 5: A transitional or unavailable regime
applies a neutral blend without raising**:

    For any candle sequence, when the Regime_Classifier reports a ``transitional``
    trend state OR returns an Unavailable_Marker (no usable trend state), the
    top-level ``forecaster.forecast`` MUST:

      * never raise an exception and never block (R2.4) — it produces either a
        well-formed Forecast_Label or an honest Unavailable_Marker (when there
        are too few valid candles), and
      * apply a NEUTRAL (unweighted) blend — i.e. the recorded
        ``measures.standardized_drift`` equals the unweighted standardized drift
        ``drift / volatility`` exactly (weight 1), with no trend-continuation
        amplification or mean-reversion dampening.

    The same neutrality is confirmed directly at the blend level:
    ``conditioned_drift(drift, volatility, 'transitional', config)`` and
    ``conditioned_drift(drift, volatility, None, config)`` (None standing in for
    an unavailable regime) BOTH equal the unweighted value ``drift / volatility``.

Validates: Requirements 2.4.

The regime classifier is patched (``forecaster.regime.classify_regime``, the same
module object ``forecaster`` calls internally) to return, in turn, (a) a
``transitional`` Regime_Label and (b) an Unavailable_Marker, so the neutral-blend
path is exercised deterministically without relying on the candles happening to
classify as transitional. The candle generator, sys.path / import pattern, and
config-via-``resolve_forecaster_config()`` mirror the sibling
``test_forecaster_estimation_measures_properties.py`` and
``test_forecaster_regime_conditioning_properties.py`` modules.
"""

import math
import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import forecaster  # noqa: E402
from forecaster import (  # noqa: E402
    compute_drift,
    compute_volatility,
    conditioned_drift,
    resolve_forecaster_config,
)

_FORECAST_DIRECTIONS = {"up", "down", "flat"}
_ALIGNMENT_VALUES = {"aligned", "misaligned", "neutral"}
_PROJECTION_FIELDS = (
    "projected_direction",
    "up_probability",
    "expected_move_atr",
    "forecast_confidence",
    "forecast_alignment",
)

# A transitional Regime_Label (only ``trend_state`` is consulted by the
# forecaster; the rest mirrors a realistic regime label shape).
_TRANSITIONAL_LABEL = {
    "trend_state": "transitional",
    "volatility_state": "normal",
    "favorability": "neutral",
    "measures": {},
    "candles_used": 0,
}
# An Unavailable_Marker from the classifier (no usable trend state -> neutral).
_REGIME_UNAVAILABLE_MARKER = {"unavailable": True, "reason": "test: regime unavailable"}


@st.composite
def _random_walk_candles(draw):
    """A varied random price-walk sequence of dict OHLCV candles."""
    n = draw(st.integers(min_value=0, max_value=80))
    price = draw(st.floats(min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False))
    candles = []
    for _ in range(n):
        step = draw(st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False))
        new_price = max(price + step, 1.0)
        open_ = price
        close = new_price
        high = max(open_, close) + draw(
            st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)
        )
        low = max(
            min(open_, close)
            - draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)),
            0.5,
        )
        candles.append({"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0})
        price = new_price
    return candles


@st.composite
def _flat_window_candles(draw):
    """A degenerate, zero-variance window: every candle shares one close price."""
    n = draw(st.integers(min_value=1, max_value=50))
    flat = draw(st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False))
    return [
        {"open": flat, "high": flat, "low": flat, "close": flat, "volume": 1000.0}
        for _ in range(n)
    ]


# Span the valid / varied / degenerate / insufficient candle space.
_candles = st.one_of(
    _random_walk_candles(),
    _flat_window_candles(),
)


def _is_finite_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _assert_label_or_marker_well_formed(result):
    """A neutral-regime forecast is either a well-formed label or an honest marker."""
    assert isinstance(result, dict)
    if result.get("unavailable"):
        # Insufficient-candle marker: fabricated projection fields are omitted.
        for field in _PROJECTION_FIELDS:
            assert field not in result, f"unavailable marker fabricated {field!r}"
        return False
    # Well-formed Forecast_Label.
    assert result["projected_direction"] in _FORECAST_DIRECTIONS
    assert _is_finite_number(result["up_probability"]) and 0.0 <= result["up_probability"] <= 1.0
    ema = result["expected_move_atr"]
    assert ema is None or _is_finite_number(ema)
    assert _is_finite_number(result["forecast_confidence"]) and 0.0 <= result["forecast_confidence"] <= 1.0
    assert result["forecast_alignment"] in _ALIGNMENT_VALUES
    assert "measures" in result and isinstance(result["measures"], dict)
    assert _is_finite_number(result["measures"]["standardized_drift"])
    return True


def _assert_neutral_blend_applied(candles, label, config, expected_trend_state):
    """The label's standardized_drift equals the UNWEIGHTED drift / volatility (neutral blend)."""
    # The forecaster records the regime trend state it conditioned on.
    assert label["regime_trend_state"] == expected_trend_state

    z = label["measures"]["standardized_drift"]
    drift = compute_drift(candles, config)
    volatility = compute_volatility(candles, config)

    if drift is not None and volatility is not None and math.isfinite(volatility) and volatility > 0.0:
        # Positive-variance window: neutral blend == unweighted standardized drift.
        base = drift / volatility
        assert math.isclose(z, base, rel_tol=1e-9, abs_tol=1e-12), (
            f"standardized_drift {z!r} != unweighted drift/vol {base!r} for {expected_trend_state}"
        )
        # Confirm directly at the blend level: 'transitional' and None (unavailable)
        # both equal the neutral / unweighted value (the heart of Property 5).
        assert math.isclose(
            conditioned_drift(drift, volatility, "transitional", config), base,
            rel_tol=1e-9, abs_tol=1e-12,
        )
        assert math.isclose(
            conditioned_drift(drift, volatility, None, config), base,
            rel_tol=1e-9, abs_tol=1e-12,
        )
    else:
        # Zero-variance / degenerate window: the forecast short-circuits to a flat,
        # maximally-uncertain forecast (z == 0) rather than dividing by zero.
        assert z == 0.0
        assert label["projected_direction"] == "flat"
        assert label["up_probability"] == 0.5
        assert label["forecast_confidence"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Property 5 (task 4.4): A transitional or unavailable regime applies a neutral
# blend without raising
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 5: A transitional or unavailable regime applies a neutral blend without raising
@settings(max_examples=150, deadline=None)
@given(candles=_candles)
def test_property_5_transitional_or_unavailable_regime_applies_neutral_blend(candles):
    """Feature: volatility-aware-forecaster, Property 5: A transitional or
    unavailable regime applies a neutral blend without raising.

    With the Regime_Classifier reporting a transitional trend state, and again
    with it returning an Unavailable_Marker, ``forecast`` never raises and emits
    a well-formed label (or an honest insufficient-data marker) whose
    standardized drift equals the unweighted ``drift / volatility`` — proving the
    neutral (weight 1) blend was applied. ``conditioned_drift`` with
    ``'transitional'`` and with ``None`` both equal that same unweighted value.

    Validates: Requirements 2.4
    """
    config = resolve_forecaster_config()

    # ── (a) Transitional regime -> neutral blend, never raises (R2.4) ────────
    with mock.patch.object(
        forecaster.regime, "classify_regime", return_value=dict(_TRANSITIONAL_LABEL)
    ):
        result = forecaster.forecast(candles, config, proposed_direction="up")
    is_label = _assert_label_or_marker_well_formed(result)
    if is_label:
        _assert_neutral_blend_applied(candles, result, config, "transitional")

    # ── (b) Unavailable regime -> neutral blend, never raises (R2.4) ─────────
    with mock.patch.object(
        forecaster.regime, "classify_regime", return_value=dict(_REGIME_UNAVAILABLE_MARKER)
    ):
        result = forecaster.forecast(candles, config, proposed_direction="up")
    is_label = _assert_label_or_marker_well_formed(result)
    if is_label:
        # An unavailable regime is recorded as the "unavailable" trend state but
        # still blended neutrally (weight 1).
        _assert_neutral_blend_applied(candles, result, config, "unavailable")
