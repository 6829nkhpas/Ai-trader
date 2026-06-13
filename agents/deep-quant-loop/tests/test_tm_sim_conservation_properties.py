"""Property-based test for conservation of size (trade_manager.py, task 3.12).

Feature: trade-management

This module implements design **Property 11: Conservation of size**:

    For any RESOLVED ``Management_Plan``, the sum of all filled-leg fractions
    plus the residual fraction equals exactly ``1.0`` (Requirement 3.3). Because
    the residual is closed as its own recorded fill at the final stop / exit (or
    is ``0.0`` when the position fully scales out via its targets), the invariant
    is equivalently stated as: the sum of every recorded ``fill.fraction`` equals
    ``1.0``, and ``residual_fraction`` is consistent with the fills (it equals the
    fraction of the single final stop fill, or ``0.0`` when there is none).

Validates: Requirements 3.3.

Strategy: generate plans + candle sequences that are GUARANTEED to resolve, in
three complementary ways that together exercise every path size can leave the
book —

    * ``"scaleout"`` — the leg fractions sum to exactly ``1.0`` and a single
      candle reaches every (strictly-ordered) target, so the whole position
      closes via its targets and the residual is ``0.0``;
    * ``"stop"`` — the leg fractions sum to strictly LESS than ``1.0`` and, after
      zero or more leading target fills, a candle reaches the stop and closes the
      residual (a non-zero ``residual_fraction``); and
    * ``"oversubscribed"`` — the leg fractions sum to strictly MORE than ``1.0``
      and a single candle reaches every target, so the per-leg fills are clamped
      against the remaining size and still sum to exactly ``1.0`` (residual
      ``0.0``).

The plans carry no breakeven and no trailing, so the active stop is provably the
initial stop throughout and every resolution path is deterministic.

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_sim_stop_residual_properties.py`` and
``tests/test_tm_sim_realized_r_properties.py``.
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
# property: conservation is a structural invariant of the fills and is driven
# entirely by the plan's legs / initial stop (no breakeven, no trailing), not by
# any configured default.
_CONFIG = TradeManagerConfig(
    default_first_target_r=1.0,
    default_first_target_fraction=0.5,
    default_breakeven_trigger_r=1.0,
    default_trail_atr_multiple=1.5,
    min_blended_reward_to_risk=2.0,
)

# Conservation / price tolerance for floating-point residue from repeated
# fraction subtraction.
_TOL = 1e-9

# The stop / breakeven / trail fill ``kind`` values, all recorded with index -1
# as the single residual-closing fill.
_STOP_KINDS = ("stop", "breakeven-stop", "trail-stop")


@st.composite
def _resolving_cases(draw):
    """Build a (plan, candles) case that is guaranteed to RESOLVE.

    Three resolution modes are drawn with equal weight (see module docstring):
    ``scaleout`` (fractions sum to exactly 1.0), ``stop`` (fractions sum to < 1.0,
    residual closed at the stop), and ``oversubscribed`` (fractions sum to > 1.0,
    clamped against the remaining size). No breakeven, no trailing: the active
    stop is provably the initial stop, so every fill price is exact.
    """
    mode = draw(st.sampled_from(["scaleout", "stop", "oversubscribed"]))
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
    elif mode == "stop":
        # Fractions sum to strictly < 1.0 -> a residual remains for the stop.
        total = draw(st.floats(min_value=0.2, max_value=0.9, allow_nan=False, allow_infinity=False))
        scale = total / sum(raw)
        fractions = [r * scale for r in raw]
    else:  # oversubscribed
        # Fractions sum to strictly > 1.0 -> per-leg fills clamped vs remaining.
        total = draw(st.floats(min_value=1.1, max_value=3.0, allow_nan=False, allow_infinity=False))
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
    if mode in ("scaleout", "oversubscribed"):
        # One candle reaching the furthest target reaches every nearer target too
        # (strictly ordered), without touching the stop. Fully closes via targets.
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
    else:  # stop
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

    return plan, candles, mode


# ─────────────────────────────────────────────────────────────────────────────
# Property 11 (task 3.12): Conservation of size
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 11: Conservation of size
@settings(max_examples=200, deadline=None)
@given(case=_resolving_cases())
def test_property_11_conservation_of_size(case):
    """Feature: trade-management, Property 11: Conservation of size — for any
    resolved plan, the sum of all filled-leg fractions plus the residual fraction
    equals exactly ``1.0``, and ``residual_fraction`` is consistent with the
    recorded fills (the single final stop fill's fraction, or ``0.0`` when the
    position fully scales out via its targets).

    Validates: Requirements 3.3
    """
    plan, candles, mode = case

    result = simulate_plan(plan, candles, _CONFIG)

    # Every generated case is constructed to resolve.
    assert result.status == "resolved", f"mode={mode} did not resolve: {result.status}"

    # Conservation: the sum of EVERY recorded fill's fraction equals exactly 1.0
    # (filled scale-out legs plus the residual closed at the final stop / exit).
    total_fraction = sum(f.fraction for f in result.fills)
    assert math.isclose(total_fraction, 1.0, abs_tol=_TOL), (
        f"mode={mode}: filled fractions sum to {total_fraction}, expected 1.0"
    )

    # Every recorded fraction is a valid positive portion of the position.
    for f in result.fills:
        assert f.fraction > 0.0
        assert f.fraction <= 1.0 + _TOL

    # residual_fraction is consistent with the fills: it equals the fraction of
    # the single final stop / exit fill (index -1), or 0.0 when the position
    # fully scaled out via its targets with no residual.
    residual_fills = [f for f in result.fills if f.index == -1]
    assert len(residual_fills) <= 1
    if residual_fills:
        final = residual_fills[-1]
        assert final.kind in _STOP_KINDS
        # The residual fill is always the LAST recorded fill (it ends the sim).
        assert result.fills[-1] is final
        assert math.isclose(result.residual_fraction, final.fraction, abs_tol=_TOL), (
            f"mode={mode}: residual_fraction={result.residual_fraction} != "
            f"final stop fill fraction {final.fraction}"
        )
        # The filled scale-out legs plus this residual still sum to 1.0.
        filled_leg_fraction = sum(f.fraction for f in result.fills if f.index != -1)
        assert math.isclose(filled_leg_fraction + result.residual_fraction, 1.0, abs_tol=_TOL)
    else:
        # Fully scaled out via targets: no residual closed at a stop.
        assert math.isclose(result.residual_fraction, 0.0, abs_tol=_TOL), (
            f"mode={mode}: expected residual 0.0 on full scale-out, "
            f"got {result.residual_fraction}"
        )
