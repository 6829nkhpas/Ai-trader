"""Property-based test for present, finite-or-null, correct measures (rs.py, task 3.4).

Feature: relative-strength-context

This module implements design **Property 3: Computed measures are present,
finite-or-null, and correct**:

    Whenever ``classify_relative_strength`` returns a Relative_Strength_Label
    (rather than an Unavailable_Marker), its ``measures`` dict carries every
    named Relative_Strength_Measure key (``rs_ratio``, ``rs_ratio_slope``,
    ``relative_return``, ``correlation``, ``beta``), and each value is either
    ``None`` or a finite number (Requirements 1.3, 1.4, 1.5, 3.3). The
    relative-return and RS-ratio measures additionally agree with an independent
    recomputation from the same aligned closes.

Validates: Requirements 1.3, 1.4, 1.5, 3.3.

``classify_relative_strength`` time-aligns the symbol and benchmark candles by
timestamp before computing the measures, so this test generates two candle
sequences on a shared, strictly-increasing timestamp grid (every bar survives
alignment) with enough candles to clear the sufficiency gate and be
classifiable. The sys.path / import and candle-generation patterns mirror the
sibling ``test_rs_*_properties.py`` modules.
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

import rs  # noqa: E402
from rs import classify_relative_strength, resolve_rs_config  # noqa: E402

# The named Relative_Strength_Measure keys that must always be present in a
# returned label's ``measures`` dict (Requirements 1.3, 1.4, 1.5).
_MEASURE_KEYS = ("rs_ratio", "rs_ratio_slope", "relative_return", "correlation", "beta")

# Strictly-positive close prices so benchmark prices and return bases are never
# zero — this keeps every bar valid (finite) and avoids the all-null
# unavailable path, so the calculator returns a label we can inspect. The pool
# spans ordinary and extreme magnitudes to stress the measures.
_PRICE = st.floats(min_value=0.5, max_value=1e6, allow_nan=False, allow_infinity=False)


@st.composite
def _classifiable_candle_pair(draw):
    """A symbol and benchmark candle sequence sharing a common timestamp grid.

    Both sequences carry the same strictly-increasing timestamps (so
    ``time_align`` keeps every bar in build order) and enough bars to clear the
    sufficiency gate (``max(min_candles, largest_lookback)`` for the resolved
    config), so ``classify_relative_strength`` returns a Relative_Strength_Label.
    Close prices are drawn independently per sequence so the relative-strength
    measures take arbitrary values.
    """
    config = resolve_rs_config()
    required = max(config.min_candles, config.largest_lookback)
    # A little headroom above the gate so we comfortably stay classifiable.
    n = draw(st.integers(min_value=required, max_value=required + 40))
    timestamps = [1_000 + i * 60_000 for i in range(n)]

    def _candle(ts):
        close = draw(_PRICE)
        return {
            "timestamp_ms": ts,
            "open": draw(_PRICE),
            "high": draw(_PRICE),
            "low": draw(_PRICE),
            "close": close,
            "volume": draw(
                st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
            ),
        }

    symbol_candles = [_candle(ts) for ts in timestamps]
    benchmark_candles = [_candle(ts) for ts in timestamps]
    return symbol_candles, benchmark_candles, config


# ─────────────────────────────────────────────────────────────────────────────
# Property 3 (task 3.4): Computed measures are present, finite-or-null, and correct
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 3: Computed measures are present, finite-or-null, and correct
@settings(max_examples=100, deadline=None)
@given(candle_pair=_classifiable_candle_pair())
def test_property_3_measures_present_finite_or_null_and_correct(candle_pair):
    """Feature: relative-strength-context, Property 3: Computed measures are
    present, finite-or-null, and correct.

    When ``classify_relative_strength`` returns a label, every named measure key
    is present and each value is ``None`` or a finite number; the relative-return
    and RS-ratio measures match an independent recomputation from the aligned
    closes.

    Validates: Requirements 1.3, 1.4, 1.5, 3.3
    """
    symbol_candles, benchmark_candles, config = candle_pair

    result = classify_relative_strength(symbol_candles, benchmark_candles, config)

    # The generated input is classifiable, so a label (not a marker) is returned.
    assert "unavailable" not in result
    measures = result["measures"]
    assert isinstance(measures, dict)

    # R1.3 / R1.4 / R1.5 / R3.3: every named measure key is present, and each
    # value is either None or a finite number.
    for key in _MEASURE_KEYS:
        assert key in measures, f"missing measure {key!r}"
        value = measures[key]
        assert value is None or (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ), f"measure {key!r} is neither None nor a finite number: {value!r}"

    # Independent recomputation from the same aligned closes. Because both
    # sequences share a strictly-increasing timestamp grid, the aligned order is
    # exactly the build order; closes are the price source for the measures.
    sym_closes = [c["close"] for c in symbol_candles]
    bench_closes = [c["close"] for c in benchmark_candles]

    # rs_ratio = symbol_close / benchmark_close at the most recent aligned bar (R1.3).
    expected_rs_ratio = sym_closes[-1] / bench_closes[-1]
    assert measures["rs_ratio"] is not None
    assert math.isclose(
        measures["rs_ratio"], expected_rs_ratio, rel_tol=1e-9, abs_tol=1e-12
    ), f"rs_ratio {measures['rs_ratio']!r} != recomputed {expected_rs_ratio!r}"

    # relative_return = symbol_return - benchmark_return over the configured
    # lookback (base = the bar `lookback` steps back from the latest) (R1.4).
    base = config.lookback + 1
    sym_base, bench_base = sym_closes[-base], bench_closes[-base]
    expected_rel_return = (
        (sym_closes[-1] - sym_base) / sym_base
        - (bench_closes[-1] - bench_base) / bench_base
    )
    assert measures["relative_return"] is not None
    assert math.isclose(
        measures["relative_return"], expected_rel_return, rel_tol=1e-9, abs_tol=1e-12
    ), f"relative_return {measures['relative_return']!r} != recomputed {expected_rel_return!r}"
