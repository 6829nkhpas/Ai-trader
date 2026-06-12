"""Property-based test for regime classification determinism (regime.py, task 3.2).

Feature: regime-detection-gate

This module implements design **Property 1: Classification is deterministic**:

    For any candle sequence and resolved configuration, invoking ``classify_regime``
    two or more times with element-wise identical candles and an identical config
    returns results (Regime_Label or Unavailable_Marker — including every state,
    measure, and Favorability) that are element-wise identical across all
    invocations.

Validates: Requirements 1.2, 2.8.

The strategies below generate arbitrary candle sequences (mixing clean OHLCV
records with candles carrying non-finite / non-numeric fields, short and long
sequences) together with arbitrary ``RegimeConfig`` values, so the property
exercises the Regime_Label path, the Unavailable_Marker path, and the
non-finite-exclusion path. Determinism is asserted by classifying the *same*
inputs three times (with and without symbol/timeframe) and requiring deep
equality of the results.

The sys.path / import pattern mirrors ``tests/test_regime_config.py``: the
service directory (one level up) is prepended to ``sys.path`` so ``regime`` is
importable when pytest is run from anywhere.
"""

import copy
import math
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

# Finite price values kept in a sane, non-degenerate band so generated sequences
# frequently reach the Regime_Label path (rather than only ever degenerating to
# an Unavailable_Marker). NaN / inf are injected separately below.
_finite_price = st.floats(
    min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False
)

# Values that make a candle "dirty" — a non-finite or non-numeric OHLCV field
# that must be excluded from every measure computation (Requirement 2.2). The
# determinism property must hold regardless of how many of these appear.
_bad_field = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), "x", None, "12.5", True]
)


@st.composite
def _clean_candle(draw):
    """A well-formed OHLCV candle dict with finite numeric fields."""
    a = draw(_finite_price)
    b = draw(_finite_price)
    c = draw(_finite_price)
    d = draw(_finite_price)
    low = min(a, b, c, d)
    high = max(a, b, c, d)
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
    """A candle dict carrying at least one non-finite / non-numeric OHLCV field."""
    candle = draw(_clean_candle())
    field = draw(st.sampled_from(["open", "high", "low", "close", "volume"]))
    candle[field] = draw(_bad_field)
    return candle


@st.composite
def _candle(draw):
    """Mostly clean candles, occasionally dirty ones (exercise exclusion path)."""
    if draw(st.integers(min_value=0, max_value=9)) == 0:
        return draw(_dirty_candle())
    return draw(_clean_candle())


# Variable-length sequences: short ones drive the Unavailable_Marker path, long
# ones drive the Regime_Label path.
_candles = st.lists(_candle(), min_size=0, max_size=160)


@st.composite
def _config(draw):
    """An arbitrary, internally consistent ``RegimeConfig``.

    Lookback periods and the percentile window are kept small so the configured
    ``largest_lookback`` is frequently reachable by the generated sequences,
    letting the property cover both the label and the marker paths. The
    low<high volatility-percentile ordering is enforced (as ``resolve_regime_
    config`` would), but the property only needs a valid config object.
    """
    vol_low = draw(st.floats(min_value=0.0, max_value=80.0, allow_nan=False,
                             allow_infinity=False))
    vol_high = draw(st.floats(min_value=vol_low + 1.0, max_value=100.0,
                              allow_nan=False, allow_infinity=False))
    return RegimeConfig(
        adx_period=draw(st.integers(min_value=2, max_value=20)),
        chop_period=draw(st.integers(min_value=2, max_value=20)),
        vol_period=draw(st.integers(min_value=1, max_value=20)),
        vol_pctl_window=draw(st.integers(min_value=1, max_value=40)),
        bb_period=draw(st.integers(min_value=1, max_value=20)),
        adx_trend_cutoff=draw(st.floats(min_value=0.0, max_value=100.0,
                                        allow_nan=False, allow_infinity=False)),
        chop_ranging_cutoff=draw(st.floats(min_value=0.0, max_value=100.0,
                                           allow_nan=False, allow_infinity=False)),
        vol_low_pctl=vol_low,
        vol_high_pctl=vol_high,
        min_candles=draw(st.integers(min_value=1, max_value=60)),
    )


def _deep_equal(a, b):
    """Structural equality that treats NaN measures as equal to NaN.

    Regime_Measures are always a finite number or ``None`` by construction, so a
    plain ``==`` suffices for measures; this helper additionally treats two NaNs
    as equal purely as a defensive guard so a (non-)deterministic NaN would still
    be caught as a *difference* rather than masked by ``nan != nan``.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return a == b
    return a == b


# ─────────────────────────────────────────────────────────────────────────────
# Property 1: Classification is deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 1
@settings(max_examples=200, deadline=None)
@given(candles=_candles, config=_config())
def test_property_1_classification_is_deterministic(candles, config):
    """Validates: Requirements 1.2, 2.8

    Invoking ``classify_regime`` repeatedly with element-wise identical candles
    and an identical config returns element-wise identical results (whether a
    Regime_Label or an Unavailable_Marker), including every state, measure, and
    Favorability.
    """
    # Snapshot the inputs so we can confirm the calls did not mutate them (a
    # mutation would be a hidden source of non-determinism across invocations).
    candles_snapshot = copy.deepcopy(candles)

    first = classify_regime(candles, config, symbol="RELIANCE", timeframe="15m")
    second = classify_regime(candles, config, symbol="RELIANCE", timeframe="15m")
    third = classify_regime(candles, config, symbol="RELIANCE", timeframe="15m")

    assert _deep_equal(first, second), (
        f"non-deterministic across invocations:\n first={first!r}\n second={second!r}"
    )
    assert _deep_equal(second, third), (
        f"non-deterministic across invocations:\n second={second!r}\n third={third!r}"
    )

    # Determinism must also hold for the no-symbol/no-timeframe call shape: the
    # only difference between the two result families is the optional symbol/
    # timeframe keys, never the states, measures, or favorability.
    bare_first = classify_regime(candles, config)
    bare_second = classify_regime(candles, config)
    assert _deep_equal(bare_first, bare_second), (
        f"non-deterministic (bare call):\n first={bare_first!r}\n "
        f"second={bare_second!r}"
    )

    # Inputs must be left unmodified across all invocations (purity underpins
    # determinism — Requirements 1.2 / 2.8).
    assert candles == candles_snapshot, "classify_regime mutated its candle input"
