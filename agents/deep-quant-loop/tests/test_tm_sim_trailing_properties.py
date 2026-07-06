"""Property-based test for the monotone, never-adverse trailing stop
(trade_manager.py ``simulate_plan``, task 3.6).

Feature: trade-management

This module implements design **Property 5: Trailing stop is monotone and never
adverse**:

    For any Management_Plan with a Trailing_Stop and any candle sequence, the
    sequence of active-stop values only ever moves toward locking in progress
    (non-decreasing for a BUY, non-increasing for a SELL) and never moves
    adversely.

Validates: Requirements 2.5.

The active stop is internal to ``simulate_plan`` (it is not returned directly),
so the trajectory is OBSERVED via the recorded fills. The robust technique used
here reconstructs the active-stop trajectory: after each favorable candle we run
``simulate_plan`` on that prefix followed by a synthetic "probe" candle whose
range is guaranteed to reach the stop. Because the simulator resolves the stop
FIRST on every candle, the residual fill the probe produces is closed at exactly
the active stop established by the prefix — recovering the active-stop value at
that step. The recovered sequence ``S_0, S_1, ...`` must therefore be:

  * monotone toward locking progress — non-decreasing for a BUY, non-increasing
    for a SELL; and
  * never adverse — never worse than the INITIAL stop (BUY: ``S_i >= initial_stop``;
    SELL: ``S_i <= initial_stop``).

We additionally assert that when the trailed stop finally resolves the residual,
its R is never worse than the initial-stop loss (``leg_r >= -1.0`` and the overall
``realized_r >= -1.0``), the concrete "never adverse" guarantee.

Both ATR-multiple (with a finite ``atr_14``) and fixed-R-increment trailing rules
are generated, with and without a preceding breakeven trigger (the trail engages
after the breakeven when one exists, else from the start).

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_sim_breakeven_properties.py`` and
``tests/test_tm_sim_stop_residual_properties.py``.
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
    TradeManagerConfig,
    TrailingStop,
    simulate_plan,
)

# A resolved configuration is required by the signature but irrelevant here: the
# active stop is driven entirely by the plan's own trailing rule, not by any
# configured default.
_CONFIG = TradeManagerConfig(
    default_first_target_r=1.0,
    default_first_target_fraction=0.5,
    default_breakeven_trigger_r=1.0,
    default_trail_atr_multiple=1.5,
    min_blended_reward_to_risk=2.0,
)

_TOL = 1e-9


def _finite(min_value, max_value):
    return st.floats(
        min_value=min_value,
        max_value=max_value,
        allow_nan=False,
        allow_infinity=False,
    )


@st.composite
def _trailing_scenarios(draw):
    """Build a trailing plan and a favorable, monotone candle path.

    Returns ``(plan, favorable_candles, side, entry, initial_stop, far_target)``.

    The plan carries a single far scale-out target (fraction ``1.0``, never
    reached so the whole position survives to the trailed stop), a Trailing_Stop
    rule (ATR-multiple or fixed-R-increment), and an optional breakeven trigger.
    The favorable candles are flat bars whose closes march steadily in the
    profit direction (rising for a BUY, falling for a SELL) without ever touching
    the stop or the far target — so the only thing they do is ratchet the trail.
    """
    side = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(_finite(100.0, 1000.0))
    stop_distance = draw(_finite(1.0, 50.0))
    step = draw(_finite(1.0, 30.0))
    m = draw(st.integers(min_value=1, max_value=6))

    # Trailing rule: ATR-multiple (needs a finite atr_14) or fixed-R-increment.
    rule = draw(st.sampled_from(["atr", "r_increment"]))
    if rule == "atr":
        atr_14 = draw(_finite(0.5, 20.0))
        atr_multiple = draw(_finite(0.5, 4.0))
        trailing = TrailingStop(atr_multiple=atr_multiple)
    else:
        atr_14 = None
        r_increment = draw(_finite(0.25, 3.0))
        trailing = TrailingStop(r_increment=r_increment)

    # Optional preceding breakeven (the trail engages only after it triggers).
    use_breakeven = draw(st.booleans())
    breakeven = None
    if use_breakeven:
        # Place the breakeven within reach of the favorable march so it triggers.
        be_r = draw(_finite(0.25, 1.0))
        breakeven = BreakevenTrigger(r_multiple=be_r)

    # A far target on the profit side that the favorable march never reaches.
    far = stop_distance + step * (m + 5) + 1000.0
    if side == "BUY":
        initial_stop = entry - stop_distance
        far_target = entry + far
        closes = [entry + i * step for i in range(m + 1)]
    else:
        initial_stop = entry + stop_distance
        far_target = entry - far
        closes = [entry - i * step for i in range(m + 1)]

    plan = ManagementPlan(
        action=side,
        entry=entry,
        initial_stop=initial_stop,
        legs=(ScaleOutLeg(target=far_target, fraction=1.0),),
        breakeven=breakeven,
        trailing=trailing,
        atr_14=atr_14,
    )

    # Flat bars at each close: high == low == open == close, so they never reach
    # the far target and never touch the stop (the close marches with the trade).
    favorable_candles = []
    for i, c in enumerate(closes):
        favorable_candles.append(
            {
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "volume": 1000.0,
                "timestamp_ms": 1000 * (i + 1),
            }
        )

    return plan, favorable_candles, side, entry, initial_stop, far_target


def _probe_candle(side, entry, initial_stop, far_target, ts):
    """A candle whose range is guaranteed to reach any active stop but no target.

    For a BUY the active stop is at or above the initial stop and at or below the
    current price, so a low far below the initial stop is certain to reach it; the
    high stays well short of the far target. Mirror image for a SELL.
    """
    stop_distance = abs(entry - initial_stop)
    if side == "BUY":
        lo = initial_stop - stop_distance - 1.0   # certainly <= any active stop
        hi = entry                                # < far_target (which is entry + far)
    else:
        hi = initial_stop + stop_distance + 1.0   # certainly >= any active stop
        lo = entry                                # > far_target (entry - far)
    return {
        "open": entry,
        "high": hi,
        "low": lo,
        "close": entry,
        "volume": 1000.0,
        "timestamp_ms": ts,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 5 (task 3.6): Trailing stop is monotone and never adverse
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 5: Trailing stop is monotone and never adverse
@settings(max_examples=50, deadline=None)
@given(scenario=_trailing_scenarios())
def test_property_5_trailing_stop_monotone_and_never_adverse(scenario):
    """Feature: trade-management, Property 5: Trailing stop is monotone and never
    adverse — the reconstructed active-stop trajectory moves only toward locking
    progress (non-decreasing for BUY, non-increasing for SELL) and never sits
    worse than the initial stop, and the trailed residual fill is never worse
    than the initial-stop loss (R >= -1).

    Validates: Requirements 2.5
    """
    plan, favorable_candles, side, entry, initial_stop, far_target = scenario

    # Reconstruct the active-stop trajectory: after each favorable prefix, a probe
    # candle resolves the residual at exactly the active stop established so far.
    trajectory = []
    for i in range(len(favorable_candles)):
        prefix = favorable_candles[: i + 1]
        probe_ts = prefix[-1]["timestamp_ms"] + 1
        probe = _probe_candle(side, entry, initial_stop, far_target, probe_ts)
        result = simulate_plan(plan, prefix + [probe], _CONFIG)

        # The probe always resolves the whole (un-scaled) position at the stop.
        assert result.status == "resolved"
        assert len(result.fills) == 1
        final = result.fills[0]
        assert final.index == -1
        assert final.fraction == 1.0
        trajectory.append(final.price)

    # Monotone toward locking progress, and never adverse vs the initial stop.
    if side == "BUY":
        for prev, cur in zip(trajectory, trajectory[1:]):
            assert cur >= prev - _TOL, f"BUY active stop moved down: {prev} -> {cur}"
        for s in trajectory:
            assert s >= initial_stop - _TOL, f"BUY active stop below initial: {s} < {initial_stop}"
    else:
        for prev, cur in zip(trajectory, trajectory[1:]):
            assert cur <= prev + _TOL, f"SELL active stop moved up: {prev} -> {cur}"
        for s in trajectory:
            assert s <= initial_stop + _TOL, f"SELL active stop above initial: {s} > {initial_stop}"

    # The trailed residual fill is never worse than the initial-stop loss: closing
    # the residual at the final active stop yields R >= -1 (and realized_r >= -1).
    final_result = simulate_plan(
        plan,
        favorable_candles
        + [_probe_candle(side, entry, initial_stop, far_target,
                         favorable_candles[-1]["timestamp_ms"] + 1)],
        _CONFIG,
    )
    assert final_result.status == "resolved"
    assert final_result.realized_r is not None
    assert final_result.realized_r >= -1.0 - _TOL
    residual = final_result.fills[-1]
    assert residual.leg_r >= -1.0 - _TOL
    if side == "BUY":
        assert residual.price >= initial_stop - _TOL
    else:
        assert residual.price <= initial_stop + _TOL
