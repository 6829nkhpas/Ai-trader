"""Property-based tests for per-strike OI-buildup classification (options.py, task 5.3).

Feature: options-analytics-engine

This Hypothesis property exercises the deterministic per-strike OI-buildup
classifier (:func:`options.classify_oi_buildup`) across the full (ΔOI, Δprice)
input space — finite values of every sign, exact zeros, within-dead-band
magnitudes, and the non-finite / non-numeric degenerate cases (``None``, ``NaN``,
``±inf``) — together with varied resolved configs carrying assorted dead-bands,
asserting the totality + exact-mapping-table invariant:

  * Property 7 (3.1, 3.4, 3.5) — Per-strike OI buildup is a total sign mapping:
        ``classify_oi_buildup`` returns exactly one of the five labels for ANY
        input (totality), and the returned label matches the design's
        sign(ΔOI) × sign(Δprice) table after dead-banding —
            rising OI + rising price  -> long_buildup
            rising OI + falling price -> short_buildup
            falling OI + falling price -> long_unwinding
            falling OI + rising price  -> short_covering
            zero / within-dead-band ΔOI or Δprice -> neutral.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options  # noqa: E402
from options import (  # noqa: E402
    BUILDUP_LONG,
    BUILDUP_LONG_UNWINDING,
    BUILDUP_NEUTRAL,
    BUILDUP_SHORT,
    BUILDUP_SHORT_COVERING,
    OptionsConfig,
    classify_oi_buildup,
    resolve_options_config,
)

# The complete, closed set of labels the classifier may ever return.
_ALL_LABELS = frozenset(
    {
        BUILDUP_LONG,
        BUILDUP_SHORT,
        BUILDUP_SHORT_COVERING,
        BUILDUP_LONG_UNWINDING,
        BUILDUP_NEUTRAL,
    }
)


def _expected_label(d_oi, d_price, oi_eps, price_eps):
    """Reference implementation of the design's mapping table (Property 7).

    Mirrors the spec independently of the production code: a non-finite /
    non-numeric change carries no direction (-> neutral); a change whose
    magnitude is within its dead-band is "no change" (-> neutral); otherwise the
    sign(ΔOI) x sign(Δprice) quadrant selects the label.
    """
    def _finite(x):
        return (
            isinstance(x, (int, float))
            and not isinstance(x, bool)
            and math.isfinite(x)
        )

    if not (_finite(d_oi) and _finite(d_price)):
        return BUILDUP_NEUTRAL

    # A non-finite / negative epsilon is not a valid dead-band -> exact-zero band.
    if not (_finite(oi_eps) and oi_eps >= 0.0):
        oi_eps = 0.0
    if not (_finite(price_eps) and price_eps >= 0.0):
        price_eps = 0.0

    oi_sign = 0
    if abs(d_oi) > oi_eps:
        oi_sign = 1 if d_oi > 0.0 else -1
    price_sign = 0
    if abs(d_price) > price_eps:
        price_sign = 1 if d_price > 0.0 else -1

    if oi_sign == 0 or price_sign == 0:
        return BUILDUP_NEUTRAL
    if oi_sign > 0:
        return BUILDUP_LONG if price_sign > 0 else BUILDUP_SHORT
    return BUILDUP_SHORT_COVERING if price_sign > 0 else BUILDUP_LONG_UNWINDING


# Arbitrary ΔOI / Δprice: spans every sign, exact zero, tiny within-band values,
# large magnitudes, and the non-finite / non-numeric degenerate inputs the
# classifier must tolerate without raising (None, NaN, ±inf, a stray string).
_delta_value = st.one_of(
    st.floats(allow_nan=True, allow_infinity=True),          # finite + NaN/±inf
    st.floats(min_value=-1e9, max_value=1e9),                # in-range finite
    st.just(0.0),
    st.just(-0.0),
    st.floats(min_value=-1.0, max_value=1.0),                # small (dead-band range)
    st.integers(min_value=-1000, max_value=1000),            # int changes
    st.none(),                                               # missing -> neutral
    st.text(max_size=4),                                     # non-numeric garbage
)

# Non-negative finite dead-bands plus the documented default of 0.0.
_epsilon = st.one_of(
    st.just(0.0),
    st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
)


@settings(max_examples=100)
@given(d_oi=_delta_value, d_price=_delta_value, oi_eps=_epsilon, price_eps=_epsilon)
def test_property_7_per_strike_buildup_is_a_total_sign_mapping(
    d_oi, d_price, oi_eps, price_eps
):
    # Feature: options-analytics-engine, Property 7: Per-strike OI buildup is a total sign mapping
    """Feature: options-analytics-engine, Property 7: Per-strike OI buildup is a
    total sign mapping — for ANY ΔOI and Δprice (including None/NaN/±inf and
    non-numeric inputs) and ANY dead-band configuration, ``classify_oi_buildup``
    returns exactly one of the five labels (totality) and that label matches the
    design's sign(ΔOI) × sign(Δprice) mapping table after dead-banding.

    Validates: Requirements 3.1, 3.4, 3.5
    """
    # Build a config carrying the generated dead-bands on top of the resolved
    # defaults (so every other field stays valid / in range).
    base = resolve_options_config()
    config = OptionsConfig(
        risk_free_rate=base.risk_free_rate,
        iv_tolerance=base.iv_tolerance,
        iv_max_iterations=base.iv_max_iterations,
        iv_min_vol=base.iv_min_vol,
        iv_max_vol=base.iv_max_vol,
        oi_wall_min_oi=base.oi_wall_min_oi,
        buildup_oi_epsilon=oi_eps,
        buildup_price_epsilon=price_eps,
    )

    # Totality: never raises and always returns one of the five labels.
    label = classify_oi_buildup(d_oi, d_price, config)
    assert label in _ALL_LABELS

    # Exact mapping: the returned label matches the independent reference table.
    assert label == _expected_label(d_oi, d_price, oi_eps, price_eps)


@settings(max_examples=100)
@given(
    d_oi=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    d_price=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
def test_property_7_default_config_exact_zero_dead_band_quadrants(d_oi, d_price):
    # Feature: options-analytics-engine, Property 7: Per-strike OI buildup is a total sign mapping
    """Feature: options-analytics-engine, Property 7: Per-strike OI buildup is a
    total sign mapping — with the documented default (zero) dead-bands, finite
    changes map exactly by the strict sign of each: nonzero ΔOI and nonzero
    Δprice select the four directional quadrants and an exact-zero on either side
    yields ``neutral``.

    Validates: Requirements 3.1, 3.4, 3.5
    """
    config = resolve_options_config()  # default epsilons are 0.0
    label = classify_oi_buildup(d_oi, d_price, config)
    assert label in _ALL_LABELS

    if d_oi == 0.0 or d_price == 0.0:
        assert label == BUILDUP_NEUTRAL
    elif d_oi > 0.0 and d_price > 0.0:
        assert label == BUILDUP_LONG
    elif d_oi > 0.0 and d_price < 0.0:
        assert label == BUILDUP_SHORT
    elif d_oi < 0.0 and d_price > 0.0:
        assert label == BUILDUP_SHORT_COVERING
    else:  # d_oi < 0.0 and d_price < 0.0
        assert label == BUILDUP_LONG_UNWINDING
