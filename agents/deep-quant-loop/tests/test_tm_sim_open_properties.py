"""Property-based test for unresolved plans reporting open (trade_manager.py, task 3.10).

Feature: trade-management

This module implements design **Property 9: Unresolved plans report open
without fabricating an exit**:

    For any ``Management_Plan`` and candle sequence in which NO candle reaches
    any target or the stop, ``simulate_plan`` reports ``status == "open"`` with
    no fabricated fills and ``realized_r is None``, and does not raise.

Validates: Requirements 3.1.

Strategy: construct candle sequences that stay strictly between the stop and the
nearest target so nothing fills —

    * BUY:  every candle ``high < min(targets)`` and ``low  > stop``
    * SELL: every candle ``low  > max(targets)`` and ``high < stop``

To keep the guarantee exact we OMIT the breakeven trigger and the trailing rule
(per the task note), so the active stop is provably the initial stop for every
candle and the candles can never reach a moved/trailed stop either. Every OHLC
value is placed strictly inside the open band ``(stop, nearest_target)`` (BUY) /
``(nearest_target, stop)`` (SELL), so no candle touches a target or the stop.
We then assert ``status == "open"``, ``realized_r is None``, and ``fills`` is
empty (no target reached, no exit fabricated).

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_sim_target_fill_properties.py`` and
``tests/test_tm_sim_stop_residual_properties.py``.
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
    ManagementPlan,
    ScaleOutLeg,
    TradeManagerConfig,
    simulate_plan,
)

# A resolved configuration is required by the signature but irrelevant here: the
# plans carry no breakeven and no trailing, so no configured default influences
# the active stop — it stays the initial stop throughout.
_CONFIG = TradeManagerConfig(
    default_first_target_r=1.0,
    default_first_target_fraction=0.5,
    default_breakeven_trigger_r=1.0,
    default_trail_atr_multiple=1.5,
    min_blended_reward_to_risk=2.0,
)


def _band_value(low_bound, high_bound, frac):
    """Map ``frac`` in [0.1, 0.9] into the OPEN band ``(low_bound, high_bound)``.

    With ``frac`` restricted to [0.1, 0.9] the result is strictly inside the band
    (never on either boundary), so a candle built from such values never reaches
    the stop boundary or the nearest-target boundary.
    """
    return low_bound + frac * (high_bound - low_bound)


@st.composite
def _unresolved_cases(draw):
    """Build a (plan, candles) case where NO candle reaches any target or stop.

    The plan has one or more strictly-ordered scale-out legs, NO breakeven, and
    NO trailing, so the active stop is the initial stop for every candle. Every
    candle's full OHLC range lies strictly inside the open band between the stop
    and the nearest target, so nothing fills and nothing stops out.
    """
    side = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(st.floats(min_value=50.0, max_value=1000.0,
                           allow_nan=False, allow_infinity=False))
    stop_distance = draw(st.floats(min_value=1.0, max_value=50.0,
                                   allow_nan=False, allow_infinity=False))
    step = draw(st.floats(min_value=1.0, max_value=50.0,
                          allow_nan=False, allow_infinity=False))
    n = draw(st.integers(min_value=1, max_value=4))

    # Strictly ordered targets on the profit side; the stop on the loss side.
    if side == "BUY":
        stop = entry - stop_distance
        targets = [entry + (j + 1) * step for j in range(n)]
        nearest_target = min(targets)          # = targets[0]
        low_bound, high_bound = stop, nearest_target
    else:
        stop = entry + stop_distance
        targets = [entry - (j + 1) * step for j in range(n)]
        nearest_target = max(targets)          # = targets[0]
        low_bound, high_bound = nearest_target, stop

    # Leg fractions in (0.0, 1.0]; their exact values are irrelevant because no
    # leg fills, but "for any plan" means generating realistic legs.
    fractions = [
        draw(st.floats(min_value=0.05, max_value=1.0,
                       allow_nan=False, allow_infinity=False))
        for _ in range(n)
    ]
    legs = tuple(ScaleOutLeg(target=t, fraction=f) for t, f in zip(targets, fractions))
    plan = ManagementPlan(
        action=side,
        entry=entry,
        initial_stop=stop,
        legs=legs,
        breakeven=None,
        trailing=None,
        atr_14=None,
    )

    # Build candles whose every OHLC value is strictly inside (low_bound, high_bound).
    num_candles = draw(st.integers(min_value=1, max_value=8))
    _f = st.floats(min_value=0.1, max_value=0.9, allow_nan=False, allow_infinity=False)
    candles = []
    for i in range(num_candles):
        vals = [_band_value(low_bound, high_bound, draw(_f)) for _ in range(4)]
        candles.append(
            {
                "timestamp_ms": 1000 + i * 1000,
                "open": vals[0],
                "close": vals[1],
                "high": max(vals),
                "low": min(vals),
                "volume": 100.0,
            }
        )

    return plan, candles


# ─────────────────────────────────────────────────────────────────────────────
# Property 9 (task 3.10): Unresolved plans report open without fabricating an exit
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 9: Unresolved plans report open without fabricating an exit
@settings(max_examples=200, deadline=None)
@given(case=_unresolved_cases())
def test_property_9_unresolved_plans_report_open(case):
    """Feature: trade-management, Property 9: Unresolved plans report open
    without fabricating an exit — for any plan and a candle sequence in which no
    candle reaches any target or the stop, the simulator reports
    ``status == "open"`` with no fabricated fills and ``realized_r is None``, and
    does not raise.

    Validates: Requirements 3.1
    """
    plan, candles = case

    # Does not raise.
    result = simulate_plan(plan, candles, _CONFIG)

    # Unresolved: reported open, no realized R, and no fabricated fills.
    assert result.status == "open"
    assert result.realized_r is None
    assert result.fills == ()           # no target reached, no exit fabricated
    # The whole position is still open (nothing closed).
    assert result.residual_fraction == 1.0
