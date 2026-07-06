"""Property-based test for the zero-variance short-circuit (forecaster.py, task 4.7).

Feature: volatility-aware-forecaster

This module implements design **Property 14: A zero-variance window yields
flat / 0.5 / 0.0 without dividing by zero**:

    For any flat, zero-variance candle window that carries *enough* valid
    candles to pass the sufficiency gate — every candle sharing one constant
    price (``high == low == close == const``) so the Volatility_Estimate is
    ``0`` — ``forecast(candles, config, ...)`` short-circuits to a flat,
    maximally-uncertain forecast: ``projected_direction == "flat"``,
    ``up_probability == 0.5``, ``forecast_confidence == 0.0``, and the
    standardized-drift measure ``measures["standardized_drift"] == 0.0``. It
    never divides by zero and never raises (Requirement 4.5).

Validates: Requirements 4.5.

Candles are generated as dict-like OHLCV records with ``open`` / ``high`` /
``low`` / ``close`` / ``volume`` keys, exactly as ``forecaster.py`` reads them
through ``regime``'s validation helpers. The generator builds flat windows of
at least the required candle count (``max(min_candles, largest_lookback)``;
with the defaults that is 30, so ``>= 31`` candles are generated to clear the
gate) where every candle is identical, and varies both the constant price and
the candle count. The sys.path / import pattern mirrors the sibling
``test_forecaster_determinism_properties.py`` and
``test_forecaster_estimation_measures_properties.py`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from forecaster import forecast, resolve_forecaster_config  # noqa: E402

# The required valid-candle count for the gate with the resolved configuration.
# Generating at least this many flat candles guarantees the sufficiency gate
# passes and the forecast reaches the zero-variance short-circuit rather than an
# Unavailable_Marker. Computed once from the resolved config so the test stays
# correct if the documented defaults change.
_CONFIG = resolve_forecaster_config()
_REQUIRED = max(_CONFIG.min_candles, _CONFIG.largest_lookback)

# A constant price for the flat window. Strictly positive (a non-positive close
# would make the log-returns undefined) and finite.
_CONST_PRICE = st.floats(
    min_value=0.5, max_value=1e5, allow_nan=False, allow_infinity=False
)
# Candle count comfortably above the gate (>= required + 1) so the window always
# clears the sufficiency gate while still varying the length.
_CANDLE_COUNT = st.integers(min_value=_REQUIRED + 1, max_value=_REQUIRED + 60)

_PROPOSED_DIRECTION = st.one_of(
    st.none(),
    st.sampled_from(["up", "down", "buy", "sell", "long", "short", "hold", "", "BUY"]),
)


def _flat_window(const: float, count: int) -> list:
    """A zero-variance window: ``count`` identical bars at a single price.

    Every OHLCV field equals ``const`` (``high == low == open == close``), so the
    window has no return dispersion at all — the Volatility_Estimate is ``0``.
    """
    return [
        {"open": const, "high": const, "low": const, "close": const, "volume": 1000.0}
        for _ in range(count)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Property 14: A zero-variance window yields flat / 0.5 / 0.0 without dividing by zero
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 14: A zero-variance window yields flat / 0.5 / 0.0 without dividing by zero
@settings(max_examples=200, deadline=None)
@given(const=_CONST_PRICE, count=_CANDLE_COUNT, proposed_direction=_PROPOSED_DIRECTION)
def test_property_14_zero_variance_window_flat_half_zero(const, count, proposed_direction):
    """Feature: volatility-aware-forecaster, Property 14: A zero-variance window
    yields flat / 0.5 / 0.0 without dividing by zero.

    A flat window of enough identical candles to pass the sufficiency gate
    produces a Forecast_Label with ``projected_direction == "flat"``,
    ``up_probability == 0.5``, ``forecast_confidence == 0.0``, and
    ``measures["standardized_drift"] == 0.0``. ``forecast`` does not raise and
    never divides by zero.

    Validates: Requirements 4.5
    """
    config = resolve_forecaster_config()
    candles = _flat_window(const, count)

    # ``forecast`` must not raise on a zero-variance window (Requirement 4.5);
    # any exception would propagate out of this call and fail the test.
    result = forecast(
        candles,
        config,
        proposed_direction=proposed_direction,
        symbol="RELIANCE",
        timeframe="15m",
    )

    # With >= required identical candles the sufficiency gate passes, so the
    # result is a Forecast_Label (never an Unavailable_Marker).
    assert not result.get("unavailable"), (
        f"flat window of {count} candles (>= {_REQUIRED} required) should produce "
        f"a label, not a marker: {result!r}"
    )

    # The zero-variance short-circuit: flat / 0.5 / 0.0 (Requirement 4.5).
    assert result["projected_direction"] == "flat", (
        f"zero-variance window must project 'flat', got {result['projected_direction']!r}"
    )
    assert result["up_probability"] == 0.5, (
        f"zero-variance window must have up_probability 0.5, got {result['up_probability']!r}"
    )
    assert result["forecast_confidence"] == 0.0, (
        f"zero-variance window must have forecast_confidence 0.0, got "
        f"{result['forecast_confidence']!r}"
    )

    # The standardized-drift measure is exactly 0.0 — the short-circuit never
    # forms ``drift / volatility`` (no division by zero).
    assert result["measures"]["standardized_drift"] == 0.0, (
        f"zero-variance window must have standardized_drift 0.0, got "
        f"{result['measures']['standardized_drift']!r}"
    )
