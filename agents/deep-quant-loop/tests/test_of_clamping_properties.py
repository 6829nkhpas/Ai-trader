"""Property-based test for bounded-measure clamping (order_flow.py, task 2.3).

Feature: order-flow-context

This module implements design **Property 4: Bounded measures are clamped within
their range**:

    A bounded Order_Flow_Proxy_Measure is clamped to the nearest boundary value
    whenever its raw computed value would otherwise fall outside the range. The
    close-location value is defined on the closed range ``[-1.0, 1.0]`` and the
    buying-pressure ratio is defined on ``[0.0, 1.0]``; whenever
    ``compute_close_location_value`` / ``compute_buying_pressure_ratio`` return a
    non-``None`` value, that value is a finite number lying within its defined
    bounds — even for extreme, near-degenerate, inverted-range, or
    floating-point-edge candle inputs whose raw computation could otherwise drift
    just outside the interval.

Validates: Requirements 4.4.

Candles are dict-like OHLCV records with keys ``open`` / ``high`` / ``low`` /
``close`` / ``volume`` (matching how ``order_flow.py`` reads candles via
``c.get(...)``). The generators below produce arbitrary candle sequences,
including extreme magnitudes, near-zero ranges, flat windows, and inverted
high/low values, so the clamping guarantee is stressed across the degenerate
input space. The sys.path / import pattern mirrors
``tests/test_of_config_default_fallback_properties.py`` and the
candle-generation approach mirrors ``tests/test_regime_measures_properties.py``.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import (  # noqa: E402
    compute_buying_pressure_ratio,
    compute_close_location_value,
)

# ─────────────────────────────────────────────────────────────────────────────
# Candle generation: arbitrary OHLCV records, including extreme / degenerate values
# ─────────────────────────────────────────────────────────────────────────────

# A pool of price values spanning ordinary, extreme, near-zero, and zero
# magnitudes so the bounded measures are stressed at the edges of their domain,
# where floating-point error could push a raw value marginally outside its range.
_PRICE = st.one_of(
    st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1e-9, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([0.0, 1e-12, 1e12, 1.0, 100.0, 12345.6789]),
)

_VOLUME = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)


@st.composite
def _candle(draw):
    """One OHLCV candle dict with possibly extreme / inconsistent values.

    ``high``/``low`` are NOT forced to bracket ``open``/``close``; this is
    intentional so the generator also produces degenerate (e.g. inverted-range
    or flat) candles that drive the raw close-location value toward or past its
    bounds, exercising the clamping guarantee.
    """
    return {
        "open": draw(_PRICE),
        "high": draw(_PRICE),
        "low": draw(_PRICE),
        "close": draw(_PRICE),
        "volume": draw(_VOLUME),
    }


@st.composite
def _flat_candle(draw):
    """A flat candle where O=H=L=C (a zero-range, degenerate bar — CLV is None)."""
    p = draw(_PRICE)
    return {"open": p, "high": p, "low": p, "close": p, "volume": draw(_VOLUME)}


# Sequences mixing arbitrary and flat candles so both ordinary directional
# windows and zero-range / zero-directional-volume windows are covered.
_CANDLES = st.lists(
    st.one_of(_candle(), _flat_candle()),
    min_size=1,
    max_size=160,
)

# A lookback small enough to be satisfiable by the generated sequences but large
# enough to exercise multi-bar windows.
_LOOKBACK = st.integers(min_value=2, max_value=40)


# ─────────────────────────────────────────────────────────────────────────────
# Property 4: Bounded measures are clamped within their range
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 4: Bounded measures are clamped within their range
@settings(max_examples=200, deadline=None)
@given(candles=_CANDLES, lookback=_LOOKBACK)
def test_property_4_bounded_measures_are_clamped(candles, lookback):
    """Feature: order-flow-context, Property 4: Bounded measures are clamped
    within their range.

    For any candle sequence (including extreme / near-zero / inverted-range /
    flat candles), whenever a bounded Order_Flow_Proxy_Measure returns a
    non-``None`` value it is a finite number lying within its defined bounds:
      * close-location value   in [-1.0, 1.0]   (per candle)
      * buying-pressure ratio  in [0.0, 1.0]    (over the lookback)

    Validates: Requirements 4.4
    """
    # The close-location value is bounded per-candle to [-1.0, 1.0].
    for candle in candles:
        clv = compute_close_location_value(candle)
        if clv is not None:
            assert math.isfinite(clv), f"close-location value not finite: {clv!r}"
            assert -1.0 <= clv <= 1.0, f"close-location value {clv!r} outside [-1.0, 1.0]"

    # The buying-pressure ratio is bounded over the lookback window to [0.0, 1.0].
    ratio = compute_buying_pressure_ratio(candles, lookback)
    if ratio is not None:
        assert math.isfinite(ratio), f"buying-pressure ratio not finite: {ratio!r}"
        assert 0.0 <= ratio <= 1.0, f"buying-pressure ratio {ratio!r} outside [0.0, 1.0]"
