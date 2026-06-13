"""Property-based test for the stop closing the entire residual (trade_manager.py, task 3.4).

Feature: trade-management

This module implements design **Property 3: Stop closes the entire residual**:

    For any ``Management_Plan`` and a candle whose range reaches the active stop
    (BUY: ``low <= stop``; SELL: ``high >= stop``), ``simulate_plan`` closes ALL
    remaining (unfilled) size at the active stop price, records the final exit,
    and ends the simulation:

      * ``status == "resolved"``;
      * the sum of every fill's fraction equals ``1.0`` (conservation of size);
      * the FINAL fill is at the active stop price, closing the residual
        (``index == -1``, a stop ``kind``, ``price == active stop``,
        ``fraction == residual``);
      * no candle after the stop adds any further fill (the simulation ends).

Validates: Requirements 2.3.

Each generated case constructs a candle sequence in which a stop-hitting candle
arrives *after* zero or more target-filling candles, so the residual the stop
closes is genuinely "all remaining size after some partial target fills". The
plans carry no breakeven and no trailing rule, so the *active* stop is provably
the *initial* stop throughout — making the expected final fill price exact.

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_plan_roundtrip_properties.py`` and
``tests/test_tm_config_path_independent_properties.py``.
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
# (no breakeven, no trailing), not by any configured default.
_CONFIG = TradeManagerConfig(
    default_first_target_r=1.0,
    default_first_target_fraction=0.5,
    default_breakeven_trigger_r=1.0,
    default_trail_atr_multiple=1.5,
    min_blended_reward_to_risk=2.0,
)

# Conservation / price tolerance for floating-point residue.
_TOL = 1e-9


@st.composite
def _stop_closing_cases(draw):
    """Build a (plan, candles, stop_price, num_target_fills) case.

    The plan has one or more scale-out legs whose fractions sum to strictly less
    than 1.0 (so a non-empty residual always survives to be closed at the stop),
    no breakeven, and no trailing (so the active stop equals the initial stop).
    The candle sequence is: optionally one candle that fills the first ``k`` legs
    at their targets, then a candle that reaches the stop, then optionally some
    trailing candles that must never be reached.
    """
    side = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(st.floats(min_value=50.0, max_value=1000.0, allow_nan=False, allow_infinity=False))
    stop_distance = draw(st.floats(min_value=1.0, max_value=40.0, allow_nan=False, allow_infinity=False))
    step = draw(st.floats(min_value=1.0, max_value=20.0, allow_nan=False, allow_infinity=False))
    n = draw(st.integers(min_value=1, max_value=4))

    # Strictly ordered targets on the profit side, and the stop on the loss side.
    if side == "BUY":
        stop = entry - stop_distance
        targets = [entry + (j + 1) * step for j in range(n)]
    else:
        stop = entry + stop_distance
        targets = [entry - (j + 1) * step for j in range(n)]

    # Leg fractions in (0.0, 1.0] summing to strictly < 1.0 (guarantees a
    # residual remains for the stop to close).
    raw = [draw(st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False)) for _ in range(n)]
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

    # k = how many leading legs a target candle fills before the stop arrives.
    k = draw(st.integers(min_value=0, max_value=n))

    candles = []
    if k > 0:
        # A candle reaching target[k-1] fills the first k (strictly-ordered) legs
        # without touching the stop (its low/high stays on the entry side).
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

    # Trailing candles that must never be reached (the sim ends at the stop).
    num_after = draw(st.integers(min_value=0, max_value=3))
    for i in range(num_after):
        candles.append(
            {"open": entry, "high": entry + abs(step), "low": entry - abs(step), "close": entry,
             "volume": 1.0, "timestamp_ms": 3000 + i * 1000}
        )

    return plan, candles, stop, k, fractions


# ─────────────────────────────────────────────────────────────────────────────
# Property 3 (task 3.4): Stop closes the entire residual
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 3: Stop closes the entire residual
@settings(max_examples=200, deadline=None)
@given(case=_stop_closing_cases())
def test_property_3_stop_closes_entire_residual(case):
    """Feature: trade-management, Property 3: Stop closes the entire residual —
    a candle reaching the active stop closes all remaining size at the stop
    price and ends the simulation.

    Validates: Requirements 2.3
    """
    plan, candles, stop_price, k, fractions = case

    result = simulate_plan(plan, candles, _CONFIG)

    # The plan resolves (the stop closed the position).
    assert result.status == "resolved"
    assert result.realized_r is not None

    # Conservation: every fill's fraction sums to exactly 1.0.
    total_fraction = sum(f.fraction for f in result.fills)
    assert math.isclose(total_fraction, 1.0, abs_tol=_TOL)

    # Exactly k target fills precede the single stop fill, so no candle after the
    # stop added any further fill (the simulation ended at the stop candle).
    assert len(result.fills) == k + 1

    # The leading fills are the k target fills (in order, at their target prices).
    for i in range(k):
        assert result.fills[i].kind == "target"
        assert result.fills[i].index == i

    # The FINAL fill closes the residual at the active stop price. With no
    # breakeven and no trailing, the active stop is the initial stop.
    final = result.fills[-1]
    assert final.index == -1
    assert final.kind == "stop"
    assert math.isclose(final.price, stop_price, abs_tol=_TOL)

    # The residual it closed is exactly 1.0 minus the filled target fractions.
    expected_residual = 1.0 - sum(fractions[:k])
    assert math.isclose(final.fraction, expected_residual, abs_tol=_TOL)
    assert math.isclose(result.residual_fraction, expected_residual, abs_tol=_TOL)
