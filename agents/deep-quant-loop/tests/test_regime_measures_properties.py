"""Property-based tests for regime measure functions (regime.py, task 2.x).

Feature: regime-detection-gate

These Hypothesis properties exercise the pure Regime_Measure functions in
``regime.py``. Task 2.2 covers Property 4 (bounded-measure clamping): the three
measures defined on a closed numeric range — the Kaufman efficiency ratio
([0.0, 1.0]), the choppiness index ([0.0, 100.0]), and the ATR-percentile
([0.0, 100.0]) — must report a value within their defined bounds whenever they
return a non-``None`` value, even for extreme or degenerate candle inputs that
would otherwise push the raw computation outside its range.

Candles are dict OHLCV records with keys ``open`` / ``high`` / ``low`` /
``close`` / ``volume`` (matching how ``regime.py`` / ``journal.py`` /
``backtest.py`` read candles via ``c.get(...)``). The generator below produces
arbitrary candle sequences, including extreme magnitudes, near-zero ranges,
flat windows, and inverted high/low values, so the clamping guarantee is
stressed across the degenerate input space.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from regime import (  # noqa: E402
    compute_atr_percentile,
    compute_choppiness,
    compute_efficiency_ratio,
)

# ─────────────────────────────────────────────────────────────────────────────
# Candle generation: arbitrary OHLCV records, including extreme / degenerate values
# ─────────────────────────────────────────────────────────────────────────────

# A pool of price values spanning ordinary, extreme, near-zero, and zero
# magnitudes so the bounded measures are stressed at the edges of their domain.
_PRICE = st.one_of(
    st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1e-9, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([0.0, 1e-12, 1e12, 1.0, 100.0, 12345.6789]),
)


@st.composite
def _candle(draw):
    """One OHLCV candle dict with possibly extreme / inconsistent values.

    ``high``/``low`` are NOT forced to bracket ``open``/``close``; this is
    intentional so the generator also produces degenerate (e.g. inverted-range
    or flat) candles that drive the raw measures toward or past their bounds,
    exercising the clamping guarantee.
    """
    return {
        "open": draw(_PRICE),
        "high": draw(_PRICE),
        "low": draw(_PRICE),
        "close": draw(_PRICE),
        "volume": draw(
            st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
        ),
    }


@st.composite
def _flat_candle(draw):
    """A flat candle where O=H=L=C (a zero-range, degenerate bar)."""
    p = draw(_PRICE)
    return {"open": p, "high": p, "low": p, "close": p, "volume": draw(_PRICE)}


# Sequences long enough to satisfy the measures' lookbacks, mixing arbitrary and
# flat candles so both ordinary and zero-range windows are covered.
_CANDLES = st.lists(
    st.one_of(_candle(), _flat_candle()),
    min_size=1,
    max_size=160,
)

# A period small enough to be satisfiable by the generated sequences but large
# enough to exercise multi-bar windows (choppiness requires period >= 2).
_PERIOD = st.integers(min_value=2, max_value=30)
_WINDOW = st.integers(min_value=1, max_value=120)


# ─────────────────────────────────────────────────────────────────────────────
# Property 4: Bounded measures are clamped within their range
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 4
@settings(max_examples=200, deadline=None)
@given(candles=_CANDLES, period=_PERIOD, window=_WINDOW)
def test_property_4_bounded_measures_are_clamped(candles, period, window):
    """Validates: Requirements 2.5

    For any candle sequence (including extreme / degenerate values), whenever a
    bounded Regime_Measure returns a non-``None`` value it lies within its
    defined bounds:
      * efficiency ratio  in [0.0, 1.0]
      * choppiness index  in [0.0, 100.0]
      * ATR-percentile    in [0.0, 100.0]
    """
    er = compute_efficiency_ratio(candles, period)
    if er is not None:
        assert math.isfinite(er), f"efficiency_ratio not finite: {er!r}"
        assert 0.0 <= er <= 1.0, f"efficiency_ratio {er!r} outside [0.0, 1.0]"

    chop = compute_choppiness(candles, period)
    if chop is not None:
        assert math.isfinite(chop), f"choppiness not finite: {chop!r}"
        assert 0.0 <= chop <= 100.0, f"choppiness {chop!r} outside [0.0, 100.0]"

    atr_pctl = compute_atr_percentile(candles, period, window)
    if atr_pctl is not None:
        assert math.isfinite(atr_pctl), f"atr_percentile not finite: {atr_pctl!r}"
        assert 0.0 <= atr_pctl <= 100.0, f"atr_percentile {atr_pctl!r} outside [0.0, 100.0]"
