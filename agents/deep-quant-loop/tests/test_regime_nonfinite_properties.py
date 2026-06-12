"""Property-based test for non-finite candle exclusion (regime.py, task 2.3).

Feature: regime-detection-gate

This Hypothesis property exercises the candle-validation behaviour shared by
every Regime_Measure function: candles carrying a non-finite or non-numeric
OHLCV field (NaN / +/-inf / None / non-numeric values such as strings, bools,
or containers) are excluded from EVERY computation, so interleaving such
corrupt candles anywhere in a valid sequence does not change the measure
result and never raises.

  * Property 7 (2.2) — Non-finite candles are excluded without affecting the
                       result: for any valid candle sequence and any
                       interleaving of candles carrying non-finite / non-numeric
                       OHLCV fields, each measure function returns a result equal
                       to the result of computing on only the valid candles, and
                       never raises. (classify_regime is asserted the same way
                       when it is available.)
"""

import os
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import regime  # noqa: E402
from regime import (  # noqa: E402
    compute_atr_percentile,
    compute_bb_width,
    compute_choppiness,
    compute_directional_strength,
    compute_efficiency_ratio,
)

# Lookback parameters used to drive the measures. They are small enough that a
# few dozen valid candles make every measure computable, yet exercise the real
# windowed code paths.
_ADX_PERIOD = 14
_CHOP_PERIOD = 14
_ER_PERIOD = 14
_ATR_PERIOD = 14
_ATR_WINDOW = 100
_BB_PERIOD = 20

# Enough valid candles to make every measure computable. The most demanding
# lookback here is the Bollinger period (20 valid rows); the ATR-percentile
# window is an upper bound on the trailing slice, not a minimum, so a few dozen
# valid candles exercise every measure while keeping input generation cheap.
_MIN_VALID = 25
_MAX_VALID = 70


# ── Generators ────────────────────────────────────────────────────────────────

# Finite, positive, bounded price components. Bounded to keep variance/stddev
# arithmetic well away from overflow while still spanning a realistic range.
_price = st.floats(
    min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _valid_candle(draw):
    """A dict OHLCV candle whose every field is a finite, numeric value.

    ``_parse_ohlc`` accepts this candle (every measure includes it). High/low
    are derived from open/close with a tiny fixed margin so the record is
    plausible; the property only requires that all fields be finite numbers, so
    generation stays cheap (two price draws per candle).
    """
    open_ = draw(_price)
    close = draw(_price)
    high = max(open_, close) + 1.0
    low = max(min(open_, close) - 1.0, 0.5)
    return {"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}


# Values that make an OHLCV field non-finite or non-numeric. Each guarantees the
# carrying candle is excluded by ``_parse_ohlc`` (NaN/inf fail ``isfinite``;
# None/str/bool/containers are non-numeric — note ``bool`` is excluded by the
# repo's ``_is_finite_number`` convention).
_bad_value = st.sampled_from(
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        None,
        "not-a-number",
        "",
        True,
        False,
        [],
        {},
    ]
)


@st.composite
def _bad_candle(draw):
    """A candle guaranteed to be excluded: at least one of open/high/low/close
    carries a non-finite / non-numeric value (volume may be corrupted too)."""
    candle = dict(draw(_valid_candle()))
    field = draw(st.sampled_from(["open", "high", "low", "close"]))
    candle[field] = draw(_bad_value)
    if draw(st.booleans()):
        candle["volume"] = draw(_bad_value)
    return candle


@st.composite
def _clean_and_corrupted(draw):
    """Produce a (clean, corrupted) pair.

    ``clean`` is a sequence of only-valid candles. ``corrupted`` is ``clean``
    with zero or more guaranteed-invalid candles inserted at arbitrary
    positions, so the valid candles retain their original relative order.
    """
    clean = draw(st.lists(_valid_candle(), min_size=_MIN_VALID, max_size=_MAX_VALID))
    bad_candles = draw(st.lists(_bad_candle(), max_size=15))
    corrupted = list(clean)
    for bad in bad_candles:
        idx = draw(st.integers(min_value=0, max_value=len(corrupted)))
        corrupted.insert(idx, bad)
    return clean, corrupted


# ─────────────────────────────────────────────────────────────────────────────
# Property 7 (2.2): Non-finite candles are excluded without affecting the result
# ─────────────────────────────────────────────────────────────────────────────

@settings(max_examples=150, suppress_health_check=[HealthCheck.large_base_example])
@given(data=_clean_and_corrupted())
def test_property_7_non_finite_candles_excluded(data):
    # Feature: regime-detection-gate, Property 7
    """Feature: regime-detection-gate, Property 7: Non-finite candles are
    excluded without affecting the result — for any valid candle sequence and
    any interleaving of candles carrying non-finite / non-numeric OHLCV fields,
    every measure function returns a result equal to the result of computing on
    only the valid candles, and never raises an exception.

    Validates: Requirements 2.2
    """
    clean, corrupted = data

    # Each measure must yield an identical result on the corrupted sequence as on
    # the clean sequence (the invalid candles are dropped before any math), and
    # neither call may raise.
    assert compute_directional_strength(corrupted, _ADX_PERIOD) == \
        compute_directional_strength(clean, _ADX_PERIOD)
    assert compute_choppiness(corrupted, _CHOP_PERIOD) == \
        compute_choppiness(clean, _CHOP_PERIOD)
    assert compute_efficiency_ratio(corrupted, _ER_PERIOD) == \
        compute_efficiency_ratio(clean, _ER_PERIOD)
    assert compute_atr_percentile(corrupted, _ATR_PERIOD, _ATR_WINDOW) == \
        compute_atr_percentile(clean, _ATR_PERIOD, _ATR_WINDOW)
    assert compute_bb_width(corrupted, _BB_PERIOD) == \
        compute_bb_width(clean, _BB_PERIOD)

    # classify_regime is the top-level entry point (added in a later task). When
    # it is present, it must obey the same exclusion invariant: classifying the
    # corrupted sequence equals classifying only the valid candles, and it never
    # raises.
    classify_regime = getattr(regime, "classify_regime", None)
    if callable(classify_regime):
        config = regime.resolve_regime_config()
        assert classify_regime(corrupted, config) == classify_regime(clean, config)
