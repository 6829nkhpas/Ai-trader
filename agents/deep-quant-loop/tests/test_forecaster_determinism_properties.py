"""Property-based test for forecast determinism (forecaster.py, task 4.2).

Feature: volatility-aware-forecaster

This module implements design **Property 1: Forecast is deterministic**:

    For any candle sequence — sufficient, insufficient, or degenerate/flat — and
    an optional proposed trade direction, invoking ``forecast(candles, config,
    proposed_direction, symbol, timeframe)`` two or more times with element-wise
    identical inputs returns an identical result: an identical ``Forecast_Label``
    (every field, measure, alignment, regime trend state) or an identical
    ``Unavailable_Marker`` (Requirements 1.4, 4.6).

Validates: Requirements 1.4, 4.6.

Candles are generated as dict-like OHLCV records with ``open`` / ``high`` /
``low`` / ``close`` / ``volume`` keys, exactly as ``forecaster.py`` reads them
through ``regime``'s validation helpers. The generator deliberately spans
varied random price walks (frequently long enough to reach the
``Forecast_Label`` path), short / insufficient sequences (driving the
``Unavailable_Marker`` path), and degenerate flat (zero-variance) windows (the
``flat`` / ``0.5`` / ``0.0`` short-circuit). An optional ``proposed_direction``
exercises the alignment derivation. Determinism is asserted by calling
``forecast`` on independent deep copies of equal inputs and requiring deep
equality of the results.

The sys.path / import pattern mirrors the sibling
``test_forecaster_estimation_measures_properties.py`` and
``test_regime_determinism_properties.py`` modules.
"""

import copy
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from forecaster import forecast, resolve_forecaster_config  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

_PRICE = st.floats(min_value=0.5, max_value=1e5, allow_nan=False, allow_infinity=False)
_SPAN = st.floats(min_value=0.0, max_value=5e3, allow_nan=False, allow_infinity=False)
_VOLUME = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)


@st.composite
def _candle(draw, close_override=None):
    """One dict-like OHLCV candle with ``high >= low`` and ``low <= open,close <= high``."""
    low = draw(_PRICE)
    high = low + draw(_SPAN)
    if close_override is not None:
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
    """A varied random price-walk sequence, frequently long enough to forecast."""
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
    flat_close = draw(st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False))
    return [draw(_candle(close_override=flat_close)) for _ in range(n)]


# A candle sequence that is one of: a varied random walk (reaches the label
# path), a list of arbitrary independent candles (short -> insufficient marker,
# plus degenerate high==low bars), or a flat zero-variance window. This spans
# the sufficient / insufficient / flat space the property requires.
_candles = st.one_of(
    _random_walk_candles(),
    st.lists(_candle(), min_size=0, max_size=80),
    _flat_window_candles(),
)

# An optional proposed trade direction exercising the alignment derivation; the
# blank / None cases drive the neutral path.
_proposed_direction = st.one_of(
    st.none(),
    st.sampled_from(["up", "down", "buy", "sell", "long", "short", "hold", "", "  ", "BUY", "Sell"]),
)


def _deep_equal(a, b):
    """Structural equality that treats NaN floats as equal to NaN.

    Forecast measures are a finite number or ``None`` by construction, so a plain
    ``==`` suffices; this helper additionally treats two NaNs as equal purely as
    a defensive guard so a (non-)deterministic NaN would still be caught as a
    *difference* rather than masked by ``nan != nan``.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_deep_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return a == b
    return a == b


# ─────────────────────────────────────────────────────────────────────────────
# Property 1: Forecast is deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 1: Forecast is deterministic
@settings(max_examples=200, deadline=None)
@given(candles=_candles, proposed_direction=_proposed_direction)
def test_property_1_forecast_is_deterministic(candles, proposed_direction):
    """Feature: volatility-aware-forecaster, Property 1: Forecast is deterministic.

    Invoking ``forecast`` two or more times with element-wise identical candles
    and identical configuration / proposed direction returns an identical
    ``Forecast_Label`` (or an identical ``Unavailable_Marker``).

    Validates: Requirements 1.4, 4.6
    """
    config = resolve_forecaster_config()

    # Independent deep copies of equal inputs so determinism is asserted across
    # element-wise-identical (but distinct) candle sequences, and so a mutation
    # of one call's input could not silently feed the next.
    candles_a = copy.deepcopy(candles)
    candles_b = copy.deepcopy(candles)
    candles_c = copy.deepcopy(candles)

    first = forecast(
        candles_a, config, proposed_direction=proposed_direction,
        symbol="RELIANCE", timeframe="15m",
    )
    second = forecast(
        candles_b, config, proposed_direction=proposed_direction,
        symbol="RELIANCE", timeframe="15m",
    )
    third = forecast(
        candles_c, config, proposed_direction=proposed_direction,
        symbol="RELIANCE", timeframe="15m",
    )

    assert first == second, (
        f"non-deterministic across invocations:\n first={first!r}\n second={second!r}"
    )
    assert _deep_equal(first, second), (
        f"non-deterministic (deep) across invocations:\n first={first!r}\n second={second!r}"
    )
    assert _deep_equal(second, third), (
        f"non-deterministic (deep) across invocations:\n second={second!r}\n third={third!r}"
    )

    # Determinism must also hold for the bare (no symbol/timeframe) call shape.
    bare_first = forecast(copy.deepcopy(candles), config, proposed_direction=proposed_direction)
    bare_second = forecast(copy.deepcopy(candles), config, proposed_direction=proposed_direction)
    assert bare_first == bare_second, (
        f"non-deterministic (bare call):\n first={bare_first!r}\n second={bare_second!r}"
    )
