"""Property-based test for validator breakeven placement (validator.py, task 6.5).

Feature: trade-management

This module implements design **Property 16: Validator breakeven placement**:

    For a plan with a Breakeven_Trigger, the Trade_Validator accepts it if and
    only if the trigger lies strictly between the entry and the first scale-out
    leg's target on the trade's profit side.

Validates: Requirements 5.3.

The validator checks a breakeven trigger two ways (see
``validator._validate_management_plan``):

* a **price-form** trigger is range-checked directly — for a BUY it must satisfy
  ``entry < price < first_target``; for a SELL ``first_target < price < entry``;
* an **r_multiple-form** trigger is checked in R-space — the entry sits at ``0R``
  and the first target at ``first_target_r = |first_target - entry| /
  stop_distance``, so the trigger is accepted iff ``0 < r_multiple <
  first_target_r``.

Each generated scenario keeps the leg fractions in ``(0.0, 1.0]`` (summing to
≤ 1.0), the targets strictly on the profit side in monotone order, and the base
bracket direction-consistent, and passes an explicit ``min_blended_reward_to_risk
= 0.0`` so the leg-fraction, target-ordering, and blended-reward-to-risk checks
all pass — leaving the breakeven placement as the only check that can decide
acceptance. We then assert acceptance/rejection matches the predicate exactly,
generating valid and invalid price-form and r_multiple-form triggers (below
entry, at/above the first target, negative ``r``, ``r >= first_target_r``) for
both BUY and SELL.

The sys.path / import pattern mirrors ``tests/test_validator.py`` and
``tests/test_tm_config_default_fallback_properties.py``.
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
)
from validator import (  # noqa: E402
    Action,
    ExecutionLevels,
    ValidatorReason,
    validate_trade,
)


# A reward-to-risk floor of 0.0 makes the blended-reward-to-risk check
# unconditionally pass (every profit-side target sits a strictly positive
# distance from entry), so only the breakeven placement can decide acceptance.
_MIN_BLENDED_RR = 0.0


def _finite(min_value, max_value):
    return st.floats(
        min_value=min_value,
        max_value=max_value,
        allow_nan=False,
        allow_infinity=False,
    )


@st.composite
def _scenarios(draw):
    """Generate a direction-consistent, fraction-valid, monotone-target plan plus
    a breakeven trigger (price-form or r_multiple-form), and return everything
    needed to drive the validator and compute the expected acceptance.
    """
    side = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(_finite(10.0, 1000.0))
    stop_distance = draw(_finite(1.0, 100.0))
    first_off = draw(_finite(0.5, 200.0))

    # Build one-to-three legs strictly on the profit side, in monotone order.
    n_legs = draw(st.integers(min_value=1, max_value=3))
    increments = [draw(_finite(0.0, 50.0)) for _ in range(n_legs - 1)]

    if side == "BUY":
        stop_loss = entry - stop_distance
        targets = [entry + first_off]
        for inc in increments:
            targets.append(targets[-1] + inc)
    else:  # SELL
        stop_loss = entry + stop_distance
        targets = [entry - first_off]
        for inc in increments:
            targets.append(targets[-1] - inc)

    first_target = targets[0]

    # Fractions each in (0.0, 1.0] summing to <= 1.0 (0.8 split evenly).
    fraction = 0.8 / n_legs
    legs = tuple(ScaleOutLeg(target=t, fraction=fraction) for t in targets)

    # Breakeven trigger: price-form or r_multiple-form, spanning valid AND invalid
    # values (below entry / at-or-beyond the first target, negative r, r at/above
    # first_target_r), including exact boundaries. ``first_target_r`` is computed
    # from the SAME operands the validator uses (``entry`` and the plan's
    # ``initial_stop`` == ``stop_loss``) so a boundary value sampled here is
    # bit-identical to the validator's recomputed threshold and the strict
    # comparison cannot flip on a floating-point ULP.
    first_target_r = abs(first_target - entry) / abs(entry - stop_loss)
    form = draw(st.sampled_from(["price", "r_multiple"]))

    if form == "price":
        # Span a window that brackets the valid (entry, first_target) interval on
        # both sides, and also draw the exact boundaries that must be rejected.
        lo = min(entry, first_target)
        hi = max(entry, first_target)
        span = (hi - lo) if hi > lo else 1.0
        be_price = draw(
            st.one_of(
                _finite(lo - span, hi + span),
                st.sampled_from([entry, first_target]),
            )
        )
        breakeven = BreakevenTrigger(price=be_price)
        if side == "BUY":
            expected_accept = entry < be_price < first_target
        else:
            expected_accept = first_target < be_price < entry
    else:
        be_r = draw(
            st.one_of(
                _finite(-2.0, first_target_r + 2.0),
                st.sampled_from([0.0, first_target_r]),
            )
        )
        breakeven = BreakevenTrigger(r_multiple=be_r)
        expected_accept = 0.0 < be_r < first_target_r

    plan = ManagementPlan(
        action=side,
        entry=entry,
        initial_stop=stop_loss,
        legs=legs,
        breakeven=breakeven,
        trailing=None,
        atr_14=None,
    )
    # The base bracket must itself be direction-consistent; use the first target
    # as the take-profit so entry sits strictly between stop and target.
    levels = ExecutionLevels(entry=entry, stop_loss=stop_loss, take_profit=first_target)
    action = Action.BUY if side == "BUY" else Action.SELL
    return action, levels, plan, expected_accept


# ─────────────────────────────────────────────────────────────────────────────
# Property 16 (task 6.5): Validator breakeven placement
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 16: Validator breakeven placement
@settings(max_examples=200, deadline=None)
@given(scenario=_scenarios())
def test_property_16_validator_breakeven_placement(scenario):
    """Feature: trade-management, Property 16: Validator breakeven placement —
    for a plan with a Breakeven_Trigger, ``validate_trade`` accepts it IFF the
    trigger lies strictly between entry and the first scale-out target on the
    trade's profit side (price-form checked directly, r_multiple-form checked in
    R-space against ``first_target_r``).

    Validates: Requirements 5.3
    """
    action, levels, plan, expected_accept = scenario

    outcome = validate_trade(
        action,
        levels,
        atr_14=None,
        plan=plan,
        min_blended_reward_to_risk=_MIN_BLENDED_RR,
    )

    # Acceptance must match the breakeven-placement predicate exactly.
    assert outcome.is_pass() == expected_accept

    # When rejected, it must be specifically the breakeven check that fired (the
    # leg-fraction, target-ordering, and blended-reward-to-risk checks are all
    # satisfied by construction).
    if not expected_accept:
        assert outcome.reason == ValidatorReason.BREAKEVEN_OUT_OF_RANGE
