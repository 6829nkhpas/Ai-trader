"""Property-based test for the fraction-weighted Realized_R (trade_manager.py, task 3.8).

Feature: trade-management

This module implements design **Property 7: Realized_R is the fraction-weighted
sum against the initial stop**:

    For any RESOLVED ``Management_Plan``, ``SimulationResult.realized_r`` equals
    the sum over ALL fills (scale-out legs plus the residual) of
    ``fraction * leg_R``, where each ``leg_R`` is the SIGNED distance of the fill
    price from entry divided by the INITIAL stop distance:

        * BUY:  ``leg_R = (price - entry) / initial_stop_distance``
        * SELL: ``leg_R = (entry - price) / initial_stop_distance``

    where ``initial_stop_distance == abs(entry - initial_stop)``.

Validates: Requirements 2.7.

Strategy: generate plans + candle sequences that are GUARANTEED to resolve, in
two complementary ways —

    * "scale-out" resolution: the leg fractions sum to exactly ``1.0`` and a
      single candle reaches every (strictly-ordered) target, so the whole
      position closes via its targets with no residual; and
    * "stop" resolution: the leg fractions sum to strictly less than ``1.0`` and,
      after zero or more leading target fills, a candle reaches the stop and
      closes the residual.

The plans carry no breakeven and no trailing, so the active stop equals the
initial stop throughout and the geometry is exact. For each resolved result we
re-derive every fill's ``leg_R`` INDEPENDENTLY from ``fill.price`` and assert (a)
each recorded ``fill.leg_r`` matches the independent recomputation and (b)
``realized_r`` equals the fraction-weighted sum of those independent ``leg_R``.

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_sim_stop_residual_properties.py`` and
``tests/test_tm_sim_target_fill_properties.py``.
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
    ManagementPlan,
    ScaleOutLeg,
    TradeManagerConfig,
    simulate_plan,
)

# A resolved configuration is required by the signature but irrelevant to this
# property: the active stop here is driven entirely by the plan's initial stop
# (no breakeven, no trailing), and Realized_R is measured against the INITIAL
# stop distance regardless of any configured default.
_CONFIG = TradeManagerConfig(
    default_first_target_r=1.0,
    default_first_target_fraction=0.5,
    default_breakeven_trigger_r=1.0,
    default_trail_atr_multiple=1.5,
    min_blended_reward_to_risk=2.0,
)

# Tolerance for floating-point residue in the fraction-weighted comparison.
_TOL = 1e-9


@st.composite
def _resolved_cases(draw):
    """Build a (plan, candles) case that is guaranteed to RESOLVE.

    Two resolution modes are drawn with equal weight:

      * ``"scaleout"`` — leg fractions sum to exactly 1.0; a single candle
        reaches the furthest (hence every) target, fully closing the position via
        its targets (no residual, no stop fill).
      * ``"stop"`` — leg fractions sum to strictly less than 1.0; after a candle
        that fills the first ``k`` legs at their targets, a stop-hitting candle
        closes the residual.

    No breakeven, no trailing: the active stop is provably the initial stop, so
    every fill price (target prices and the stop) is exact.
    """
    mode = draw(st.sampled_from(["scaleout", "stop"]))
    side = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(st.floats(min_value=50.0, max_value=1000.0, allow_nan=False, allow_infinity=False))
    stop_distance = draw(st.floats(min_value=1.0, max_value=40.0, allow_nan=False, allow_infinity=False))
    step = draw(st.floats(min_value=1.0, max_value=20.0, allow_nan=False, allow_infinity=False))
    n = draw(st.integers(min_value=1, max_value=4))

    # Strictly ordered targets on the profit side; stop on the loss side.
    if side == "BUY":
        stop = entry - stop_distance
        targets = [entry + (j + 1) * step for j in range(n)]
    else:
        stop = entry + stop_distance
        targets = [entry - (j + 1) * step for j in range(n)]

    raw = [draw(st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False)) for _ in range(n)]

    if mode == "scaleout":
        # Fractions sum to exactly 1.0 -> full close via targets, no residual.
        scale = 1.0 / sum(raw)
        fractions = [r * scale for r in raw]
    else:
        # Fractions sum to strictly < 1.0 -> a residual remains for the stop.
        total = draw(st.floats(min_value=0.2, max_value=0.9, allow_nan=False, allow_infinity=False))
        scale = total / sum(raw)
        fractions = [r * scale for r in raw]

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

    candles = []
    if mode == "scaleout":
        # One candle reaching the furthest target reaches every nearer target too
        # (strictly ordered), without touching the stop. Fully scales out.
        if side == "BUY":
            candles.append(
                {"open": entry, "high": targets[-1], "low": entry, "close": entry,
                 "volume": 1.0, "timestamp_ms": 1000}
            )
        else:
            candles.append(
                {"open": entry, "high": entry, "low": targets[-1], "close": entry,
                 "volume": 1.0, "timestamp_ms": 1000}
            )
    else:
        # k leading target fills before the stop closes the residual.
        k = draw(st.integers(min_value=0, max_value=n))
        if k > 0:
            if side == "BUY":
                candles.append(
                    {"open": entry, "high": targets[k - 1], "low": entry, "close": entry,
                     "volume": 1.0, "timestamp_ms": 1000}
                )
            else:
                candles.append(
                    {"open": entry, "high": entry, "low": targets[k - 1], "close": entry,
                     "volume": 1.0, "timestamp_ms": 1000}
                )
        # The stop-hitting candle (BUY: low <= stop; SELL: high >= stop).
        if side == "BUY":
            candles.append(
                {"open": entry, "high": entry, "low": stop, "close": stop,
                 "volume": 1.0, "timestamp_ms": 2000}
            )
        else:
            candles.append(
                {"open": entry, "high": stop, "low": entry, "close": stop,
                 "volume": 1.0, "timestamp_ms": 2000}
            )

    return plan, candles


def _independent_leg_r(price, entry, initial_stop, side):
    """Re-derive a fill's R INDEPENDENTLY of the simulator (Requirement 2.7).

    Signed distance of the fill price from entry over the INITIAL stop distance:
    BUY -> (price - entry) / d; SELL -> (entry - price) / d.
    """
    d = abs(entry - initial_stop)
    if side == "BUY":
        return (price - entry) / d
    return (entry - price) / d


# ─────────────────────────────────────────────────────────────────────────────
# Property 7 (task 3.8): Realized_R is the fraction-weighted sum vs the initial stop
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 7: Realized_R is the fraction-weighted sum against the initial stop
@settings(max_examples=50, deadline=None)
@given(case=_resolved_cases())
def test_property_7_realized_r_fraction_weighted_against_initial_stop(case):
    """Feature: trade-management, Property 7: Realized_R is the fraction-weighted
    sum against the initial stop — for any resolved plan, ``realized_r`` equals
    ``sum(fill.fraction * leg_R)`` where each ``leg_R`` is the signed
    ``(price - entry)`` (BUY) / ``(entry - price)`` (SELL) over the INITIAL stop
    distance, independently recomputed from each fill price.

    Validates: Requirements 2.7
    """
    plan, candles = case

    result = simulate_plan(plan, candles, _CONFIG)

    # The case is constructed to always resolve.
    assert result.status == "resolved"
    assert result.realized_r is not None
    assert len(result.fills) >= 1

    entry = plan.entry
    initial_stop = plan.initial_stop
    side = plan.action

    # (a) Each recorded fill.leg_r matches an INDEPENDENT recomputation from its
    #     own fill price against the initial stop distance.
    for fill in result.fills:
        expected_leg_r = _independent_leg_r(fill.price, entry, initial_stop, side)
        assert math.isclose(fill.leg_r, expected_leg_r, rel_tol=1e-9, abs_tol=_TOL), (
            f"fill.leg_r={fill.leg_r} != independent {expected_leg_r} "
            f"(price={fill.price}, entry={entry}, stop={initial_stop})"
        )

    # (b) realized_r equals the fraction-weighted sum of the INDEPENDENT leg_R
    #     over all fills (scale-out legs plus the residual closed at the stop).
    expected_realized_r = sum(
        fill.fraction * _independent_leg_r(fill.price, entry, initial_stop, side)
        for fill in result.fills
    )
    assert math.isclose(result.realized_r, expected_realized_r, rel_tol=1e-9, abs_tol=_TOL), (
        f"realized_r={result.realized_r} != fraction-weighted independent sum "
        f"{expected_realized_r}"
    )
