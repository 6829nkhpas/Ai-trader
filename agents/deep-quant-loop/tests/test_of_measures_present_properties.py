"""Property-based test for present, finite-or-null, correct proxy measures (order_flow.py, task 2.2).

Feature: order-flow-context

This module implements design **Property 3: Proxy measures are present,
finite-or-null, and correct**:

    Every candle-derived Order_Flow_Proxy_Measure is either a finite number or
    null (``None``), and each agrees with its mathematical definition recomputed
    independently from the same candles:

      * the close-location value is ``((close - low) - (high - close)) /
        (high - low)`` clamped to ``[-1.0, 1.0]``, or ``None`` when ``high ==
        low`` (R1.2),
      * the per-candle delta proxy is the close-location value times volume, or
        ``None`` when the close-location value is ``None`` (R1.2),
      * the CVD proxy is the running sum of the per-candle delta proxy over the
        last ``lookback`` valid candles (None-delta candles contribute 0) (R1.3),
      * the up-volume / down-volume are the summed volumes on candles closing
        above / below their open over the lookback (R1.4),
      * the buying-pressure ratio is ``up / (up + down)``, or ``None`` when the
        total directional volume is zero (R1.5),

    and each computed measure is finite (R4.3).

Validates: Requirements 1.2, 1.3, 1.4, 4.3.

Candles are generated as dict-like OHLCV records with ``open`` / ``high`` /
``low`` / ``close`` / ``volume`` keys, exactly as ``order_flow.py`` reads them
via ``candle.get(...)``. The sys.path / import pattern mirrors the sibling
``test_of_*_properties.py`` and ``test_rs_measures_properties.py`` modules.
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
    compute_candle_delta_proxy,
    compute_close_location_value,
    compute_cvd_proxy,
    compute_up_down_volume,
)

# Finite price magnitudes spanning ordinary and extreme values.
_PRICE = st.floats(min_value=0.5, max_value=1e6, allow_nan=False, allow_infinity=False)
# A non-negative span; 0 produces a degenerate (high == low) candle so the
# None / zero-range path (R1.2) is exercised too.
_SPAN = st.floats(min_value=0.0, max_value=1e5, allow_nan=False, allow_infinity=False)
_VOLUME = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)


@st.composite
def _candle(draw):
    """One dict-like OHLCV candle with ``high >= low`` and ``low <= open,close <= high``.

    Keeping ``open`` and ``close`` inside ``[low, high]`` mirrors real OHLCV data
    so the close-location value naturally lands in ``[-1, 1]`` (the clamp is a
    no-op there); ``span == 0`` yields a degenerate ``high == low`` candle that
    must produce a ``None`` close-location value and delta proxy.
    """
    low = draw(_PRICE)
    high = low + draw(_SPAN)
    if high <= low:
        o = c = low
    else:
        o = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
        c = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    return {"open": o, "high": high, "low": low, "close": c, "volume": draw(_VOLUME)}


def _is_finite_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _clamp(value, low, high):
    return low if value < low else high if value > high else value


def _expected_clv(candle):
    """Independent recomputation of the close-location value (R1.2)."""
    o, h, low, c = candle["open"], candle["high"], candle["low"], candle["close"]
    if h == low:
        return None
    return _clamp(((c - low) - (h - c)) / (h - low), -1.0, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Property 3 (task 2.2): Proxy measures are present, finite-or-null, and correct
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 3: Proxy measures are present, finite-or-null, and correct
@settings(max_examples=200, deadline=None)
@given(
    candles=st.lists(_candle(), min_size=1, max_size=60),
    lookback=st.integers(min_value=1, max_value=80),
)
def test_property_3_proxy_measures_present_finite_or_null_and_correct(candles, lookback):
    """Feature: order-flow-context, Property 3: Proxy measures are present,
    finite-or-null, and correct.

    Each proxy measure is either a finite number or ``None``, and matches its
    mathematical definition recomputed independently from the same candles.

    Validates: Requirements 1.2, 1.3, 1.4, 4.3
    """
    # ── Per-candle measures: close-location value and delta proxy (R1.2) ──────
    for candle in candles:
        clv = compute_close_location_value(candle)
        expected_clv = _expected_clv(candle)

        if expected_clv is None:
            # high == low -> the close-location value is null (R1.2).
            assert clv is None
        else:
            assert _is_finite_number(clv), f"clv not finite: {clv!r}"  # R4.3
            assert -1.0 <= clv <= 1.0, f"clv out of bounds: {clv!r}"
            assert math.isclose(clv, expected_clv, rel_tol=1e-9, abs_tol=1e-12)

        # Per-candle delta proxy = close-location value * volume; None when the
        # close-location value is None (R1.2).
        delta = compute_candle_delta_proxy(candle)
        if expected_clv is None:
            assert delta is None
        else:
            assert _is_finite_number(delta), f"delta not finite: {delta!r}"  # R4.3
            assert math.isclose(
                delta, expected_clv * candle["volume"], rel_tol=1e-9, abs_tol=1e-9
            )

    # ── CVD proxy: running sum of the per-candle delta over the lookback (R1.3) ─
    cvd = compute_cvd_proxy(candles, lookback)
    window = candles[-lookback:]
    expected_cvd = 0.0
    for candle in window:
        clv = _expected_clv(candle)
        if clv is not None:  # None-delta candles contribute 0 (R1.3)
            expected_cvd += clv * candle["volume"]
    # The window is non-empty (candles has >= 1 element) so a value is returned.
    assert _is_finite_number(cvd), f"cvd not finite: {cvd!r}"  # R4.3
    assert math.isclose(cvd, expected_cvd, rel_tol=1e-9, abs_tol=1e-6)

    # ── Up/down volume over the lookback (R1.4) ───────────────────────────────
    up_volume, down_volume = compute_up_down_volume(candles, lookback)
    expected_up = sum(c["volume"] for c in window if c["close"] > c["open"])
    expected_down = sum(c["volume"] for c in window if c["close"] < c["open"])
    assert _is_finite_number(up_volume) and _is_finite_number(down_volume)  # R4.3
    assert math.isclose(up_volume, expected_up, rel_tol=1e-9, abs_tol=1e-6)
    assert math.isclose(down_volume, expected_down, rel_tol=1e-9, abs_tol=1e-6)

    # ── Buying-pressure ratio = up / (up + down), null at zero denominator (R1.5) ─
    ratio = compute_buying_pressure_ratio(candles, lookback)
    total = expected_up + expected_down
    if total == 0:
        assert ratio is None
    else:
        assert _is_finite_number(ratio), f"ratio not finite: {ratio!r}"  # R4.3
        assert 0.0 <= ratio <= 1.0, f"ratio out of bounds: {ratio!r}"
        assert math.isclose(ratio, expected_up / total, rel_tol=1e-9, abs_tol=1e-12)
