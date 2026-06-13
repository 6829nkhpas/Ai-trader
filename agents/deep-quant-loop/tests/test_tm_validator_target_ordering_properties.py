"""Property-based test for validator target ordering (validator.py, task 6.4).

Feature: trade-management

This module implements design **Property 15: Validator target ordering**:

    For a BUY ``Management_Plan`` the ``Trade_Validator`` accepts the levels IF
    AND ONLY IF ``initial_stop < entry``, every scale-out target is strictly
    greater than entry, and the targets are in non-decreasing order; the
    mirror-image ordering (``initial_stop > entry``, every target strictly less
    than entry, targets in non-increasing order) holds for a SELL.

Validates: Requirements 5.2.

To isolate the ordering check (so the only plan rejection reason in play is
``TARGET_ORDERING_INCONSISTENT``) the generated plans keep every other plan
dimension valid:

* leg fractions are always in ``(0.0, 1.0]`` and sum well under ``1.0`` (so
  ``LEG_FRACTION_OUT_OF_RANGE`` never fires),
* no breakeven trigger is supplied (so ``BREAKEVEN_OUT_OF_RANGE`` never fires),
* an explicit ``min_blended_reward_to_risk=0.0`` is passed (the fraction-weighted
  blended reward is a sum of non-negative terms, so ``BLENDED_RR_TOO_LOW`` can
  never fire), and
* the base bracket ``levels`` passed to ``validate_trade`` is a fixed, always
  direction-consistent bracket with ``atr_14=None`` (so the base hard rules pass
  and execution reaches the multi-leg ordering check).

The sys.path / import pattern mirrors ``tests/test_validator.py`` and the other
``test_tm_*`` property modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (validator.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from trade_manager import ManagementPlan, ScaleOutLeg  # noqa: E402
from validator import (  # noqa: E402
    Action,
    ExecutionLevels,
    ValidatorReason,
    validate_trade,
)

# A fixed entry shared by the plan and the base bracket, so the ordering of the
# plan is the only thing under test.
ENTRY = 100.0

# Always direction-consistent base brackets (entry/stop/take-profit) per side, so
# the base hard rules pass and execution reaches the multi-leg ordering check.
# atr_14 is None so the stop-distance rule is skipped. The single-target
# reward-to-risk check is replaced by the (always-passing) blended check because
# a plan is present.
_LEVELS = {
    "BUY": ExecutionLevels(entry=ENTRY, stop_loss=90.0, take_profit=130.0),
    "SELL": ExecutionLevels(entry=ENTRY, stop_loss=110.0, take_profit=70.0),
}

# Bounded magnitudes for stop and target offsets from entry.
_OFFSET = st.floats(min_value=1.0, max_value=50.0, allow_nan=False, allow_infinity=False)
# Signed offsets (may land a target on the wrong side of, or exactly at, entry).
_SIGNED_OFFSET = st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False)


def _ordering_accepts(action: str, entry: float, initial_stop: float, targets) -> bool:
    """The Property-15 predicate: the levels are acceptable for ordering reasons
    iff the stop is on the loss side, every target is strictly on the profit
    side, and the targets are monotone toward profit (non-decreasing for BUY,
    non-increasing for SELL). Computed independently of ``validator.py`` so the
    test is a genuine oracle, not a tautology.
    """
    if not targets:
        return False
    if action == "BUY":
        if not (initial_stop < entry):
            return False
        previous = entry
        for target in targets:
            if not (target > entry and target >= previous):
                return False
            previous = target
        return True
    # SELL — mirror image.
    if not (initial_stop > entry):
        return False
    previous = entry
    for target in targets:
        if not (target < entry and target <= previous):
            return False
        previous = target
    return True


@st.composite
def _ordering_case(draw):
    """Generate plans across both valid and invalid orderings.

    With ``make_valid`` we build a guaranteed-acceptable plan (stop on the loss
    side, monotone profit-side targets — duplicates allowed to exercise the
    non-strict ordering boundary). Otherwise we build an arbitrary arrangement
    (targets on either side of entry / out of order, stop on either side) so
    shuffled, wrong-side-target, and wrong-side-stop rejections are all covered.
    """
    action = draw(st.sampled_from(["BUY", "SELL"]))
    n = draw(st.integers(min_value=1, max_value=4))
    make_valid = draw(st.booleans())
    stop_off = draw(_OFFSET)

    if make_valid:
        offsets = sorted(draw(st.lists(_OFFSET, min_size=n, max_size=n)))  # ascending magnitudes
        if action == "BUY":
            initial_stop = ENTRY - stop_off
            targets = [ENTRY + o for o in offsets]  # increasing, all > entry
        else:
            initial_stop = ENTRY + stop_off
            targets = [ENTRY - o for o in offsets]  # decreasing, all < entry
    else:
        signed = draw(st.lists(_SIGNED_OFFSET, min_size=n, max_size=n))
        targets = [ENTRY + s for s in signed]
        stop_sign = draw(st.sampled_from([-1.0, 1.0]))
        initial_stop = ENTRY + stop_sign * stop_off

    return action, initial_stop, targets


# ─────────────────────────────────────────────────────────────────────────────
# Property 15 (task 6.4): Validator target ordering
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 15: Validator target ordering
@settings(max_examples=300, deadline=None)
@given(case=_ordering_case())
def test_property_15_validator_target_ordering(case):
    """Feature: trade-management, Property 15: Validator target ordering — the
    validator accepts a BUY plan's levels iff ``initial_stop < entry``, every
    target is strictly greater than entry, and the targets are non-decreasing;
    the mirror image holds for a SELL. Acceptance/rejection must match the
    ordering predicate exactly, and a rejection must cite
    ``TARGET_ORDERING_INCONSISTENT``.

    Validates: Requirements 5.2
    """
    action, initial_stop, targets = case

    # Keep every other plan dimension valid so ordering is the only thing tested:
    # small equal fractions well under 1.0, and no breakeven trigger.
    legs = tuple(ScaleOutLeg(target=t, fraction=0.1) for t in targets)
    plan = ManagementPlan(
        action=action,
        entry=ENTRY,
        initial_stop=initial_stop,
        legs=legs,
        breakeven=None,
        trailing=None,
        atr_14=None,
    )

    outcome = validate_trade(
        Action[action],
        _LEVELS[action],
        None,                          # atr_14 unknown -> stop-distance rule skipped
        plan=plan,
        min_blended_reward_to_risk=0.0,  # blended RR can never fail -> isolate ordering
    )

    expected_accept = _ordering_accepts(action, ENTRY, initial_stop, targets)
    assert outcome.is_pass() == expected_accept

    # A rejection here can only be an ordering rejection given the isolation.
    if not expected_accept:
        assert outcome.reason == ValidatorReason.TARGET_ORDERING_INCONSISTENT
