"""Property-based test for zero-denominator and all-null handling (rs.py, task 3.9).

Feature: relative-strength-context

This module implements design **Property 11: Zero-denominator measures are null,
and all-null yields unavailable**:

    A Relative_Strength_Measure whose denominator is zero — a zero benchmark
    price, or zero return variance over the window — is represented as ``null``
    in the Relative_Strength_Label rather than raising (Requirement 3.5). When
    EVERY required measure is null because none could be computed,
    ``classify_relative_strength`` returns an Unavailable_Marker
    (``unavailable: true``) instead of a label, and never fabricates an
    Index_Direction / Relative_Strength_State / Alignment (Requirement 3.6).

Validates: Requirements 3.5, 3.6.

The test drives two zero-denominator regimes on a shared, strictly-increasing
timestamp grid (so ``time_align`` keeps every bar in build order) with enough
bars to clear the sufficiency gate:

  * ``flat_zero_variance``  — both the symbol and the benchmark hold a constant
    (flat) close, so per-bar return variance is zero on both legs. ``correlation``
    and ``beta`` (the variance-denominated measures) are therefore ``null`` while
    other measures remain computable, so a label is returned that we inspect.
  * ``all_benchmark_zero``  — every benchmark close (and OHLC field) is zero, so
    EVERY measure's denominator is zero and none can be computed; the calculator
    must degrade to an Unavailable_Marker.

The sys.path / import and candle-generation patterns mirror the sibling
``test_rs_*_properties.py`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from rs import classify_relative_strength, resolve_rs_config  # noqa: E402

# Fields the calculator must NOT fabricate when relative strength is unavailable
# (Requirement 3.6 / AD-4): the marker omits these entirely.
_FABRICATED_FIELDS = ("index_direction", "relative_strength_state", "alignment")

# Strictly-positive, finite close prices so a flat sequence's prices are never
# zero (isolating the zero-*variance* denominator rather than a zero price).
_PRICE = st.floats(min_value=0.5, max_value=1e6, allow_nan=False, allow_infinity=False)


@st.composite
def _zero_denominator_case(draw):
    """A symbol/benchmark candle pair exercising a zero-denominator regime.

    Returns ``(symbol_candles, benchmark_candles, config, scenario)`` where both
    sequences share a strictly-increasing timestamp grid with enough aligned
    bars to clear the sufficiency gate. ``scenario`` selects which zero-
    denominator regime is built.
    """
    config = resolve_rs_config()
    required = max(config.min_candles, config.largest_lookback)
    # A little headroom above the gate so the aligned-candle count clears it.
    n = draw(st.integers(min_value=required, max_value=required + 20))
    timestamps = [1_000 + i * 60_000 for i in range(n)]
    scenario = draw(st.sampled_from(["flat_zero_variance", "all_benchmark_zero"]))

    def _flat_candle(ts, price):
        # A perfectly flat bar: every OHLC field equals the constant close, so
        # the per-bar return series for this leg has zero variance.
        return {
            "timestamp_ms": ts,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": draw(
                st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
            ),
        }

    def _zero_candle(ts):
        # A bar whose every price field is zero: any measure denominated by a
        # benchmark price or benchmark return is undefined here.
        return {
            "timestamp_ms": ts,
            "open": 0.0,
            "high": 0.0,
            "low": 0.0,
            "close": 0.0,
            "volume": 0.0,
        }

    if scenario == "flat_zero_variance":
        # Distinct constant closes per leg; both flat so both return series have
        # zero variance -> correlation and beta are null, other measures finite.
        sym_price = draw(_PRICE)
        bench_price = draw(_PRICE)
        symbol_candles = [_flat_candle(ts, sym_price) for ts in timestamps]
        benchmark_candles = [_flat_candle(ts, bench_price) for ts in timestamps]
    else:  # all_benchmark_zero
        # Symbol prices vary normally; every benchmark price is zero so every
        # measure's denominator (price or return variance) is zero.
        symbol_candles = [_flat_candle(ts, draw(_PRICE)) for ts in timestamps]
        benchmark_candles = [_zero_candle(ts) for ts in timestamps]

    return symbol_candles, benchmark_candles, config, scenario


# ─────────────────────────────────────────────────────────────────────────────
# Property 11 (task 3.9): Zero-denominator measures are null, and all-null
# yields unavailable
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 11: Zero-denominator measures are null, and all-null yields unavailable
@settings(max_examples=100, deadline=None)
@given(case=_zero_denominator_case())
def test_property_11_zero_denominator_null_and_all_null_unavailable(case):
    """Feature: relative-strength-context, Property 11: Zero-denominator measures
    are null, and all-null yields unavailable.

    A measure whose denominator is zero is represented as ``null`` in the label
    rather than raising; when every required measure is null, the calculator
    returns an Unavailable_Marker and fabricates no Index_Direction /
    Relative_Strength_State / Alignment.

    Validates: Requirements 3.5, 3.6
    """
    symbol_candles, benchmark_candles, config, scenario = case

    # Never raises (Requirement 3): classification always yields a dict.
    result = classify_relative_strength(symbol_candles, benchmark_candles, config)
    assert isinstance(result, dict)

    if scenario == "flat_zero_variance":
        # Both legs are flat -> per-bar return variance is zero on both, so the
        # variance-denominated measures must be null (Requirement 3.5). Other
        # measures remain computable, so a label (not a marker) is returned.
        assert "unavailable" not in result, (
            "flat (zero-variance) input should still classify into a label"
        )
        measures = result["measures"]
        assert measures["correlation"] is None, (
            f"zero-variance correlation must be null, got {measures['correlation']!r}"
        )
        assert measures["beta"] is None, (
            f"zero-variance beta must be null, got {measures['beta']!r}"
        )
    else:  # all_benchmark_zero
        # Every benchmark price is zero -> every measure's denominator is zero
        # and none can be computed, so the calculator must degrade to an honest
        # Unavailable_Marker rather than a label (Requirement 3.6).
        assert result.get("unavailable") is True, (
            f"all-null measures must yield an Unavailable_Marker, got {result!r}"
        )
        # No fabricated Index_Direction / Relative_Strength_State / Alignment
        # (Requirement 3.6 / AD-4): those fields are omitted entirely.
        for field in _FABRICATED_FIELDS:
            assert field not in result, (
                f"unavailable marker must not fabricate {field!r}: {result!r}"
            )
