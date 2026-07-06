"""Property-based test for target fill before stop (trade_manager.py, task 3.3).

Feature: trade-management

This module implements design **Property 2: Target fill before stop**:

    For any ``Management_Plan`` and a candle whose range reaches an unfilled
    scale-out leg's target *without* reaching the active stop, the simulator
    fills exactly that leg at its target price for its size fraction and records
    the fill in the Exit_Breakdown with ``kind == "target"``.

Validates: Requirements 2.2.

Strategy: construct a single candle that reaches a chosen leg's target but not
the active (initial) stop —

    * BUY:  ``high >= target`` and ``low  > stop``
    * SELL: ``low  <= target`` and ``high < stop``

— and isolate the *first* leg (the target closest to entry) so that only that
leg's target lies within the candle's range. Every other leg's target sits
strictly further from entry, so the same candle cannot reach it. We then assert
the Exit_Breakdown contains exactly one fill, for that leg, at its target price,
for its size fraction, with ``kind == "target"``.

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_plan_roundtrip_properties.py`` and
``tests/test_tm_config_default_fallback_properties.py``.
"""

import itertools
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


# ── Building-block strategies ─────────────────────────────────────────────────
# Moderate, finite magnitudes keep the constructed geometry exact under float
# arithmetic (the assertions compare the recorded fill price/fraction for exact
# equality, which holds because the simulator records the leg's own target and
# fraction verbatim).
_entry = st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False)
# Initial stop distance (entry -> initial stop), strictly positive so the plan
# is never the "invalid" zero-distance case.
_stop_distance = st.floats(min_value=0.5, max_value=1e3, allow_nan=False, allow_infinity=False)
# Per-leg gap; cumulative gaps make the targets STRICTLY ordered away from entry
# so only the first (closest) leg can be reached by the isolating candle.
_gap = st.floats(min_value=0.5, max_value=500.0, allow_nan=False, allow_infinity=False)
# Leg fraction in (0.0, 1.0].
_fraction = st.floats(min_value=1e-6, max_value=1.0, allow_nan=False, allow_infinity=False)

# Optional breakeven / trailing — included to honour "for any plan". On a single
# candle these only ever advance the stop for SUBSEQUENT candles, so they cannot
# change this candle's target fill, but generating them exercises that guarantee.
_breakeven = st.one_of(
    st.none(),
    st.builds(BreakevenTrigger, price=st.none(),
              r_multiple=st.floats(min_value=0.1, max_value=5.0,
                                   allow_nan=False, allow_infinity=False)),
)
_trailing = st.one_of(
    st.none(),
    st.builds(TrailingStop, atr_multiple=st.none(),
              r_increment=st.floats(min_value=0.1, max_value=5.0,
                                    allow_nan=False, allow_infinity=False)),
)


@st.composite
def _target_fill_scenarios(draw):
    """Build a (plan, candle, chosen_leg_index) reaching the first leg's target.

    The candle reaches the first leg's target but not the active stop, and is
    constructed so NO other leg's target falls within its range.
    """
    action = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(_entry)
    d = draw(_stop_distance)
    n = draw(st.integers(min_value=1, max_value=4))
    gaps = draw(st.lists(_gap, min_size=n, max_size=n))
    fractions = draw(st.lists(_fraction, min_size=n, max_size=n))

    # Cumulative gaps -> strictly increasing distance from entry, so leg 0 is the
    # closest target and every later leg sits strictly further away.
    offsets = list(itertools.accumulate(gaps))

    if action == "BUY":
        initial_stop = entry - d                      # stop below entry
        targets = [entry + off for off in offsets]    # targets above entry, increasing
        legs = tuple(ScaleOutLeg(target=t, fraction=f) for t, f in zip(targets, fractions))
        chosen_target = targets[0]
        # Candle reaches leg 0's target (high == target0) but not the stop
        # (low strictly above the stop). high < target1 keeps later legs unreached.
        candle = {
            "timestamp_ms": 1_000,
            "open": entry,
            "high": chosen_target,
            "low": entry - d / 2.0,                   # > initial_stop (= entry - d)
            "close": entry,
            "volume": 100.0,
        }
    else:  # SELL — mirror image
        initial_stop = entry + d                      # stop above entry
        targets = [entry - off for off in offsets]    # targets below entry, decreasing
        legs = tuple(ScaleOutLeg(target=t, fraction=f) for t, f in zip(targets, fractions))
        chosen_target = targets[0]
        # Candle reaches leg 0's target (low == target0) but not the stop
        # (high strictly below the stop). low > target1 keeps later legs unreached.
        candle = {
            "timestamp_ms": 1_000,
            "open": entry,
            "high": entry + d / 2.0,                  # < initial_stop (= entry + d)
            "low": chosen_target,
            "close": entry,
            "volume": 100.0,
        }

    plan = ManagementPlan(
        action=action,
        entry=entry,
        initial_stop=initial_stop,
        legs=legs,
        breakeven=draw(_breakeven),
        trailing=draw(_trailing),
        atr_14=draw(st.one_of(st.none(), st.floats(min_value=0.1, max_value=100.0,
                                                    allow_nan=False, allow_infinity=False))),
    )
    return plan, candle, 0


# ─────────────────────────────────────────────────────────────────────────────
# Property 2 (task 3.3): Target fill before stop
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 2: Target fill before stop
@settings(max_examples=50, deadline=None)
@given(scenario=_target_fill_scenarios())
def test_property_2_target_fill_before_stop(scenario):
    """Feature: trade-management, Property 2: Target fill before stop —
    for any plan and a candle whose range reaches an unfilled scale-out leg's
    target without reaching the active stop, the simulator fills exactly that leg
    at its target price for its size fraction and records the fill with
    ``kind == "target"``.

    Validates: Requirements 2.2
    """
    plan, candle, chosen = scenario
    config = resolve_trade_manager_config()

    result = simulate_plan(plan, [candle], config)

    leg = plan.legs[chosen]

    # Exactly that leg filled: the isolating candle reaches only leg 0's target,
    # so the Exit_Breakdown holds a single target fill (no other leg, no stop).
    assert len(result.fills) == 1, (
        f"expected exactly one fill, got {result.fills}"
    )

    fill = result.fills[0]
    assert fill.kind == "target"
    assert fill.index == chosen
    # Filled at the leg's own target price for the leg's own size fraction —
    # recorded verbatim, so exact equality holds.
    assert fill.price == leg.target
    assert fill.fraction == leg.fraction
