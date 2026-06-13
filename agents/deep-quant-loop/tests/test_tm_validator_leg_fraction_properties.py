"""Property-based test for validator leg-fraction bounds (validator.py, task 6.3).

Feature: trade-management

This module implements design **Property 14: Validator leg-fraction bounds**:

    For any Management_Plan, the Trade_Validator accepts the leg fractions if and
    only if every leg fraction lies in ``(0.0, 1.0]`` and the fractions sum to at
    most ``1.0`` (with the ``LEG_FRACTION_SUM_TOLERANCE`` slack on the sum so a
    plan summing to exactly ``1.0`` is never rejected by floating-point noise).

Validates: Requirements 1.5, 5.1.

To isolate the leg-fraction predicate as the *only* deciding check, every other
aspect of each generated plan is kept valid:

* valid base bracket / direction ordering (BUY: ``stop < entry < take_profit``;
  SELL mirror), with ``atr_14=None`` so the ``stop >= 1.5x ATR`` rule is skipped;
* scale-out targets strictly on the profit side of entry and in monotone order
  (Property 15's concern), independent of the fractions;
* no breakeven trigger (Property 16's concern), so the breakeven check is a no-op;
* an explicit ``min_blended_reward_to_risk=0.0`` so the blended reward-to-risk
  check (Property 17's concern) always passes and never masks the fraction
  decision — and so the test is deterministic regardless of the environment.

With every other check fixed to pass, acceptance is decided solely by the leg
fractions, so ``validate_trade(...).is_pass()`` must equal the fraction predicate
and any rejection must carry ``ValidatorReason.LEG_FRACTION_OUT_OF_RANGE``.

The sys.path / import pattern mirrors ``tests/test_tm_config_path_independent_properties.py``.
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
    LEG_FRACTION_SUM_TOLERANCE,
    Action,
    ExecutionLevels,
    ValidatorReason,
    validate_trade,
)
from trade_manager import ManagementPlan, ScaleOutLeg  # noqa: E402


# ── Strategies ────────────────────────────────────────────────────────────────
# Each leg gets a finite fraction drawn from a band that straddles both bounds of
# the valid window ``(0.0, 1.0]`` — so the generator produces fractions that are
# negative, exactly zero, in-range, and above one, plus multi-leg sets whose sum
# spans both sides of ``1.0``. This exercises the accept branch and every reject
# branch (out-of-range individual fraction AND sum-over-one).
_fraction = st.floats(
    min_value=-0.5,
    max_value=1.5,
    allow_nan=False,
    allow_infinity=False,
)

_fractions = st.lists(_fraction, min_size=1, max_size=5)
_action = st.sampled_from([Action.BUY, Action.SELL])


def _fractions_valid(fractions):
    """The leg-fraction predicate, mirroring validator.py exactly (R5.1).

    Accept iff every fraction lies in ``(0.0, 1.0]`` AND the fractions sum to at
    most ``1.0`` within ``LEG_FRACTION_SUM_TOLERANCE``.
    """
    if not all(0.0 < f <= 1.0 for f in fractions):
        return False
    return sum(fractions) <= 1.0 + LEG_FRACTION_SUM_TOLERANCE


def _build_plan_and_levels(action, fractions):
    """Construct an otherwise-valid plan + base bracket whose ONLY questionable
    aspect is its leg fractions.

    BUY: ``entry=100``, ``initial_stop=90``, targets strictly increasing above
    entry. SELL: the mirror image (targets strictly decreasing below entry).
    Breakeven/trailing are omitted and ``atr_14`` is ``None``.
    """
    entry = 100.0
    n = len(fractions)
    if action == Action.BUY:
        initial_stop = 90.0
        # Strictly increasing targets above entry -> non-decreasing & profit-side.
        targets = [entry + (i + 1) for i in range(n)]
        take_profit = targets[0]
    else:  # SELL
        initial_stop = 110.0
        # Strictly decreasing targets below entry -> non-increasing & profit-side.
        targets = [entry - (i + 1) for i in range(n)]
        take_profit = targets[0]

    legs = tuple(
        ScaleOutLeg(target=t, fraction=f) for t, f in zip(targets, fractions)
    )
    plan = ManagementPlan(
        action=action.value,
        entry=entry,
        initial_stop=initial_stop,
        legs=legs,
        breakeven=None,
        trailing=None,
        atr_14=None,
    )
    levels = ExecutionLevels(entry=entry, stop_loss=initial_stop, take_profit=take_profit)
    return plan, levels


# ─────────────────────────────────────────────────────────────────────────────
# Property 14 (task 6.3): Validator leg-fraction bounds
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 14: Validator leg-fraction bounds
@settings(max_examples=200, deadline=None)
@given(action=_action, fractions=_fractions)
def test_property_14_validator_leg_fraction_bounds(action, fractions):
    """Feature: trade-management, Property 14: Validator leg-fraction bounds —
    the Trade_Validator accepts the leg fractions IFF every fraction lies in
    ``(0.0, 1.0]`` and the fractions sum to at most ``1.0``. Every other plan
    aspect is held valid so only the fraction bound decides; a rejection must
    carry ``LEG_FRACTION_OUT_OF_RANGE``.

    Validates: Requirements 1.5, 5.1
    """
    plan, levels = _build_plan_and_levels(action, fractions)

    outcome = validate_trade(
        action,
        levels,
        atr_14=None,
        plan=plan,
        # Force the blended reward-to-risk floor to 0.0 so it can never mask the
        # fraction decision and the test stays deterministic across environments.
        min_blended_reward_to_risk=0.0,
    )

    expected_accept = _fractions_valid(fractions)

    # Acceptance matches the fraction predicate exactly (the IFF).
    assert outcome.is_pass() == expected_accept

    # When rejected, it is specifically the leg-fraction check that fired (every
    # other check was held valid), confirming the predicate is what decided.
    if not expected_accept:
        assert outcome.reason == ValidatorReason.LEG_FRACTION_OUT_OF_RANGE
