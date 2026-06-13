"""Property-based test for the simulator's purity and determinism
(trade_manager.py ``simulate_plan``, task 3.9).

Feature: trade-management

This module implements design **Property 8: Purity and determinism**:

    For any ``ManagementPlan``, candle sequence, and configuration, the simulator
    (a) leaves its inputs unchanged — a deep copy taken before the call equals
    the inputs after the call — and (b) returns an identical ``SimulationResult``
    (the ``Exit_Breakdown`` and ``Realized_R``) on repeated invocations of the
    same inputs.

Validates: Requirements 2.8, 3.5.

Strategy: generate varied plans (BUY/SELL, single-target, multi-leg scale-out,
with/without a breakeven trigger, with/without a trailing rule) and candle lists
whose price band overlaps the plan band so targets/stops are actually reached on
many examples (exercising resolved, open, and invalid outcomes). Before the call
we take a ``copy.deepcopy`` of both the plan and the candles; after the call we
assert the live inputs still equal that before-image. We then call
``simulate_plan`` twice on the same inputs and assert the two results are equal.

The sys.path / import and strategy patterns mirror the sibling TM property tests
``tests/test_tm_sim_order_invariance_properties.py`` and
``tests/test_tm_sim_breakeven_properties.py``.
"""

import copy
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

# A single resolved configuration from the documented defaults (the simulator is
# a pure function of plan + candles + config; the config is held fixed here so
# the property isolates purity / determinism over varied plans and candles).
_CONFIG = resolve_trade_manager_config()


# ── Plan strategies ───────────────────────────────────────────────────────────
# Finite, well-behaved floats in a band that overlaps the candle band below so
# targets / stops are reached on many examples (resolved), not just open ones.
_price = st.floats(
    min_value=0.0,
    max_value=200.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)

# Leg fraction in (0.0, 1.0]; multiple legs may oversubscribe, which the
# simulator clamps against the remaining size — purity must hold regardless.
_fraction = st.floats(
    min_value=1e-3,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)

_leg = st.builds(ScaleOutLeg, target=_price, fraction=_fraction)
# One leg (single-target) up to several legs (scale-out).
_legs = st.lists(_leg, min_size=1, max_size=4).map(tuple)

# Breakeven: absent, price-form, or r_multiple-form (Requirement 1.4).
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

# Trailing: absent, ATR-multiple-form, or R-increment-form.
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
    """Build a varied finite ``ManagementPlan`` (BUY/SELL, scale-out, breakeven,
    trailing) exercising every optional field."""
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
# OHLCV dicts (plain mutable dicts, as the live callers pass), well-formed:
# low <= open/close <= high. Mutable dicts are deliberate so a purity violation
# (the simulator writing into a candle) would be caught by the before/after
# deep-copy equality check.
@st.composite
def _candle(draw, timestamp_ms):
    low = draw(_price)
    high = draw(
        st.floats(min_value=low, max_value=200.0, allow_nan=False, allow_infinity=False)
    )
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
def _plan_and_candles(draw):
    """Draw a plan and a list of candles (timestamps not required to be unique —
    purity / determinism must hold for any candle list)."""
    plan = draw(_plans())
    timestamps = draw(
        st.lists(st.integers(min_value=1, max_value=10_000_000), min_size=0, max_size=25)
    )
    candles = [draw(_candle(ts)) for ts in timestamps]
    return plan, candles


# ─────────────────────────────────────────────────────────────────────────────
# Property 8 (task 3.9): Purity and determinism
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 8: Purity and determinism
@settings(max_examples=50, deadline=None)
@given(case=_plan_and_candles())
def test_property_8_purity_and_determinism(case):
    """Feature: trade-management, Property 8: Purity and determinism —
    ``simulate_plan`` (a) leaves its plan and candle inputs unchanged (a deep
    copy taken before the call equals the inputs after it) and (b) returns an
    identical ``SimulationResult`` on repeated invocations of the same inputs.

    Validates: Requirements 2.8, 3.5
    """
    plan, candles = case

    # (a) Purity — capture a before-image via deep copy, then run.
    plan_before = copy.deepcopy(plan)
    candles_before = copy.deepcopy(candles)

    result_first = simulate_plan(plan, candles, _CONFIG)

    # The simulator must not mutate its inputs (Requirement 2.8): the live
    # objects still equal the before-image.
    assert plan == plan_before
    assert candles == candles_before

    # (b) Determinism — a second invocation on the same inputs returns an
    # identical SimulationResult (status, realized_r, fills, residual_fraction,
    # breakeven_moved_at, trailed — all covered by frozen dataclass equality)
    # (Requirement 3.5).
    result_second = simulate_plan(plan, candles, _CONFIG)
    assert result_first == result_second

    # Inputs remain unchanged after the repeated invocation as well.
    assert plan == plan_before
    assert candles == candles_before
