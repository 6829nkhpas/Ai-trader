"""Property-based test for zero initial stop distance (trade_manager.py, task 3.13).

Feature: trade-management

This module implements design **Property 12: Zero initial stop distance is
invalid, not a divide-by-zero**:

    For any ``Management_Plan`` whose ``entry`` equals its ``initial_stop`` (a
    zero initial stop distance), ``simulate_plan`` reports ``status == "invalid"``
    with ``realized_r is None`` and does NOT raise a division error — the R
    measurement would otherwise divide by the zero stop distance.

Validates: Requirements 3.4.

Each generated case constructs a plan with ``entry == initial_stop`` for an
arbitrary side (BUY/SELL), an arbitrary number of scale-out legs at arbitrary
target prices and fractions, optional breakeven and trailing rules, and an
arbitrary candle window (including candles that would otherwise reach targets or
the stop). Regardless of the candles, the zero stop distance must short-circuit
to ``invalid`` before any R division occurs, and the call must never raise.

A sanity counterpart confirms that the SAME plan shape with a NON-zero stop
distance is NOT reported as ``invalid`` (so ``invalid`` is specific to the
zero-distance degeneracy, not an artifact of the generated shape).

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_sim_stop_residual_properties.py`` and
``tests/test_tm_plan_roundtrip_properties.py``.
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
    TradeManagerConfig,
    simulate_plan,
)

# A resolved configuration is required by the signature but irrelevant to this
# property: the zero stop distance short-circuits before any configured default
# is consulted.
_CONFIG = TradeManagerConfig(
    default_first_target_r=1.0,
    default_first_target_fraction=0.5,
    default_breakeven_trigger_r=1.0,
    default_trail_atr_multiple=1.5,
    min_blended_reward_to_risk=2.0,
)

_price = st.floats(min_value=50.0, max_value=1000.0, allow_nan=False, allow_infinity=False)


@st.composite
def _candle(draw, ts):
    """An arbitrary finite OHLCV candle at timestamp ``ts``."""
    base = draw(_price)
    spread = draw(st.floats(min_value=0.0, max_value=60.0, allow_nan=False, allow_infinity=False))
    high = base + spread
    low = base - spread
    return {
        "open": base,
        "high": high,
        "low": low,
        "close": draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False)),
        "volume": draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)),
        "timestamp_ms": ts,
    }


@st.composite
def _zero_stop_cases(draw):
    """Build a (plan, candles) case whose ``entry == initial_stop``.

    The legs, fractions, breakeven, trailing, atr_14, and candle window are all
    arbitrary — the only invariant is the zero initial stop distance.
    """
    side = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(_price)

    # Arbitrary legs (one or more), arbitrary targets and fractions.
    n = draw(st.integers(min_value=1, max_value=4))
    legs = tuple(
        ScaleOutLeg(
            target=draw(_price),
            fraction=draw(st.floats(min_value=0.05, max_value=1.0, allow_nan=False, allow_infinity=False)),
        )
        for _ in range(n)
    )

    # Optionally a breakeven trigger and/or a trailing rule.
    breakeven = None
    if draw(st.booleans()):
        breakeven = BreakevenTrigger(
            r_multiple=draw(st.floats(min_value=0.1, max_value=5.0, allow_nan=False, allow_infinity=False))
        )
    trailing = None
    if draw(st.booleans()):
        trailing = TrailingStop(
            atr_multiple=draw(st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False))
        )
    atr_14 = draw(st.one_of(st.none(), st.floats(min_value=0.1, max_value=50.0, allow_nan=False, allow_infinity=False)))

    plan = ManagementPlan(
        action=side,
        entry=entry,
        initial_stop=entry,  # zero initial stop distance (entry == initial_stop)
        legs=legs,
        breakeven=breakeven,
        trailing=trailing,
        atr_14=atr_14,
    )

    num_candles = draw(st.integers(min_value=0, max_value=6))
    candles = [draw(_candle(ts=1000 + i * 1000)) for i in range(num_candles)]

    return plan, candles


# ─────────────────────────────────────────────────────────────────────────────
# Property 12 (task 3.13): Zero initial stop distance is invalid
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 12: Zero initial stop distance is invalid, not a divide-by-zero
@settings(max_examples=50, deadline=None)
@given(case=_zero_stop_cases())
def test_property_12_zero_initial_stop_distance_is_invalid(case):
    """Feature: trade-management, Property 12: Zero initial stop distance is
    invalid, not a divide-by-zero — a plan whose entry equals its initial stop
    is reported as ``invalid`` with no ``realized_r`` and never raises.

    Validates: Requirements 3.4
    """
    plan, candles = case

    # Must not raise (no divide-by-zero on the zero stop distance).
    result = simulate_plan(plan, candles, _CONFIG)

    assert result.status == "invalid"
    assert result.realized_r is None
    # No fabricated fills for an invalid plan.
    assert result.fills == ()


# Feature: trade-management, Property 12: Zero initial stop distance is invalid, not a divide-by-zero
@settings(max_examples=50, deadline=None)
@given(case=_zero_stop_cases(), stop_distance=st.floats(min_value=1.0, max_value=40.0, allow_nan=False, allow_infinity=False))
def test_property_12_nonzero_counterpart_is_not_invalid(case, stop_distance):
    """Sanity counterpart: the SAME plan shape with a NON-zero stop distance is
    NOT reported as ``invalid`` — confirming ``invalid`` is specific to the
    zero-distance degeneracy, not an artifact of the generated shape.

    Validates: Requirements 3.4
    """
    plan, candles = case

    # Move the initial stop to the loss side so the stop distance is non-zero.
    if plan.action == "BUY":
        moved_stop = plan.entry - stop_distance
    else:
        moved_stop = plan.entry + stop_distance

    nonzero_plan = ManagementPlan(
        action=plan.action,
        entry=plan.entry,
        initial_stop=moved_stop,
        legs=plan.legs,
        breakeven=plan.breakeven,
        trailing=plan.trailing,
        atr_14=plan.atr_14,
    )

    result = simulate_plan(nonzero_plan, candles, _CONFIG)

    # With a real stop distance, the result is resolved or open — never invalid.
    assert result.status in ("resolved", "open")
