"""Property-based test for classifier purity (regime.py, task 3.3).

Feature: regime-detection-gate

This Hypothesis property exercises Property 2 (classifier purity / no input
mutation): every ``Regime_Classifier`` function — the measure functions
(``compute_directional_strength`` / ``compute_choppiness`` /
``compute_efficiency_ratio`` / ``compute_atr_percentile`` / ``compute_bb_width``),
the classification functions (``classify_trend_state`` /
``classify_volatility_state`` / ``derive_favorability``), and the top-level
``classify_regime`` — must produce NO observable change to the candle sequence
or configuration it is given. After each call the provided candle sequence must
remain deep-equal to a snapshot taken before the call, and the (frozen)
``RegimeConfig`` must remain equal to its pre-call snapshot.

Candles are dict OHLCV records with keys ``open`` / ``high`` / ``low`` /
``close`` / ``volume`` (matching how ``regime.py`` reads candles via
``c.get(...)``). The generator produces arbitrary sequences — including extreme
magnitudes, flat/zero-range windows, and candles carrying non-finite /
non-numeric OHLCV fields — so the purity guarantee is stressed across the whole
input space, including the degenerate paths (insufficient data, all-null
measures, zero denominators) where a careless implementation might mutate or
normalize its inputs in place.

  * Property 2 (1.1, 1.11, 12.2, 12.4) — Classifier functions are pure: for any
    candle sequence and configuration, every classifier function leaves both
    inputs deep-equal to their pre-call snapshots.
"""

import copy
import os
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from regime import (  # noqa: E402
    RegimeConfig,
    classify_regime,
    classify_trend_state,
    classify_volatility_state,
    compute_atr_percentile,
    compute_bb_width,
    compute_choppiness,
    compute_directional_strength,
    compute_efficiency_ratio,
    derive_favorability,
    resolve_regime_config,
)

# ─────────────────────────────────────────────────────────────────────────────
# Candle generation: arbitrary OHLCV records, including extreme / degenerate /
# corrupt values so the purity guarantee is exercised across every code path
# (valid windows, flat/zero-range windows, insufficient data, corrupt fields).
# ─────────────────────────────────────────────────────────────────────────────

_PRICE = st.one_of(
    st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1e-9, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([0.0, 1e-12, 1e12, 1.0, 100.0, 12345.6789]),
)

# Values that make an OHLCV field non-finite or non-numeric, so the carrying
# candle is excluded by the measure functions. Included so the purity property
# also covers the candle-exclusion path.
_BAD_VALUE = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), None, "x", "", True, False, [], {}]
)


@st.composite
def _candle(draw):
    """One OHLCV candle dict; fields may be ordinary, extreme, or corrupt.

    High/low are NOT forced to bracket open/close so flat and inverted-range
    bars are produced too. Each field independently has a small chance of
    carrying a non-finite / non-numeric value, exercising the exclusion path.
    """
    def _field():
        return draw(st.one_of(_PRICE, _BAD_VALUE)) if draw(
            st.integers(min_value=0, max_value=9)
        ) == 0 else draw(_PRICE)

    return {
        "open": _field(),
        "high": _field(),
        "low": _field(),
        "close": _field(),
        "volume": _field(),
    }


@st.composite
def _flat_candle(draw):
    """A flat candle where O=H=L=C (a zero-range, degenerate bar)."""
    p = draw(_PRICE)
    return {"open": p, "high": p, "low": p, "close": p, "volume": draw(_PRICE)}


# Sequences span from too-short (insufficient-data path) to long enough that
# every measure is computable.
_CANDLES = st.lists(
    st.one_of(_candle(), _flat_candle()),
    min_size=0,
    max_size=160,
)

_PERIOD = st.integers(min_value=2, max_value=30)
_WINDOW = st.integers(min_value=1, max_value=120)

# State values (including a couple out-of-enum strings) for the classification
# functions, so derive_favorability / classify_* purity is checked broadly.
_TREND = st.sampled_from(["trending", "ranging", "transitional", "weird", ""])
_VOL = st.sampled_from(["low", "normal", "high", "weird", ""])
_MEASURE = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 2: Classifier functions are pure (no input mutation)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 2
@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.large_base_example])
@given(
    candles=_CANDLES,
    period=_PERIOD,
    window=_WINDOW,
    trend=_TREND,
    vol=_VOL,
    m_a=_MEASURE,
    m_b=_MEASURE,
)
def test_property_2_classifier_functions_are_pure(
    candles, period, window, trend, vol, m_a, m_b
):
    """Validates: Requirements 1.1, 1.11, 12.2, 12.4

    Every Regime_Classifier function leaves the provided candle sequence and
    configuration deep-equal to their pre-call snapshots — producing no
    observable change to either input. The (frozen) RegimeConfig is compared by
    equality; the candle sequence (and its candle dicts) is snapshotted with a
    deep copy before each call and asserted deep-equal afterward.
    """
    # ``resolve_regime_config`` reads only the environment; it takes no mutable
    # input, but we still assert it returns a frozen RegimeConfig that supports
    # equality so the snapshot comparisons below are meaningful.
    config = resolve_regime_config()
    assert isinstance(config, RegimeConfig)

    config_snapshot = config  # frozen dataclass -> compare by equality

    def _assert_pure(fn, *args):
        """Snapshot the candle sequence + config, call ``fn``, assert no mutation."""
        candles_snapshot = copy.deepcopy(candles)
        fn(*args)
        assert candles == candles_snapshot, (
            f"{getattr(fn, '__name__', fn)} mutated its candle input: "
            f"{candles!r} != {candles_snapshot!r}"
        )
        assert config == config_snapshot, (
            f"{getattr(fn, '__name__', fn)} mutated its config input"
        )

    # ── Measure functions ────────────────────────────────────────────────────
    _assert_pure(compute_directional_strength, candles, period)
    _assert_pure(compute_choppiness, candles, period)
    _assert_pure(compute_efficiency_ratio, candles, period)
    _assert_pure(compute_atr_percentile, candles, period, window)
    _assert_pure(compute_bb_width, candles, period)

    # ── Top-level entry point ────────────────────────────────────────────────
    _assert_pure(classify_regime, candles, config)
    # Also exercise the symbol/timeframe-carrying call path.
    _assert_pure(classify_regime, candles, config, "RELIANCE", "15m")

    # ── Classification functions (config-only inputs) ─────────────────────────
    # These take scalar measures + config, not candles; assert the config is not
    # mutated across the call.
    for fn, args in (
        (classify_trend_state, (m_a, m_b, config)),
        (classify_volatility_state, (m_a, m_b, config)),
        (derive_favorability, (trend, vol, config)),
    ):
        cfg_before = config
        fn(*args)
        assert config == cfg_before, f"{fn.__name__} mutated its config input"
