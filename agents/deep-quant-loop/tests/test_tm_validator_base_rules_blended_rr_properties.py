"""Property test: preserved base rules + blended reward-to-risk (validator.py, task 6.6).

Feature: trade-management

This module implements design **Property 17: Validator preserves base rules and
enforces the blended reward-to-risk**:

    With leg fractions, target ordering, and breakeven all valid, the
    Trade_Validator (a) STILL rejects a plan whose initial stop distance is
    smaller than ``1.5 × ATR`` when ATR is known (the ``StopTooTight`` base rule
    is preserved on top of the plan checks), and (b) accepts a plan IF AND ONLY
    IF the computed blended (fraction-weighted) reward-to-risk is at least the
    configured minimum, rejecting with ``BLENDED_RR_TOO_LOW`` otherwise.

Validates: Requirements 5.4.

The blended reward-to-risk is

    blended = Σ_i (fraction_i × |target_i − entry|) / |entry − initial_stop|

and REPLACES the single-target reward-to-risk check when a plan is present. The
base hard rules (direction ordering and ``stop ≥ 1.5 × ATR`` when ATR is known)
are NOT relaxed — they run first, so a too-tight stop is rejected as
``STOP_TOO_TIGHT`` even when the blended reward-to-risk would otherwise pass.

To isolate these two behaviours every other plan dimension is kept valid by
construction:

* leg fractions are each in ``(0.0, 1.0]`` and sum to ``≤ 1.0`` (so
  ``LEG_FRACTION_OUT_OF_RANGE`` never fires),
* scale-out targets are strictly on the profit side in monotone order (so
  ``TARGET_ORDERING_INCONSISTENT`` never fires),
* the breakeven trigger, when present, is a valid price strictly between entry
  and the first target (so ``BREAKEVEN_OUT_OF_RANGE`` never fires), and
* the base ``levels`` bracket is direction-consistent with ``stop_loss`` equal to
  the plan's ``initial_stop`` and ``take_profit`` equal to the first target.

The generator deliberately spans BOTH sides of each boundary: ATR values whose
``1.5 × ATR`` straddles the stop distance (plus the ATR-unknown case), and target
distances / fractions whose blended reward-to-risk straddles an explicitly passed
``min_blended_reward_to_risk``. The expected acceptance/rejection + reason is
computed by an independent oracle from the SAME float operands the validator
uses, so a boundary value cannot flip on a floating-point ULP.

The sys.path / import pattern mirrors ``tests/test_validator.py`` and the other
``test_tm_*`` property modules.
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
    MIN_STOP_ATR_MULTIPLE,
    Action,
    ExecutionLevels,
    ValidatorReason,
    validate_trade,
)


def _finite(min_value, max_value):
    return st.floats(
        min_value=min_value,
        max_value=max_value,
        allow_nan=False,
        allow_infinity=False,
    )


@st.composite
def _scenarios(draw):
    """Build a fraction-valid, monotone-target, breakeven-valid plan, plus an ATR
    that straddles the ``1.5 × ATR`` stop floor and an explicit blended-RR floor
    that straddles the plan's actual blended reward-to-risk.

    Returns ``(action, levels, atr_14, plan, min_rr)`` ready to drive
    ``validate_trade`` — the expected acceptance is recomputed by the test's
    oracle from these same operands.
    """
    side = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(_finite(50.0, 1000.0))
    stop_distance = draw(_finite(1.0, 100.0))

    # ── Profit-side, monotone targets ─────────────────────────────────────────
    n_legs = draw(st.integers(min_value=1, max_value=4))
    first_off = draw(_finite(0.5, 200.0))
    increments = [draw(_finite(0.0, 50.0)) for _ in range(n_legs - 1)]

    if side == "BUY":
        stop_loss = entry - stop_distance
        targets = [entry + first_off]
        for inc in increments:
            targets.append(targets[-1] + inc)
    else:  # SELL — mirror image.
        stop_loss = entry + stop_distance
        targets = [entry - first_off]
        for inc in increments:
            targets.append(targets[-1] - inc)

    first_target = targets[0]

    # ── Leg fractions: each in (0, 1], summing to a drawn total in (0, 1] ──────
    weights = [draw(_finite(0.1, 1.0)) for _ in range(n_legs)]
    total = draw(_finite(0.1, 1.0))
    weight_sum = sum(weights)
    fractions = [w / weight_sum * total for w in weights]
    legs = tuple(
        ScaleOutLeg(target=t, fraction=f) for t, f in zip(targets, fractions)
    )

    # ── Optional valid breakeven trigger (price strictly between entry/first) ──
    if draw(st.booleans()):
        breakeven = BreakevenTrigger(price=(entry + first_target) / 2.0)
    else:
        breakeven = None

    # ── ATR straddling the 1.5x stop floor, or unknown (None) ──────────────────
    # base ATR where 1.5 x base == stop_distance; multipliers below/above 1.0
    # straddle the StopTooTight boundary.
    base_atr = stop_distance / MIN_STOP_ATR_MULTIPLE
    atr_14 = draw(
        st.one_of(
            st.none(),
            _finite(base_atr * 0.4, base_atr * 2.0),
        )
    )

    plan = ManagementPlan(
        action=side,
        entry=entry,
        initial_stop=stop_loss,
        legs=legs,
        breakeven=breakeven,
        trailing=None,
        atr_14=atr_14,
    )

    # ── Blended reward-to-risk, computed exactly as the validator does ─────────
    stop_dist = abs(entry - stop_loss)
    blended = 0.0
    for leg, target in zip(legs, targets):
        blended += leg.fraction * (abs(target - entry) / stop_dist)

    # Floor straddling the actual blended value (below / above / exact boundary).
    min_rr = draw(
        st.one_of(
            _finite(0.0, blended * 2.0 + 1.0),
            st.just(blended),
        )
    )

    levels = ExecutionLevels(entry=entry, stop_loss=stop_loss, take_profit=first_target)
    action = Action.BUY if side == "BUY" else Action.SELL
    return action, levels, atr_14, plan, min_rr


# ─────────────────────────────────────────────────────────────────────────────
# Property 17 (task 6.6): Validator preserves base rules and enforces blended RR
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 17: Validator preserves base rules and enforces the blended reward-to-risk
@settings(max_examples=300, deadline=None)
@given(scenario=_scenarios())
def test_property_17_base_rules_and_blended_rr(scenario):
    """Feature: trade-management, Property 17: Validator preserves base rules and
    enforces the blended reward-to-risk — with fractions/ordering/breakeven all
    valid, ``validate_trade`` still rejects a too-tight stop (``< 1.5 × ATR`` when
    ATR is known) as ``STOP_TOO_TIGHT``, and otherwise accepts IFF the blended
    (fraction-weighted) reward-to-risk meets the configured minimum, rejecting
    with ``BLENDED_RR_TOO_LOW`` below it.

    Validates: Requirements 5.4
    """
    action, levels, atr_14, plan, min_rr = scenario

    outcome = validate_trade(
        action,
        levels,
        atr_14,
        plan=plan,
        min_blended_reward_to_risk=min_rr,
    )

    # ── Independent oracle, computed from the SAME float operands ──────────────
    entry = levels.entry
    stop_loss = levels.stop_loss
    risk = abs(entry - stop_loss)

    # (a) StopTooTight base rule preserved: runs BEFORE the plan checks.
    atr_known = atr_14 is not None and atr_14 > 0.0
    stop_too_tight = atr_known and risk < MIN_STOP_ATR_MULTIPLE * atr_14

    # (b) Blended reward-to-risk replaces the single-target RR check.
    blended = 0.0
    for leg in plan.legs:
        blended += leg.fraction * (abs(leg.target - entry) / risk)
    blended_too_low = blended < min_rr

    if stop_too_tight:
        # The base hard rule fires first regardless of the blended reward.
        assert not outcome.is_pass()
        assert outcome.reason == ValidatorReason.STOP_TOO_TIGHT
    elif blended_too_low:
        assert not outcome.is_pass()
        assert outcome.reason == ValidatorReason.BLENDED_RR_TOO_LOW
    else:
        assert outcome.is_pass()
        # On a pass the outcome carries the computed blended reward-to-risk.
        assert outcome.risk_reward is not None
