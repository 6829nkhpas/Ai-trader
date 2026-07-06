"""Property-based test that validation never relaxes the base rules (validator.py, task 6.8).

Feature: trade-management

This module implements design **Property 29: Validation never relaxes the base
rules**:

    Providing a Management_Plan never causes the validator to ACCEPT a trade that
    the base hard rules would reject. Concretely: for any (action, levels, atr)
    where the single-target / base rules would FAIL on the plan's own
    entry/initial_stop bracket (StopTooTight when ATR is known, or
    DirectionInconsistent), adding ANY plan must NOT make it pass — the
    plan-bearing call must also fail with the SAME base rejection. The multi-leg
    plan checks ADD to — never relax — the existing hard rules.

Validates: Requirements 14.2.

The base hard rules in :func:`validator.validate_trade` (direction ordering and
``stop >= 1.5x ATR``) run BEFORE the Management_Plan branch, so a plan can never
"rescue" a bracket the base rules reject. This property pins that guarantee and
guards against any future change that would let a plan relax it.

Construction (mirrors the careful comparison the design calls for): the base
bracket is derived from the plan's own ``entry`` / ``initial_stop`` (plus a
profit-side target), so the no-plan call and the plan-bearing call evaluate the
identical base bracket. Cases are generated to specifically violate the base
rules — a stop too tight given a known ATR, and a stop on the wrong side of entry
(direction-inconsistent) — alongside a control band of base-valid brackets.

The sys.path / import pattern mirrors ``tests/test_tm_validator_leg_fraction_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (validator.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from validator import (  # noqa: E402
    MIN_STOP_ATR_MULTIPLE,
    Action,
    ExecutionLevels,
    ValidatorReason,
    validate_trade,
)
from trade_manager import ManagementPlan, ScaleOutLeg  # noqa: E402


# ── Strategies ────────────────────────────────────────────────────────────────
# Three scenarios, each derived from the plan's own entry/initial_stop bracket:
#   * "stop_tight"  — direction ordering is correct but the stop distance is
#                     below 1.5x ATR with ATR known  -> base STOP_TOO_TIGHT.
#   * "direction"   — the stop is on the wrong (profit) side of entry
#                     -> base DIRECTION_INCONSISTENT.
#   * "valid"       — a control band: correct ordering and a stop at/above
#                     1.5x ATR with a healthy reward, so the base rules PASS.
_action = st.sampled_from([Action.BUY, Action.SELL])
_scenario = st.sampled_from(["stop_tight", "direction", "valid"])
_entry = st.floats(min_value=50.0, max_value=500.0, allow_nan=False, allow_infinity=False)
_atr = st.floats(min_value=1.0, max_value=20.0, allow_nan=False, allow_infinity=False)
# A fraction strictly inside (0, 1) -> a tight stop distance below 1.5x ATR.
_tight_frac = st.floats(min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False)
# A multiple at/above 1 -> a stop distance at/above 1.5x ATR (base passes).
_loose_mult = st.floats(min_value=1.0, max_value=5.0, allow_nan=False, allow_infinity=False)


def _base_hard_rule_violated(action, entry, stop_loss, take_profit, atr):
    """Independently derive which base hard rule (if any) the bracket violates.

    Mirrors the base checks in :func:`validator.validate_trade` EXACTLY and in
    the same order (direction ordering before the stop-tightness floor), so the
    test's expectation is computed from the acceptance criteria rather than read
    back from the implementation under test. Returns the failing
    :class:`ValidatorReason` (``DIRECTION_INCONSISTENT`` or ``STOP_TOO_TIGHT``)
    or ``None`` when the base hard rules are satisfied.
    """
    if action == Action.BUY:
        direction_ok = stop_loss < entry < take_profit
    else:  # SELL
        direction_ok = take_profit < entry < stop_loss
    if not direction_ok:
        return ValidatorReason.DIRECTION_INCONSISTENT

    risk = abs(entry - stop_loss)
    if risk <= 0.0:
        return ValidatorReason.DIRECTION_INCONSISTENT

    # ATR is known/finite/positive in every generated case here.
    if risk < MIN_STOP_ATR_MULTIPLE * atr:
        return ValidatorReason.STOP_TOO_TIGHT

    return None


def _build_case(action, scenario, entry, atr, tight_frac, loose_mult):
    """Build a (plan, levels, atr) triple whose base bracket comes from the
    plan's own entry/initial_stop, exercising the chosen base-rule scenario.

    The Management_Plan itself is kept well-formed (a single profit-side leg at
    fraction ``1.0`` with a healthy reward), so that — were the base bracket
    valid — the plan would pass. This makes the rejection in the violation
    scenarios attributable solely to the base hard rules the plan must not relax.
    """
    is_buy = action == Action.BUY

    if scenario == "stop_tight":
        # Correct side, but distance strictly below 1.5x ATR -> STOP_TOO_TIGHT.
        risk = MIN_STOP_ATR_MULTIPLE * atr * tight_frac
        initial_stop = entry - risk if is_buy else entry + risk
    elif scenario == "valid":
        # Correct side, distance at/above 1.5x ATR -> base passes.
        risk = MIN_STOP_ATR_MULTIPLE * atr * loose_mult
        initial_stop = entry - risk if is_buy else entry + risk
    else:  # "direction"
        # Stop on the wrong (profit) side of entry -> DIRECTION_INCONSISTENT.
        risk = atr * tight_frac + 1.0
        initial_stop = entry + risk if is_buy else entry - risk

    # A healthy profit-side target (reward / risk > 2) so a valid bracket passes
    # the single-target and blended reward-to-risk checks comfortably.
    reward = abs(risk) * 2.0 + 1.0
    target = entry + reward if is_buy else entry - reward

    legs = (ScaleOutLeg(target=target, fraction=1.0),)
    plan = ManagementPlan(
        action=action.value,
        entry=entry,
        initial_stop=initial_stop,
        legs=legs,
        breakeven=None,
        trailing=None,
        atr_14=atr,
    )
    levels = ExecutionLevels(entry=entry, stop_loss=initial_stop, take_profit=target)
    return plan, levels, target


# ─────────────────────────────────────────────────────────────────────────────
# Property 29 (task 6.8): Validation never relaxes the base rules
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 29: Validation never relaxes the base rules
@settings(max_examples=50, deadline=None)
@given(
    action=_action,
    scenario=_scenario,
    entry=_entry,
    atr=_atr,
    tight_frac=_tight_frac,
    loose_mult=_loose_mult,
)
def test_property_29_validation_never_relaxes_base_rules(
    action, scenario, entry, atr, tight_frac, loose_mult
):
    """Feature: trade-management, Property 29: Validation never relaxes the base
    rules — for a bracket the base hard rules reject (StopTooTight with ATR known,
    or DirectionInconsistent), adding a Management_Plan never makes it pass; the
    plan-bearing call fails with the SAME base rejection. When the base rules
    accept, the well-formed plan also passes (the plan adds checks, never relaxes).

    Validates: Requirements 14.2
    """
    plan, levels, take_profit = _build_case(
        action, scenario, entry, atr, tight_frac, loose_mult
    )

    # Independently derive the base-rule violation from the plan's own bracket.
    violated = _base_hard_rule_violated(
        action, plan.entry, plan.initial_stop, take_profit, atr
    )

    # No-plan call (the base hard rules) on the identical derived bracket.
    base_outcome = validate_trade(action, levels, atr, plan=None)
    # Plan-bearing call on the identical bracket. The blended floor is pinned to
    # 0.0 so the plan's own reward-to-risk can never be the deciding rejection —
    # any rejection here must come from the base hard rules, proving the plan
    # does not (and cannot) relax them.
    plan_outcome = validate_trade(
        action, levels, atr, plan=plan, min_blended_reward_to_risk=0.0
    )

    if violated is not None:
        # The base rules reject this bracket on a hard rule.
        assert not base_outcome.is_pass()
        assert base_outcome.reason == violated

        # Property 29: the plan never relaxes the base rules — it must ALSO be
        # rejected, and with the SAME base reason (the plan branch is never even
        # reached because the base checks fire first).
        assert not plan_outcome.is_pass()
        assert plan_outcome.reason == violated
    else:
        # Control: the base rules accept the bracket; with a well-formed plan and
        # a zero blended floor the plan-bearing call also passes. This confirms
        # the plan is genuinely acceptable, so the rejections above are caused by
        # the base rules and not by an incidentally malformed plan.
        assert base_outcome.is_pass()
        assert plan_outcome.is_pass()
