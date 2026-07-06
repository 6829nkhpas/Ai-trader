"""Property-based test for insufficient-candle unavailability (regime.py, task 3.7).

Feature: regime-detection-gate

This module implements design **Property 8: Insufficient valid candles yield an
Unavailable_Marker with counts**:

    For any candle sequence whose count of valid candles is fewer than the
    configured minimum required for the longest lookback (whether short to begin
    with or short after excluding non-finite candles), ``classify_regime``
    returns an Unavailable_Marker whose reason identifies the insufficient-data
    condition and includes both the count of valid candles received and the
    configured minimum required, leaving the inputs unmodified and never raising.

Validates: Requirements 1.3, 2.1, 2.3.

The classifier gates on ``required = max(min_candles, largest_lookback)``. The
strategy below draws an arbitrary (internally consistent) ``RegimeConfig``,
computes that gate, then builds a candle sequence whose *valid* candle count is
strictly below the gate — covering both routes into the insufficient-data
branch:

  * "short to begin with": the sequence simply has too few candles, and
  * "short after excluding non-finite": the sequence is padded with candles
    carrying non-finite / non-numeric OHLCV fields that are excluded before the
    count is taken, leaving fewer than ``required`` valid candles.

The sys.path / import pattern mirrors ``tests/test_regime_determinism_properties.py``.
"""

import copy
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from regime import RegimeConfig, classify_regime  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Finite price values in a sane, non-degenerate band. These produce candles that
# ``_parse_ohlc`` accepts (every field a finite number), so each counts as a
# *valid* candle toward the sufficiency gate.
_finite_price = st.floats(
    min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False
)

# Values that make a candle "dirty" — a non-finite or non-numeric OHLCV field
# that ``_parse_ohlc`` rejects, so the carrying candle is excluded from the valid
# count (Requirement 2.2). These drive the "short after exclusion" route.
_bad_field = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), "x", None, "12.5", True, [], {}]
)


@st.composite
def _valid_candle(draw):
    """A well-formed OHLCV candle dict with finite numeric fields (counts as
    one valid candle toward the sufficiency gate)."""
    a = draw(_finite_price)
    b = draw(_finite_price)
    low = min(a, b)
    high = max(a, b)
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False,
                           allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False,
                           allow_infinity=False))
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": draw(st.floats(min_value=0.0, max_value=1e9, allow_nan=False,
                                 allow_infinity=False)),
    }


@st.composite
def _dirty_candle(draw):
    """A candle dict guaranteed to be excluded from the valid count.

    Corruption is applied to one of the *required* OHLC fields
    (``open``/``high``/``low``/``close``); a non-finite / non-numeric value there
    always causes ``_parse_ohlc`` to reject the candle. (``volume`` is excluded
    as a corruption target because an absent / ``None`` volume does NOT, by
    itself, invalidate an otherwise-clean candle, so corrupting it would not
    reliably reduce the valid count.)
    """
    candle = draw(_valid_candle())
    field = draw(st.sampled_from(["open", "high", "low", "close"]))
    candle[field] = draw(_bad_field)
    return candle


@st.composite
def _config(draw):
    """An arbitrary ``RegimeConfig`` whose sufficiency gate stays small.

    Lookback periods and the percentile window are bounded so the gate
    ``required = max(min_candles, largest_lookback)`` is modest, keeping the
    "fewer than required valid candles" inputs cheap to generate while still
    exercising arbitrary threshold combinations. The low<high volatility ordering
    is preserved (as ``resolve_regime_config`` would produce), though the gate
    does not depend on it.
    """
    vol_low = draw(st.floats(min_value=0.0, max_value=80.0, allow_nan=False,
                             allow_infinity=False))
    vol_high = draw(st.floats(min_value=vol_low + 1.0, max_value=100.0,
                              allow_nan=False, allow_infinity=False))
    return RegimeConfig(
        adx_period=draw(st.integers(min_value=2, max_value=20)),
        chop_period=draw(st.integers(min_value=2, max_value=20)),
        vol_period=draw(st.integers(min_value=1, max_value=15)),
        vol_pctl_window=draw(st.integers(min_value=1, max_value=25)),
        bb_period=draw(st.integers(min_value=1, max_value=20)),
        adx_trend_cutoff=draw(st.floats(min_value=0.0, max_value=100.0,
                                        allow_nan=False, allow_infinity=False)),
        chop_ranging_cutoff=draw(st.floats(min_value=0.0, max_value=100.0,
                                           allow_nan=False, allow_infinity=False)),
        vol_low_pctl=vol_low,
        vol_high_pctl=vol_high,
        min_candles=draw(st.integers(min_value=1, max_value=40)),
    )


@st.composite
def _config_and_insufficient_candles(draw):
    """Draw a config plus a candle sequence with too few *valid* candles.

    Returns ``(config, candles, n_valid, required)`` where:
      * ``required = max(min_candles, largest_lookback)`` is the gate,
      * ``n_valid`` is the number of valid candles, drawn strictly below the
        gate (``0 <= n_valid < required``),
      * ``candles`` interleaves exactly ``n_valid`` valid candles with an
        arbitrary number of dirty (excluded) candles, so the valid-candle count
        is exactly ``n_valid`` regardless of how many dirty candles are present.
    """
    config = draw(_config())
    required = max(config.min_candles, config.largest_lookback)

    # Strictly fewer than the gate (required >= 1 because min_candles >= 1).
    n_valid = draw(st.integers(min_value=0, max_value=required - 1))

    valid = [draw(_valid_candle()) for _ in range(n_valid)]
    dirty = draw(st.lists(_dirty_candle(), max_size=12))

    # Interleave: insert each dirty candle at an arbitrary position so the valid
    # candles keep their relative order but the sequence is "padded" with
    # excluded candles (the "short after exclusion" route). When no dirty candles
    # are drawn, this is simply the "short to begin with" route.
    candles = list(valid)
    for bad in dirty:
        idx = draw(st.integers(min_value=0, max_value=len(candles)))
        candles.insert(idx, bad)

    return config, candles, n_valid, required


# ─────────────────────────────────────────────────────────────────────────────
# Property 8: Insufficient valid candles yield an Unavailable_Marker with counts
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 8
@settings(max_examples=200, deadline=None)
@given(data=_config_and_insufficient_candles())
def test_property_8_insufficient_candles_unavailable(data):
    """Validates: Requirements 1.3, 2.1, 2.3

    For any candle sequence whose count of valid candles is below the configured
    gate ``max(min_candles, largest_lookback)`` — whether short to begin with or
    short after excluding non-finite candles — ``classify_regime`` returns an
    Unavailable_Marker whose reason identifies the insufficient-data condition
    and includes both the received count and the required count, leaves the
    inputs unmodified, and never raises.
    """
    config, candles, n_valid, required = data

    # Snapshot the inputs to confirm purity (no mutation while classifying).
    candles_snapshot = copy.deepcopy(candles)

    # Must never raise (Requirements 2.1, 2.3): the call itself is the assertion.
    result = classify_regime(candles, config, symbol="RELIANCE", timeframe="1m")

    # The result is an honest Unavailable_Marker.
    assert isinstance(result, dict)
    assert result.get("unavailable") is True, (
        f"expected an Unavailable_Marker for {n_valid} valid candles "
        f"(< required {required}); got {result!r}"
    )

    # The reason identifies the insufficient-data condition (Requirement 1.3) and
    # includes BOTH the count of valid candles received and the configured
    # minimum required (Requirements 2.1, 2.3).
    reason = result.get("reason", "")
    assert isinstance(reason, str)
    assert "insufficient" in reason.lower(), (
        f"reason should identify the insufficient-data condition; got {reason!r}"
    )
    assert str(n_valid) in reason, (
        f"reason should include the received count {n_valid}; got {reason!r}"
    )
    assert str(required) in reason, (
        f"reason should include the required count {required}; got {reason!r}"
    )

    # An Unavailable_Marker omits the regime states rather than fabricating them
    # (Requirement 2 / design Unavailable_Marker schema): no trend/volatility/
    # favorability are present.
    assert "trend_state" not in result
    assert "volatility_state" not in result
    assert "favorability" not in result

    # The inputs are left unmodified (Requirements 1.3, 2.3 — "leave the provided
    # candle sequence and configuration unmodified").
    assert candles == candles_snapshot, "classify_regime mutated its candle input"
