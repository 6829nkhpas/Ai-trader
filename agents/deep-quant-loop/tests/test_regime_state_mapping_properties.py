"""Property-based test for well-formed states matching the mapping (regime.py, task 3.5).

Feature: regime-detection-gate

This Hypothesis property exercises ``classify_regime``'s state classification:

  * Property 5 (1.8, 1.9) — Label states are well-formed and match the threshold
    mapping: for any Regime_Label produced from sufficient candles, the
    Trend_State is exactly one of ``trending`` / ``ranging`` / ``transitional``
    and the Volatility_State is exactly one of ``low`` / ``normal`` / ``high``,
    and each equals the value dictated by comparing the corresponding
    Regime_Measures (carried in the label) against the configured thresholds per
    the design's Trend_State / Volatility_State mapping tables.

The check recomputes the expected state directly from the label's *own* measures
using the classifier's total mapping functions (``classify_trend_state`` /
``classify_volatility_state``), so the test pins the label states to the mapping
the design specifies rather than to whatever the classifier happened to emit.

Candles are dict OHLCV records with keys ``open`` / ``high`` / ``low`` /
``close`` / ``volume`` (matching how ``regime.py`` reads candles via
``c.get(...)``). The configuration is drawn across the full threshold space
(varied cutoffs and small-but-real lookbacks) so the mapping is stressed at many
threshold positions; candle counts are drawn comfortably above the resolved
sufficiency gate so a Regime_Label (not an Unavailable_Marker) is produced.
"""

import os
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from regime import (  # noqa: E402
    TREND_STATES,
    VOLATILITY_STATES,
    RegimeConfig,
    classify_regime,
    classify_trend_state,
    classify_volatility_state,
)

# ── Generators ────────────────────────────────────────────────────────────────

# Finite, positive, bounded price components. Bounded to keep variance / stddev
# and true-range arithmetic well away from overflow while spanning a realistic
# range. A wide spread of moves drives the measures across their domains so
# different (trend, volatility) states are exercised.
_price = st.floats(
    min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _valid_candle(draw):
    """A dict OHLCV candle whose every field is a finite numeric value and whose
    ``high`` / ``low`` correctly bracket ``open`` / ``close`` (a plausible bar)."""
    open_ = draw(_price)
    close = draw(_price)
    high = max(open_, close) + draw(
        st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False)
    )
    low = max(
        min(open_, close)
        - draw(
            st.floats(
                min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False
            )
        ),
        0.01,
    )
    return {"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}


@st.composite
def _config_and_candles(draw):
    """Draw a (RegimeConfig, candles) pair guaranteed to clear the gate.

    Lookbacks are small-but-real so generation stays cheap; cutoffs span the full
    threshold range (with ``vol_low_pctl < vol_high_pctl`` enforced) so the
    Trend_State / Volatility_State mappings are exercised at many positions. The
    candle count is drawn at or above the resolved sufficiency gate
    ``max(min_candles, largest_lookback)`` so ``classify_regime`` can produce a
    Regime_Label rather than an Unavailable_Marker.
    """
    adx_period = draw(st.integers(min_value=2, max_value=10))
    chop_period = draw(st.integers(min_value=2, max_value=10))
    vol_period = draw(st.integers(min_value=2, max_value=10))
    vol_pctl_window = draw(st.integers(min_value=5, max_value=30))
    bb_period = draw(st.integers(min_value=2, max_value=10))
    min_candles = draw(st.integers(min_value=5, max_value=20))

    adx_trend_cutoff = draw(
        st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
    )
    chop_ranging_cutoff = draw(
        st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
    )
    # Enforce the strict low < high ordering the resolver guarantees.
    vol_low_pctl = draw(
        st.floats(min_value=0.0, max_value=90.0, allow_nan=False, allow_infinity=False)
    )
    vol_high_pctl = draw(
        st.floats(
            min_value=vol_low_pctl + 1.0,
            max_value=100.0,
            allow_nan=False,
            allow_infinity=False,
        )
    )

    config = RegimeConfig(
        adx_period=adx_period,
        chop_period=chop_period,
        vol_period=vol_period,
        vol_pctl_window=vol_pctl_window,
        bb_period=bb_period,
        adx_trend_cutoff=adx_trend_cutoff,
        chop_ranging_cutoff=chop_ranging_cutoff,
        vol_low_pctl=vol_low_pctl,
        vol_high_pctl=vol_high_pctl,
        min_candles=min_candles,
    )

    required = max(config.min_candles, config.largest_lookback)
    n = draw(st.integers(min_value=required, max_value=required + 25))
    candles = draw(
        st.lists(_valid_candle(), min_size=n, max_size=n)
    )
    return config, candles


# ─────────────────────────────────────────────────────────────────────────────
# Property 5: Label states are well-formed and match the threshold mapping
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 5
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.large_base_example],
)
@given(data=_config_and_candles())
def test_property_5_states_well_formed_and_match_mapping(data):
    """Validates: Requirements 1.8, 1.9

    For any Regime_Label produced from sufficient candles:
      * ``trend_state`` is exactly one of trending / ranging / transitional,
      * ``volatility_state`` is exactly one of low / normal / high, and
      * each equals the value dictated by comparing the label's own
        Regime_Measures against the configured thresholds per the mapping tables
        (recomputed here via ``classify_trend_state`` / ``classify_volatility_state``).
    """
    config, candles = data
    result = classify_regime(candles, config)

    # Only a Regime_Label carries states; an Unavailable_Marker omits them and is
    # out of scope for this property. The generator targets sufficient candles so
    # labels are the common case, but a degenerate (all-null) window can still
    # yield a marker — skip those.
    if result.get("unavailable"):
        return

    trend_state = result["trend_state"]
    volatility_state = result["volatility_state"]
    measures = result["measures"]

    # Well-formed: each state is drawn from its enumeration (R1.8, R1.9).
    assert trend_state in TREND_STATES, f"trend_state {trend_state!r} not in {TREND_STATES}"
    assert (
        volatility_state in VOLATILITY_STATES
    ), f"volatility_state {volatility_state!r} not in {VOLATILITY_STATES}"

    # Matches the mapping: each state equals the value dictated by comparing the
    # label's own measures against the configured thresholds. Trend uses the
    # directional-strength + choppiness measures; volatility uses the
    # ATR-percentile (corroborated by BB-width).
    expected_trend = classify_trend_state(
        measures["directional_strength"], measures["choppiness"], config
    )
    expected_vol = classify_volatility_state(
        measures["atr_percentile"], measures["bb_width"], config
    )

    assert trend_state == expected_trend, (
        f"trend_state {trend_state!r} != mapping {expected_trend!r} "
        f"(adx={measures['directional_strength']!r}, chop={measures['choppiness']!r}, "
        f"cutoffs adx>={config.adx_trend_cutoff}, chop>={config.chop_ranging_cutoff})"
    )
    assert volatility_state == expected_vol, (
        f"volatility_state {volatility_state!r} != mapping {expected_vol!r} "
        f"(atr_pctl={measures['atr_percentile']!r}, "
        f"low<{config.vol_low_pctl}, high>{config.vol_high_pctl})"
    )
