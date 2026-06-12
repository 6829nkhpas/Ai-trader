"""Property-based test for zero-denominator / all-null handling (regime.py, task 3.8).

Feature: regime-detection-gate

This Hypothesis property exercises Property 9 of the design:

  * Property 9 (Requirements 2.6, 2.7) — Zero-denominator measures are null, and
    all-null yields unavailable:
      - for any candle window in which a Regime_Measure's denominator is zero
        (for example a flat, zero-range window), that measure is represented as
        ``null`` in the Regime_Label and no exception is raised; and
      - for any input in which every required Regime_Measure is ``null``,
        ``classify_regime`` returns an Unavailable_Marker rather than a
        Regime_Label.

The witness for the "all measures null" condition is a *fully-flat* candle
window — every bar has ``open == high == low == close`` at the same price across
the whole window, so the price range, the directional movement, and the closed
path are all exactly zero (every measure's denominator is zero). The windows are
generated long enough to clear the minimum-candle / largest-lookback count gate,
so ``classify_regime`` reaches the all-null path rather than the insufficient-
count path (R2.1/R2.3, which is covered by Property 8 in a separate file).

Candles are dict OHLCV records with keys ``open`` / ``high`` / ``low`` /
``close`` / ``volume`` (matching how ``regime.py`` reads candles via
``c.get(...)``).
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import regime  # noqa: E402

# Resolve the configuration once (deterministic for the current environment).
# The count gate is ``max(min_candles, largest_lookback)``; windows are sized to
# clear it comfortably so the all-null path — not the insufficient-count path —
# is exercised.
_CONFIG = regime.resolve_regime_config()
_REQUIRED = max(_CONFIG.min_candles, _CONFIG.largest_lookback)

# Positive finite prices spanning ordinary and extreme magnitudes. A flat window
# at any of these prices has zero range / zero movement, so every measure's
# denominator is zero.
_price = st.one_of(
    st.floats(min_value=0.5, max_value=100_000.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([1.0, 100.0, 12345.6789, 1e-6, 1e6]),
)


@st.composite
def _flat_window(draw):
    """A fully-flat OHLCV window: O == H == L == C at one constant price.

    Length is drawn to comfortably clear the count gate so ``classify_regime``
    reaches the all-null branch rather than the insufficient-count branch.
    """
    p = draw(_price)
    n = draw(st.integers(min_value=_REQUIRED + 5, max_value=_REQUIRED + 90))
    return [
        {"open": p, "high": p, "low": p, "close": p, "volume": 1000.0}
        for _ in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Property 9: Zero-denominator measures are null, and all-null yields unavailable
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 9
@settings(max_examples=150, deadline=None)
@given(window=_flat_window())
def test_property_9_zero_denominator_and_all_null(window):
    """Feature: regime-detection-gate, Property 9: Zero-denominator measures are
    null, and all-null yields unavailable.

    Validates: Requirements 2.6, 2.7
    """
    config = _CONFIG

    # --- Zero-denominator measures are null, and nothing raises (R2.6). On a
    # flat, zero-range window every measure's denominator is zero, so each
    # measure must be represented as ``null`` (the canonical R2.6 example is
    # "zero price range over the window").
    directional_strength = regime.compute_directional_strength(window, config.adx_period)
    choppiness = regime.compute_choppiness(window, config.chop_period)
    efficiency_ratio = regime.compute_efficiency_ratio(window, config.chop_period)
    atr_percentile = regime.compute_atr_percentile(
        window, config.vol_period, config.vol_pctl_window
    )
    bb_width = regime.compute_bb_width(window, config.bb_period)

    assert directional_strength is None, (
        f"directional_strength must be null on a zero-range window, "
        f"got {directional_strength!r}"
    )
    assert choppiness is None, (
        f"choppiness must be null on a zero-range window, got {choppiness!r}"
    )
    assert efficiency_ratio is None, (
        f"efficiency_ratio must be null on a zero-movement window, "
        f"got {efficiency_ratio!r}"
    )
    assert atr_percentile is None, (
        f"atr_percentile must be null on a zero-range window (zero denominator), "
        f"got {atr_percentile!r}"
    )
    assert bb_width is None, (
        f"bb_width must be null on a zero-range window (zero denominator), "
        f"got {bb_width!r}"
    )

    # --- All-null yields an Unavailable_Marker (R2.7). With every required
    # measure null, classify_regime must return an Unavailable_Marker rather than
    # a Regime_Label, and must not raise.
    result = regime.classify_regime(window, config)
    assert isinstance(result, dict)
    assert result.get("unavailable") is True, (
        "a fully-flat, sufficient-length window has every required measure null, "
        f"so classify_regime must return an Unavailable_Marker; got: {result!r}"
    )
