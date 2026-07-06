"""Property-based test for bounded-measure clamping (rs.py, task 2.2).

Feature: relative-strength-context

This module implements design **Property 4: Bounded measures are clamped within
their range**:

    A Relative_Strength_Measure defined on a bounded range is clamped to the
    nearest boundary value whenever its computed value would otherwise fall
    outside the range. The correlation measure is defined on the closed range
    ``[-1.0, 1.0]``; whenever ``compute_correlation`` returns a non-``None``
    value, that value is a finite number within ``[-1.0, 1.0]`` — even for
    extreme, near-degenerate, or floating-point-edge candle inputs whose raw
    Pearson computation could otherwise drift just outside the unit interval.

Validates: Requirements 3.4.

``compute_correlation`` operates on the time-aligned ``(ts, o, h, l, c)`` rows
produced by ``time_align``, so this test generates arbitrary symbol/benchmark
candle sequences sharing timestamps, aligns them exactly as the calculator does,
and asserts the bound on the resulting correlation. The sys.path / import
pattern mirrors ``tests/test_rs_config_default_fallback_properties.py`` and the
candle-generation approach mirrors ``tests/test_regime_measures_properties.py``.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from rs import compute_correlation, time_align  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Candle generation: arbitrary OHLCV records sharing timestamps so that
# ``time_align`` yields paired rows for ``compute_correlation``.
# ─────────────────────────────────────────────────────────────────────────────

# A pool of close prices spanning ordinary, extreme, and near-zero magnitudes so
# the per-bar returns (and thus the raw Pearson numerator/denominator) are
# stressed at the edges of their domain, where floating-point error could push a
# raw correlation marginally outside [-1.0, 1.0].
_PRICE = st.one_of(
    st.floats(min_value=1e-6, max_value=1e9, allow_nan=False, allow_infinity=False),
    st.sampled_from([1e-6, 1.0, 100.0, 1e6, 1e9, 12345.6789]),
)

# A correlation window small enough to be satisfiable by the generated sequences
# but large enough to exercise multi-bar return windows.
_WINDOW = st.integers(min_value=2, max_value=40)


@st.composite
def _aligned_candle_pair(draw):
    """A symbol and benchmark candle sequence sharing a common timestamp grid.

    Both sequences carry the same strictly-increasing timestamps, so
    ``time_align`` keeps every bar; the close prices are drawn independently
    (and from extreme/near-zero pools) so the resulting return series produce
    arbitrary covariance structures — including the perfectly-correlated and
    anti-correlated edges that drive a raw Pearson value toward +/-1.0.
    """
    n = draw(st.integers(min_value=1, max_value=80))
    timestamps = [1_000 + i * 60_000 for i in range(n)]

    def _candle(ts):
        return {
            "timestamp_ms": ts,
            "open": draw(_PRICE),
            "high": draw(_PRICE),
            "low": draw(_PRICE),
            "close": draw(_PRICE),
            "volume": draw(
                st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
            ),
        }

    symbol_candles = [_candle(ts) for ts in timestamps]
    benchmark_candles = [_candle(ts) for ts in timestamps]
    return symbol_candles, benchmark_candles


# ─────────────────────────────────────────────────────────────────────────────
# Property 4: Bounded measures are clamped within their range
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 4: Bounded measures are clamped within their range
@settings(max_examples=100, deadline=None)
@given(candle_pair=_aligned_candle_pair(), window=_WINDOW)
def test_property_4_bounded_measures_are_clamped(candle_pair, window):
    """Feature: relative-strength-context, Property 4: Bounded measures are
    clamped within their range.

    For any pair of arbitrary candle sequences (including extreme / near-zero
    prices), whenever ``compute_correlation`` returns a non-``None`` value it is
    a finite number lying within the bounded range ``[-1.0, 1.0]``.

    Validates: Requirements 3.4
    """
    symbol_candles, benchmark_candles = candle_pair
    symbol_rows, benchmark_rows = time_align(symbol_candles, benchmark_candles)

    corr = compute_correlation(symbol_rows, benchmark_rows, window)

    if corr is not None:
        assert math.isfinite(corr), f"correlation not finite: {corr!r}"
        assert -1.0 <= corr <= 1.0, f"correlation {corr!r} outside [-1.0, 1.0]"
