"""Property-based test for validator HOLD bypass (validator.py, task 6.7).

Feature: trade-management

This module implements design **Property 18: HOLD bypasses all plan checks**:

    For Action.HOLD, validate_trade always passes (is_pass() True) regardless of
    how malformed / risk-violating the Management_Plan and the base execution
    levels are. A HOLD is an abstention, so the validator returns a passing
    outcome with risk_reward == 0.0 *before* any level OR plan check runs.

Validates: Requirements 5.5.

To prove the bypass holds universally, this test deliberately generates plans
and levels that would be REJECTED for a BUY/SELL — the opposite of the other
``test_tm_validator_*`` property tests, which hold every other aspect valid to
isolate a single check. Here every aspect is allowed to be arbitrary and
hostile:

* leg fractions outside ``(0.0, 1.0]`` and/or summing well over ``1.0`` (would
  trip ``LEG_FRACTION_OUT_OF_RANGE``);
* scale-out targets on the wrong side of entry and in non-monotone order (would
  trip ``TARGET_ORDERING_INCONSISTENT``);
* breakeven triggers placed outside ``(entry, first_target)`` / negative
  r_multiples (would trip ``BREAKEVEN_OUT_OF_RANGE``);
* a deliberately tiny blended reward-to-risk floor demand combined with targets
  sitting at entry (would trip ``BLENDED_RR_TOO_LOW``);
* direction-inconsistent / missing / non-finite base levels (would trip
  ``DIRECTION_INCONSISTENT`` / ``MISSING_LEVELS``), including ``levels=None``;
* arbitrary / non-finite / absent ATR.

For Action.HOLD every one of these must be bypassed: ``validate_trade`` must
return ``is_pass()`` True with ``risk_reward == 0.0`` and ``reason is None``.

The sys.path / import pattern mirrors ``tests/test_tm_validator_leg_fraction_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (validator.py / trade_manager.py live one
# level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from trade_manager import (  # noqa: E402
    BreakevenTrigger,
    ManagementPlan,
    ScaleOutLeg,
    TrailingStop,
)
from validator import (  # noqa: E402
    Action,
    ExecutionLevels,
    validate_trade,
)


# ── Strategies ────────────────────────────────────────────────────────────────
# Hostile, deliberately-invalid values. Fractions straddle and exceed the valid
# (0.0, 1.0] window; prices range over both sides of zero and can be non-finite.
_fraction = st.floats(min_value=-2.0, max_value=3.0, allow_nan=False, allow_infinity=False)

# Prices, mixing finite values over both sides of zero with explicit non-finite
# values (NaN / +-inf) so even non-finite levels are exercised on the HOLD path
# (which must still pass without raising). Bounds and allow_nan cannot coexist on
# a single floats() strategy, so the non-finite values are injected via one_of.
_price = st.one_of(
    st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([float("nan"), float("inf"), float("-inf")]),
)


@st.composite
def _legs(draw):
    """One-to-five legs with arbitrary (often invalid) targets and fractions, in
    arbitrary (often non-monotone, wrong-side) order."""
    n = draw(st.integers(min_value=1, max_value=5))
    return tuple(
        ScaleOutLeg(target=draw(_price), fraction=draw(_fraction)) for _ in range(n)
    )


@st.composite
def _breakeven(draw):
    """A breakeven trigger that is absent, price-form, or r_multiple-form, with
    arbitrary (often out-of-range) values."""
    kind = draw(st.sampled_from(["none", "price", "r_multiple"]))
    if kind == "none":
        return None
    if kind == "price":
        return BreakevenTrigger(price=draw(_price))
    return BreakevenTrigger(r_multiple=draw(st.floats(min_value=-5.0, max_value=5.0,
                                                       allow_nan=False, allow_infinity=False)))


@st.composite
def _trailing(draw):
    """An absent or arbitrary trailing-stop rule."""
    if draw(st.booleans()):
        return None
    return TrailingStop(
        atr_multiple=draw(st.one_of(st.none(), _fraction)),
        r_increment=draw(st.one_of(st.none(), _fraction)),
    )


@st.composite
def _plan(draw):
    """An arbitrary, possibly wholly-invalid Management_Plan (or None)."""
    if draw(st.booleans()):
        # Exercise the no-plan HOLD path too (single-target style call).
        return None
    return ManagementPlan(
        action=draw(st.sampled_from(["BUY", "SELL", "HOLD", "", "garbage"])),
        entry=draw(_price),
        initial_stop=draw(_price),
        legs=draw(_legs()),
        breakeven=draw(_breakeven()),
        trailing=draw(_trailing()),
        atr_14=draw(st.one_of(st.none(), _price)),
    )


@st.composite
def _levels(draw):
    """Arbitrary base execution levels, or None (missing levels)."""
    if draw(st.booleans()):
        return None
    return ExecutionLevels(
        entry=draw(_price),
        stop_loss=draw(_price),
        take_profit=draw(_price),
    )


# A hostile blended reward-to-risk floor: a large value that would reject almost
# any real plan if the plan checks ran. For HOLD it must be ignored entirely.
_min_blended = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
)

_atr = st.one_of(st.none(), _price)


# ─────────────────────────────────────────────────────────────────────────────
# Property 18 (task 6.7): HOLD bypasses all plan checks
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 18: HOLD bypasses all plan checks
@settings(max_examples=50, deadline=None)
@given(
    levels=_levels(),
    atr_14=_atr,
    plan=_plan(),
    min_blended_reward_to_risk=_min_blended,
)
def test_property_18_hold_bypasses_all_plan_checks(
    levels, atr_14, plan, min_blended_reward_to_risk
):
    """Feature: trade-management, Property 18: HOLD bypasses all plan checks —
    for Action.HOLD, ``validate_trade`` always passes with ``risk_reward == 0.0``
    and ``reason is None``, no matter how malformed or risk-violating the plan
    and levels are.

    Validates: Requirements 5.5
    """
    outcome = validate_trade(
        Action.HOLD,
        levels,
        atr_14=atr_14,
        plan=plan,
        min_blended_reward_to_risk=min_blended_reward_to_risk,
    )

    # HOLD abstains: it passes, carries the sentinel 0.0 risk_reward, and never
    # surfaces a rejection reason — every level AND plan check is bypassed (R5.5).
    assert outcome.is_pass() is True
    assert outcome.reason is None
    assert outcome.risk_reward == 0.0
