"""Property-based test for breakeven advancing the active stop to entry
(trade_manager.py ``simulate_plan``, task 3.5).

Feature: trade-management

This module implements design **Property 4: Breakeven advances the active stop
to entry**:

    For any Management_Plan with a Breakeven_Trigger, once the trigger is reached
    the active stop equals the entry price for every candle evaluated thereafter,
    so a later candle touching entry resolves the residual at R = 0 — never at the
    original stop.

Validates: Requirements 2.4.

Scenario construction (mirrored for BUY and SELL): a plan with a breakeven
trigger (generated in BOTH price-form and r_multiple-form) and a single
scale-out target placed strictly *beyond* the breakeven level, fed two candles:

  1. a "trigger" candle whose range reaches the Breakeven_Trigger WITHOUT
     reaching the original stop and WITHOUT reaching the (further) target, so the
     simulator advances the active stop to entry but fills no leg; then
  2. a "dip-to-entry" candle that touches the entry price.

Because the active stop has been advanced to entry, the second candle resolves
the entire residual at the entry price: the residual ``LegFill`` is at the entry
price with ``leg_r`` ≈ 0.0 and ``kind == "breakeven-stop"`` — NOT at the original
stop-loss price (which would be ``leg_r`` ≈ −1.0 with ``kind == "stop"``). We also
assert ``breakeven_moved_at`` is set and the overall ``realized_r`` ≈ 0.0.

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_plan_roundtrip_properties.py`` and
``tests/test_tm_validator_breakeven_properties.py``.
"""

import math
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
    resolve_trade_manager_config,
    simulate_plan,
)


def _finite(min_value, max_value):
    return st.floats(
        min_value=min_value,
        max_value=max_value,
        allow_nan=False,
        allow_infinity=False,
    )


@st.composite
def _breakeven_scenarios(draw):
    """Build a direction-consistent plan with a breakeven trigger and a two-candle
    sequence that reaches the trigger then dips back to entry.

    Returns ``(plan, candles, entry, initial_stop, t1)`` where ``t1`` is the
    timestamp of the trigger candle (the expected ``breakeven_moved_at``).
    """
    side = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(_finite(100.0, 1000.0))
    stop_distance = draw(_finite(1.0, 50.0))
    # gap from entry to the breakeven level, and from the breakeven level to the
    # (further) scale-out target — the target sits strictly BEYOND breakeven so
    # the trigger candle never fills the leg.
    be_gap = draw(_finite(1.0, 20.0))
    tgt_gap = draw(_finite(1.0, 20.0))
    # Express the trigger in price-form OR r_multiple-form (Requirement 1.4).
    form = draw(st.sampled_from(["price", "r_multiple"]))

    t1 = draw(st.integers(min_value=1_000, max_value=2_000_000))
    t2 = t1 + draw(st.integers(min_value=1, max_value=10_000))

    if side == "BUY":
        initial_stop = entry - stop_distance          # below entry
        be_level = entry + be_gap                      # above entry
        target = be_level + tgt_gap                    # strictly beyond breakeven
        # Trigger candle: high reaches the breakeven level (with margin) but stays
        # below the target; low stays above the original stop.
        c1_high = be_level + tgt_gap * 0.25            # in (be_level, target)
        c1_low = entry                                 # above initial_stop
        # Dip-to-entry candle: low touches entry (active stop is now entry).
        c2_high = entry + be_gap * 0.5
        c2_low = entry                                 # touches entry exactly
    else:  # SELL (mirror image)
        initial_stop = entry + stop_distance           # above entry
        be_level = entry - be_gap                       # below entry
        target = be_level - tgt_gap                     # strictly beyond breakeven
        c1_low = be_level - tgt_gap * 0.25              # in (target, be_level)
        c1_high = entry                                 # below initial_stop
        c2_low = entry - be_gap * 0.5
        c2_high = entry                                 # touches entry exactly

    if form == "price":
        breakeven = BreakevenTrigger(price=be_level)
    else:
        # R-multiple of progress from entry toward the first target.
        r_multiple = abs(be_level - entry) / stop_distance
        breakeven = BreakevenTrigger(r_multiple=r_multiple)

    plan = ManagementPlan(
        action=side,
        entry=entry,
        initial_stop=initial_stop,
        legs=(ScaleOutLeg(target=target, fraction=1.0),),
        breakeven=breakeven,
        trailing=None,
        atr_14=None,
    )

    def candle(ts, lo, hi):
        # open/close kept within [low, high]; the breakeven path never reads them.
        return {
            "timestamp_ms": ts,
            "open": (lo + hi) / 2.0,
            "high": hi,
            "low": lo,
            "close": (lo + hi) / 2.0,
            "volume": 1000.0,
        }

    candles = [candle(t1, c1_low, c1_high), candle(t2, c2_low, c2_high)]
    return plan, candles, entry, initial_stop, t1


# ─────────────────────────────────────────────────────────────────────────────
# Property 4 (task 3.5): Breakeven advances the active stop to entry
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 4: Breakeven advances the active stop to entry
@settings(max_examples=50, deadline=None)
@given(scenario=_breakeven_scenarios())
def test_property_4_breakeven_advances_stop_to_entry(scenario):
    """Feature: trade-management, Property 4: Breakeven advances the active stop
    to entry — once the Breakeven_Trigger is reached the active stop equals entry
    for every subsequent candle, so a later candle touching entry resolves the
    residual at the entry price (R ≈ 0, kind ``breakeven-stop``) rather than at
    the original stop (R ≈ −1, kind ``stop``).

    Validates: Requirements 2.4
    """
    plan, candles, entry, initial_stop, t1 = scenario
    config = resolve_trade_manager_config()

    result = simulate_plan(plan, candles, config)

    # The plan fully resolves on the dip-to-entry candle.
    assert result.status == "resolved"

    # Breakeven was triggered on the first candle.
    assert result.breakeven_moved_at == t1

    # The trigger candle filled no leg (the target sits strictly beyond
    # breakeven), so the entire position is closed by a single residual fill.
    assert len(result.fills) == 1
    residual = result.fills[0]

    # The residual closed at the ENTRY price via the advanced (breakeven) stop —
    # NOT at the original stop-loss.
    assert residual.index == -1
    assert residual.kind == "breakeven-stop"
    assert residual.fraction == 1.0
    assert math.isclose(residual.price, entry, rel_tol=1e-9, abs_tol=1e-9)
    assert not math.isclose(residual.price, initial_stop, rel_tol=1e-9, abs_tol=1e-9)

    # R ≈ 0 at entry, decisively not the −1R of an original-stop fill.
    assert math.isclose(residual.leg_r, 0.0, abs_tol=1e-9)
    assert residual.leg_r > -0.5

    # Realized_R is the fraction-weighted sum -> ≈ 0.0 for a breakeven scratch.
    assert result.realized_r is not None
    assert math.isclose(result.realized_r, 0.0, abs_tol=1e-9)
