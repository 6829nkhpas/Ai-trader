"""Property-based test for present, finite-or-null measures with non-negative volatility (forecaster.py, task 2.2).

Feature: volatility-aware-forecaster

This module implements design **Property 3: Drift and volatility measures are
present, finite-or-null, and volatility is non-negative**:

    For any candle sequence — valid, varied, or degenerate — the
    ``Volatility_Aware_Forecaster`` estimation functions emit honest measures:

      * ``compute_drift(candles, config)`` returns either ``None`` or a finite
        float (the Drift_Estimate from log-returns over the drift lookback)
        (R1.2, R4.3),
      * ``compute_volatility(candles, config)`` returns either ``None`` or a
        finite float that is STRICTLY NON-NEGATIVE (the dispersion measure over
        the volatility lookback) (R1.3, R4.3).

    Neither function raises on any input, and every emitted (non-null) measure is
    finite (R4.3).

Validates: Requirements 1.2, 1.3, 4.3.

Candles are generated as dict-like OHLCV records with ``open`` / ``high`` /
``low`` / ``close`` / ``volume`` keys, exactly as ``forecaster.py`` reads them
through ``regime``'s validation helpers. The generator deliberately spans
realistic random price walks, short/insufficient sequences, and degenerate
flat (zero-variance) windows so the ``None`` / zero / finite paths are all
exercised. The sys.path / import pattern mirrors the sibling
``test_forecaster_config_default_fallback_properties.py`` and
``test_of_measures_present_properties.py`` modules.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from forecaster import (  # noqa: E402
    compute_drift,
    compute_volatility,
    resolve_forecaster_config,
)

# Finite price magnitudes spanning ordinary and large values.
_PRICE = st.floats(min_value=0.5, max_value=1e5, allow_nan=False, allow_infinity=False)
# A non-negative wick span; 0 yields a degenerate high == low == open == close
# bar so the zero-range / zero-variance paths are exercised.
_SPAN = st.floats(min_value=0.0, max_value=5e3, allow_nan=False, allow_infinity=False)
_VOLUME = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)


@st.composite
def _candle(draw, close_override=None):
    """One dict-like OHLCV candle with ``high >= low`` and ``low <= open,close <= high``."""
    low = draw(_PRICE)
    high = low + draw(_SPAN)
    if close_override is not None:
        # Build a flat bar around a fixed close to support degenerate windows.
        o = c = close_override
        high = max(high, close_override)
        low = min(low, close_override)
    elif high <= low:
        o = c = low
    else:
        o = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
        c = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    return {"open": o, "high": high, "low": low, "close": c, "volume": draw(_VOLUME)}


@st.composite
def _random_walk_candles(draw):
    """A varied random price-walk sequence of dict OHLCV candles."""
    n = draw(st.integers(min_value=0, max_value=60))
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
    n = draw(st.integers(min_value=1, max_value=40))
    flat_close = draw(st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False))
    return [draw(_candle(close_override=flat_close)) for _ in range(n)]


# A candle sequence that is one of: a varied random walk, a list of arbitrary
# independent candles (including degenerate high==low bars), or a flat
# zero-variance window. This spans the valid / varied / degenerate space the
# property requires.
_candles = st.one_of(
    _random_walk_candles(),
    st.lists(_candle(), min_size=0, max_size=60),
    _flat_window_candles(),
)


def _is_finite_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ─────────────────────────────────────────────────────────────────────────────
# Property 3 (task 2.2): Drift and volatility measures are present, finite-or-null,
# and volatility is non-negative
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 3: Drift and volatility measures are present, finite-or-null, and volatility is non-negative
@settings(max_examples=200, deadline=None)
@given(candles=_candles)
def test_property_3_measures_present_finite_or_null_volatility_non_negative(candles):
    """Feature: volatility-aware-forecaster, Property 3: Drift and volatility
    measures are present, finite-or-null, and volatility is non-negative.

    For any candle sequence, ``compute_drift`` returns either ``None`` or a
    finite float, and ``compute_volatility`` returns either ``None`` or a finite
    float that is strictly non-negative. Neither function raises.

    Validates: Requirements 1.2, 1.3, 4.3
    """
    config = resolve_forecaster_config()

    # ── Drift_Estimate: None or a finite float (R1.2, R4.3) ──────────────────
    drift = compute_drift(candles, config)
    assert drift is None or _is_finite_number(drift), (
        f"compute_drift returned a non-finite, non-null value: {drift!r}"
    )

    # ── Volatility_Estimate: None or a finite, non-negative float (R1.3, R4.3) ─
    volatility = compute_volatility(candles, config)
    if volatility is not None:
        assert _is_finite_number(volatility), (
            f"compute_volatility returned a non-finite value: {volatility!r}"
        )
        assert volatility >= 0.0, (
            f"compute_volatility returned a negative value: {volatility!r}"
        )
