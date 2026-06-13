"""Property-based test for worst-case resolution on a straddling candle (trade_manager.py, task 3.7).

Feature: trade-management

This module implements design **Property 6: Worst-case resolution on a
straddling candle** (Key Design Decision AD-4 — "Worst-case resolution never
flatters the plan"):

    For any ``Management_Plan`` and a single candle whose range reaches BOTH the
    active stop AND an unfilled target, ``simulate_plan`` resolves the STOP first,
    so the outcome is never flattered by the simultaneous target.

    * BUY:  ``low  <= stop`` AND ``high >= a target``
    * SELL: ``high >= stop`` AND ``low  <= a target``

Validates: Requirements 2.6.

Construction: each generated case builds a SINGLE candle that straddles both
the initial stop and one (or more) of the plan's scale-out targets. Because the
straddle candle is the first candle the simulator evaluates, the active stop is
provably the *initial* stop (no breakeven / trail has had a chance to advance
it). The simulator must therefore resolve the residual at the stop, not at the
target:

      * ``status == "resolved"``;
      * the FIRST (and, with no prior fill, the only) fill is a stop fill closing
        the WHOLE remaining size at the stop price (``index == -1``, ``kind ==
        "stop"``, ``price == initial stop``, ``fraction == 1.0``);
      * no target fill is recorded (the stop resolved before any target);
      * ``realized_r`` reflects the stop — for a no-prior-fill straddle the whole
        position closes at the initial stop, so ``realized_r == -1.0`` exactly
        (the entire size at exactly ``-1R``), and is never the more favorable,
        positive target outcome.

Optional breakeven / trailing rules are generated into the plan to honour "for
any plan": on the straddle candle they cannot change the outcome because the
stop is resolved before they are ever evaluated, but generating them exercises
that guarantee.

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_sim_stop_residual_properties.py`` and
``tests/test_tm_sim_target_fill_properties.py``.
"""

import itertools
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
    TrailingStop,
    resolve_trade_manager_config,
    simulate_plan,
)

# Conservation / price tolerance for floating-point residue.
_TOL = 1e-9

# ── Building-block strategies ─────────────────────────────────────────────────
_entry = st.floats(min_value=50.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
# Initial stop distance (entry -> initial stop), strictly positive so the plan
# is never the "invalid" zero-distance case.
_stop_distance = st.floats(min_value=1.0, max_value=40.0, allow_nan=False, allow_infinity=False)
# Per-leg gap; cumulative gaps make the targets STRICTLY ordered away from entry.
_gap = st.floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False)

# Optional breakeven / trailing — included to honour "for any plan". On the
# straddle candle the stop is resolved BEFORE either is evaluated, so they cannot
# flatter the outcome; generating them exercises that guarantee.
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
def _straddle_cases(draw):
    """Build a (plan, candles, stop_price) case with one straddling candle.

    The plan has one or more strictly-ordered scale-out targets on the profit
    side and an initial stop on the loss side. A single candle straddles both:
    its low/high reaches the stop AND its opposite extreme reaches the targets.
    No prior candle exists, so the active stop is the initial stop.
    """
    side = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(_entry)
    d = draw(_stop_distance)
    n = draw(st.integers(min_value=1, max_value=4))
    gaps = draw(st.lists(_gap, min_size=n, max_size=n))

    # Leg fractions in (0.0, 1.0] summing to at most 1.0 (a valid plan shape).
    raw = [draw(st.floats(min_value=0.1, max_value=1.0,
                          allow_nan=False, allow_infinity=False)) for _ in range(n)]
    total = draw(st.floats(min_value=0.2, max_value=1.0,
                           allow_nan=False, allow_infinity=False))
    scale = total / sum(raw)
    fractions = [r * scale for r in raw]

    # Cumulative gaps -> strictly increasing distance from entry.
    offsets = list(itertools.accumulate(gaps))

    if side == "BUY":
        stop = entry - d                              # stop below entry
        targets = [entry + off for off in offsets]    # targets above entry, increasing
        # Straddle: low reaches the stop, high reaches the furthest target.
        candle = {
            "timestamp_ms": 1_000,
            "open": entry,
            "high": targets[-1],                      # high >= every target
            "low": stop,                              # low <= stop
            "close": entry,
            "volume": 100.0,
        }
    else:  # SELL — mirror image
        stop = entry + d                              # stop above entry
        targets = [entry - off for off in offsets]    # targets below entry, decreasing
        candle = {
            "timestamp_ms": 1_000,
            "open": entry,
            "high": stop,                             # high >= stop
            "low": targets[-1],                       # low <= every target
            "close": entry,
            "volume": 100.0,
        }

    legs = tuple(ScaleOutLeg(target=t, fraction=f) for t, f in zip(targets, fractions))
    plan = ManagementPlan(
        action=side,
        entry=entry,
        initial_stop=stop,
        legs=legs,
        breakeven=draw(_breakeven),
        trailing=draw(_trailing),
        atr_14=draw(st.one_of(st.none(), st.floats(min_value=0.1, max_value=100.0,
                                                   allow_nan=False, allow_infinity=False))),
    )
    return plan, [candle], stop


# ─────────────────────────────────────────────────────────────────────────────
# Property 6 (task 3.7): Worst-case resolution on a straddling candle
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 6: Worst-case resolution on a straddling candle
@settings(max_examples=200, deadline=None)
@given(case=_straddle_cases())
def test_property_6_worst_case_resolution_on_straddling_candle(case):
    """Feature: trade-management, Property 6: Worst-case resolution on a
    straddling candle — for any plan and a single candle whose range reaches
    BOTH the active stop and an unfilled target, the simulator resolves the stop
    first, so the outcome is never flattered by the simultaneous target.

    Validates: Requirements 2.6
    """
    plan, candles, stop_price = case

    config = resolve_trade_manager_config()
    result = simulate_plan(plan, candles, config)

    # The straddle resolves the position.
    assert result.status == "resolved"
    assert result.realized_r is not None

    # The stop resolved FIRST: no target fill was recorded, only the single stop
    # fill that closed the whole remaining (residual) size at the stop price.
    assert len(result.fills) == 1, (
        f"expected exactly one (stop) fill, got {result.fills}"
    )
    fill = result.fills[0]
    assert fill.index == -1
    assert fill.kind == "stop"
    assert fill.kind != "target"
    assert math.isclose(fill.price, stop_price, abs_tol=_TOL)

    # The whole position (no prior fill) closed at the stop: residual == 1.0.
    assert math.isclose(fill.fraction, 1.0, abs_tol=_TOL)
    assert math.isclose(result.residual_fraction, 1.0, abs_tol=_TOL)

    # Realized_R reflects the STOP, not the favorable target: the entire size
    # closing at the initial stop is exactly -1R, and is never positive.
    assert math.isclose(result.realized_r, -1.0, abs_tol=1e-9)
    assert result.realized_r < 0.0
