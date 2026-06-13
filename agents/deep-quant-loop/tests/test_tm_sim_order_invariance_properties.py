"""Property-based test for candle order-invariance (trade_manager.py, task 3.2).

Feature: trade-management

This module implements design **Property 1: Candle order-invariance (confluence)**:

    For any ``ManagementPlan``, configuration, and multiset of candles,
    simulating the plan against the candles in ANY input order produces an
    identical ``Exit_Breakdown`` (the ``fills``) and ``Realized_R`` — indeed an
    identical full ``SimulationResult`` — because the simulator sorts candles by
    ascending timestamp (then by OHLC, a total order) before processing.

Validates: Requirements 2.1.

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_plan_roundtrip_properties.py`` and
``tests/test_tm_config_default_fallback_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (trade_manager.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from trade_manager import (  # noqa: E402
    BreakevenTrigger,
    ManagementPlan,
    ScaleOutLeg,
    TrailingStop,
    resolve_trade_manager_config,
    simulate_plan,
)

# A single resolved configuration, built from the documented defaults via the
# canonical resolver (the simulator is a pure function of plan + candles + config;
# the config value is held fixed so the property isolates candle ORDER).
_CONFIG = resolve_trade_manager_config()


# ── Plan strategies ───────────────────────────────────────────────────────────
# Finite, well-behaved floats. Prices are constrained to a band that overlaps the
# candle band below so that targets / stops are actually reached on many examples
# (exercising resolved, open, and invalid outcomes), not just trivially open ones.
_price = st.floats(
    min_value=0.0,
    max_value=200.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)

# Leg fraction in (0.0, 1.0]; multiple legs may oversubscribe, which the simulator
# clamps against the remaining size — order-invariance must hold regardless.
_fraction = st.floats(
    min_value=1e-3,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)

_leg = st.builds(ScaleOutLeg, target=_price, fraction=_fraction)
_legs = st.lists(_leg, min_size=1, max_size=4).map(tuple)

_breakeven = st.one_of(
    st.none(),
    st.builds(BreakevenTrigger, price=_price, r_multiple=st.none()),
    st.builds(
        BreakevenTrigger,
        price=st.none(),
        r_multiple=st.floats(
            min_value=1e-3, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
    ),
)

_trailing = st.one_of(
    st.none(),
    st.builds(
        TrailingStop,
        atr_multiple=st.floats(
            min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
        r_increment=st.none(),
    ),
    st.builds(
        TrailingStop,
        atr_multiple=st.none(),
        r_increment=st.floats(
            min_value=1e-3, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
    ),
)

_atr_14 = st.one_of(
    st.none(),
    st.floats(min_value=1e-3, max_value=50.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _plans(draw):
    """Build a finite ``ManagementPlan`` exercising every optional field."""
    return ManagementPlan(
        action=draw(st.sampled_from(["BUY", "SELL"])),
        entry=draw(_price),
        initial_stop=draw(_price),
        legs=draw(_legs),
        breakeven=draw(_breakeven),
        trailing=draw(_trailing),
        atr_14=draw(_atr_14),
    )


# ── Candle strategy ───────────────────────────────────────────────────────────
# Candles are OHLCV dicts with DISTINCT timestamps (so the chronological order is
# unambiguous and the only thing the shuffle changes is input order). Each candle
# is well-formed: low <= open/close <= high.
@st.composite
def _candle(draw, timestamp_ms):
    low = draw(_price)
    high = draw(st.floats(min_value=low, max_value=200.0, allow_nan=False, allow_infinity=False))
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    volume = draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))
    return {
        "timestamp_ms": timestamp_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


@st.composite
def _plan_candles_and_shuffle(draw):
    """Draw a plan, a list of distinct-timestamp candles, and a permutation."""
    plan = draw(_plans())
    timestamps = draw(
        st.lists(st.integers(min_value=1, max_value=10_000_000), min_size=0, max_size=25, unique=True)
    )
    candles = [draw(_candle(ts)) for ts in timestamps]
    # A permutation of the SAME multiset of candles (uses Hypothesis's permutation
    # strategy; equivalent to a seeded random.shuffle but shrinks cleanly).
    shuffled = draw(st.permutations(candles))
    return plan, candles, list(shuffled)


# ─────────────────────────────────────────────────────────────────────────────
# Property 1 (task 3.2): Candle order-invariance (confluence)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 1: Candle order-invariance (confluence)
@settings(max_examples=200, deadline=None)
@given(case=_plan_candles_and_shuffle())
def test_property_1_candle_order_invariance(case):
    """Feature: trade-management, Property 1: Candle order-invariance (confluence) —
    simulating a plan against the candles in any input order yields an identical
    ``SimulationResult`` (status, realized_r, fills, residual_fraction,
    breakeven_moved_at, trailed), because the simulator sorts candles by
    ascending timestamp before processing.

    Validates: Requirements 2.1
    """
    plan, candles, shuffled = case

    result_ordered = simulate_plan(plan, candles, _CONFIG)
    result_shuffled = simulate_plan(plan, shuffled, _CONFIG)

    # The full SimulationResult is compared (frozen dataclass equality covers
    # status, realized_r, the fills tuple — the Exit_Breakdown — residual_fraction,
    # breakeven_moved_at, and trailed).
    assert result_ordered == result_shuffled
