"""Property-based test for Expected_Move_ATR = signed move / ATR, null when unusable (forecaster.py, task 4.5).

Feature: volatility-aware-forecaster

This module implements design **Property 8: Expected_Move_ATR equals the signed
move over ATR, and is null exactly when ATR is unusable**:

    For any candle sequence that yields a Forecast_Label, the label's
    ``expected_move_atr`` equals the expected *signed* next-bar price move sized
    in ATR units:

        expected_move_atr = last_close * (exp(drift) - 1) / atr

    where ``drift = compute_drift(candles, config)``, ``atr =
    compute_atr(candles, config.atr_period)``, and ``last_close`` is the close of
    the most recent valid candle (R3.3). It is ``null`` exactly when the ATR
    denominator is unusable — ``compute_atr`` returns ``None`` (insufficient
    candles or a zero, flat range) — and, more generally, whenever the drift is
    missing/non-finite, no positive reference close exists, or the result is
    non-finite (the helper ``_expected_move_atr`` contract).

Validates: Requirements 3.3.

The recomputation is independent: it re-derives ``drift``, ``atr``, and
``last_close`` from the same public ``compute_*`` functions and ``regime``
validation helpers the forecaster uses, then mirrors the exact
``last_close * (exp(drift) - 1) / atr`` formula and compares with
``math.isclose``. Candle sequences span sufficient random walks (which yield a
label with a usable ATR and drift), degenerate flat / zero-range windows (which
make the ATR unusable, forcing ``null``), and short sequences (which yield an
Unavailable_Marker and are skipped). The sys.path / import pattern mirrors the
sibling forecaster property modules.
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

import regime  # noqa: E402
from forecaster import (  # noqa: E402
    compute_atr,
    compute_drift,
    forecast,
    resolve_forecaster_config,
)

# Float comparison tolerance for the recomputed signed-move-over-ATR value.
_REL_TOL = 1e-9
_ABS_TOL = 1e-12


@st.composite
def _random_walk_candles(draw):
    """A varied random price-walk sequence long enough to yield a label.

    >= 30 candles clears the default sufficiency gate (max(min_candles=30,
    largest_lookback=21)), and the wick spans keep the ATR positive so the usual
    case has a usable ATR and a usable drift.
    """
    n = draw(st.integers(min_value=30, max_value=80))
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
    """A degenerate, zero-variance / zero-range window of >= 30 candles.

    Every bar shares one flat close with high == low == close, so the ATR true
    ranges are all zero and ``compute_atr`` returns ``None`` (an unusable ATR),
    forcing ``expected_move_atr`` to be ``null``.
    """
    n = draw(st.integers(min_value=30, max_value=60))
    flat_close = draw(st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False))
    return [
        {"open": flat_close, "high": flat_close, "low": flat_close, "close": flat_close, "volume": 1000.0}
        for _ in range(n)
    ]


@st.composite
def _short_candles(draw):
    """A short sequence (< 30 candles) that yields an Unavailable_Marker."""
    n = draw(st.integers(min_value=0, max_value=20))
    price = draw(st.floats(min_value=10.0, max_value=1000.0, allow_nan=False, allow_infinity=False))
    candles = []
    for _ in range(n):
        step = draw(st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False))
        new_price = max(price + step, 1.0)
        candles.append(
            {
                "open": price,
                "high": max(price, new_price) + 1.0,
                "low": max(min(price, new_price) - 1.0, 0.5),
                "close": new_price,
                "volume": 1000.0,
            }
        )
        price = new_price
    return candles


_candles = st.one_of(
    _random_walk_candles(),
    _flat_window_candles(),
    _short_candles(),
)


def _recompute_expected_move_atr(candles, config):
    """Independently recompute the expected signed next-bar move in ATR units.

    Mirrors ``forecaster._expected_move_atr`` exactly using the public
    ``compute_drift`` / ``compute_atr`` functions and the same ``regime`` valid-
    row helper for the reference close. Returns ``None`` under the same unusable
    conditions (no/zero ATR, missing/non-finite drift, no positive close, or a
    non-finite result).
    """
    atr = compute_atr(candles, config.atr_period)
    if atr is None or not math.isfinite(atr) or atr <= 0.0:
        return None
    drift = compute_drift(candles, config)
    if drift is None or not math.isfinite(drift):
        return None
    rows = regime._valid_ohlc_rows(candles)
    if not rows:
        return None
    last_close = rows[-1][3]
    if not math.isfinite(last_close) or last_close <= 0.0:
        return None
    try:
        expected_price_move = last_close * (math.exp(drift) - 1.0)
    except OverflowError:
        return None
    if not math.isfinite(expected_price_move):
        return None
    value = expected_price_move / atr
    if not math.isfinite(value):
        return None
    return value


# ─────────────────────────────────────────────────────────────────────────────
# Property 8 (task 4.5): Expected_Move_ATR equals the signed move over ATR, and
# is null exactly when ATR is unusable
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 8: Expected_Move_ATR equals the signed move over ATR, and is null exactly when ATR is unusable
@settings(max_examples=200, deadline=None)
@given(candles=_candles)
def test_property_8_expected_move_atr_equals_signed_move_over_atr(candles):
    """Feature: volatility-aware-forecaster, Property 8: Expected_Move_ATR equals
    the signed move over ATR, and is null exactly when ATR is unusable.

    For a produced Forecast_Label, the label's ``expected_move_atr`` equals the
    independently recomputed ``last_close * (exp(drift) - 1) / atr`` (within
    tolerance) when the ATR is usable, and is ``None`` exactly when
    ``compute_atr`` reports the ATR unusable (``None`` / zero range).

    Validates: Requirements 3.3
    """
    config = resolve_forecaster_config()

    result = forecast(candles, config)
    assert isinstance(result, dict), f"forecast must return a dict, got {result!r}"

    # An Unavailable_Marker (insufficient candles) carries no expected_move_atr
    # field at all — nothing to check for this property; skip it.
    if result.get("unavailable"):
        assert "expected_move_atr" not in result, (
            "an Unavailable_Marker must not carry a fabricated expected_move_atr"
        )
        return

    # A produced Forecast_Label always carries the field.
    assert "expected_move_atr" in result, "a Forecast_Label must carry expected_move_atr"
    actual = result["expected_move_atr"]

    atr = compute_atr(candles, config.atr_period)
    expected = _recompute_expected_move_atr(candles, config)

    # ── Null exactly when the ATR denominator is unusable ────────────────────
    if atr is None:
        assert actual is None, (
            f"expected_move_atr must be null when the ATR is unusable, got {actual!r}"
        )

    # ── Matches the independent recomputation (value or null) ────────────────
    if expected is None:
        assert actual is None, (
            f"expected_move_atr must be null when unusable, got {actual!r}"
        )
    else:
        assert actual is not None, (
            "expected_move_atr must be a value when the ATR and drift are usable"
        )
        assert isinstance(actual, float) and math.isfinite(actual), (
            f"expected_move_atr must be a finite float, got {actual!r}"
        )
        assert math.isclose(actual, expected, rel_tol=_REL_TOL, abs_tol=_ABS_TOL), (
            f"expected_move_atr {actual!r} != recomputed signed-move/ATR {expected!r}"
        )
