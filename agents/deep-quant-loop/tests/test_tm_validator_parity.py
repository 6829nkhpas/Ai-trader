"""Python <-> Rust validator parity examples (validator.py, task 6.9).

Feature: trade-management

This module pins down a **shared fixture table** that is exercised on BOTH
validator implementations to prove they agree on identical inputs
(Requirement 5.6):

* the Python ``validator.validate_trade(action, levels, atr_14, plan=...,
  min_blended_reward_to_risk=2.0)`` mirror (this file), and
* the authoritative Rust ``quant::validate_management_plan(action, &plan,
  atr_14, min_blended_reward_to_risk)`` (the matching ``plan_parity_fixtures``
  test in ``frontend/src-tauri/src/quant/mod.rs``).

Both implementations return a ``ValidatorOutcome`` and expose the SAME stable
reason tags (Python ``ValidatorReason.tag`` / Rust ``ValidatorReason::as_tag``),
so the agreement assertion is "the same fixture yields the same tag (or a pass)
on both sides".

The table below is the single source of truth for the fixtures; the Rust test
re-encodes the identical rows. It contains a representative valid plan (BUY and
the SELL mirror) plus exactly one fixture per rejection class:

| name                | action | entry | stop  | legs (target, fraction)      | breakeven        | atr  | expected tag                    |
| ------------------- | ------ | ----- | ----- | ---------------------------- | ---------------- | ---- | ------------------------------- |
| valid_buy           | BUY    | 100   | 90    | (120, 0.5), (140, 0.5)       | price 110        | None | <pass> (blended RR 3.0)         |
| valid_sell          | SELL   | 100   | 110   | (80, 0.5), (60, 0.5)         | r_multiple 1.0   | None | <pass> (blended RR 3.0)         |
| leg_fraction        | BUY    | 100   | 90    | (120, 1.5)                   | --               | None | leg-fraction-out-of-range       |
| target_ordering     | BUY    | 100   | 90    | (140, 0.5), (120, 0.5)       | --               | None | target-ordering-inconsistent    |
| breakeven_range     | BUY    | 100   | 90    | (120, 0.5), (140, 0.5)       | price 95         | None | breakeven-out-of-range          |
| blended_rr          | BUY    | 100   | 90    | (115, 1.0)                   | --               | None | blended-rr-too-low              |
| stop_too_tight      | BUY    | 100   | 90    | (140, 1.0)                   | --               | 10   | stop-too-tight                  |
| hold_bypass         | HOLD   | 100   | 200   | (50, 2.0)                    | --               | 10   | <pass> (bypass, RR 0.0)         |

Every fixture isolates a SINGLE deciding condition (all other plan aspects are
held valid) so the differing internal check-ordering of the two implementations
cannot change which tag is produced. The blended reward-to-risk minimum is
pinned to ``2.0`` (the documented default) on both sides so the examples are
deterministic regardless of environment variables.

The sys.path / import pattern mirrors ``tests/test_tm_validator_leg_fraction_properties.py``.
"""

import os
import sys

import pytest

# Make the service package importable (validator.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from validator import (  # noqa: E402
    Action,
    ExecutionLevels,
    validate_trade,
)
from trade_manager import (  # noqa: E402
    BreakevenTrigger,
    ManagementPlan,
    ScaleOutLeg,
)


# The blended reward-to-risk floor pinned on both sides (the documented default
# for TM_MIN_BLENDED_REWARD_TO_RISK). Mirrors Rust `MIN_BLENDED_REWARD_TO_RISK`.
MIN_BLENDED_RR = 2.0


# ── Shared fixture table (mirrored verbatim by the Rust `plan_parity_fixtures`) ─
#
# Each row: (name, action, entry, initial_stop, legs, breakeven, atr_14, expected_tag)
#   * legs       — list of (target, fraction)
#   * breakeven  — None | ("price", value) | ("r", value)
#   * expected_tag — None means "passes"; otherwise the stable reason tag both
#     implementations must return.
PARITY_FIXTURES = [
    (
        "valid_buy",
        Action.BUY,
        100.0,
        90.0,
        [(120.0, 0.5), (140.0, 0.5)],
        ("price", 110.0),
        None,
        None,
    ),
    (
        "valid_sell",
        Action.SELL,
        100.0,
        110.0,
        [(80.0, 0.5), (60.0, 0.5)],
        ("r", 1.0),
        None,
        None,
    ),
    (
        "leg_fraction",
        Action.BUY,
        100.0,
        90.0,
        [(120.0, 1.5)],
        None,
        None,
        "leg-fraction-out-of-range",
    ),
    (
        "target_ordering",
        Action.BUY,
        100.0,
        90.0,
        [(140.0, 0.5), (120.0, 0.5)],
        None,
        None,
        "target-ordering-inconsistent",
    ),
    (
        "breakeven_range",
        Action.BUY,
        100.0,
        90.0,
        [(120.0, 0.5), (140.0, 0.5)],
        ("price", 95.0),
        None,
        "breakeven-out-of-range",
    ),
    (
        "blended_rr",
        Action.BUY,
        100.0,
        90.0,
        [(115.0, 1.0)],
        None,
        None,
        "blended-rr-too-low",
    ),
    (
        "stop_too_tight",
        Action.BUY,
        100.0,
        90.0,
        [(140.0, 1.0)],
        None,
        10.0,
        "stop-too-tight",
    ),
    (
        "hold_bypass",
        Action.HOLD,
        100.0,
        200.0,
        [(50.0, 2.0)],
        None,
        10.0,
        None,
    ),
]


def _build_breakeven(breakeven):
    if breakeven is None:
        return None
    kind, value = breakeven
    if kind == "price":
        return BreakevenTrigger(price=value)
    return BreakevenTrigger(r_multiple=value)


def _build_plan(action, entry, initial_stop, legs, breakeven):
    return ManagementPlan(
        action=action.value,
        entry=entry,
        initial_stop=initial_stop,
        legs=tuple(ScaleOutLeg(target=t, fraction=f) for t, f in legs),
        breakeven=_build_breakeven(breakeven),
        trailing=None,
        atr_14=None,
    )


def _build_levels(action, entry, initial_stop, legs):
    """Build the base bracket that the Python validator level-checks before the
    plan checks run.

    The take-profit is set to the first leg's target so the base direction
    ordering (BUY: ``stop < entry < take_profit``; SELL mirror) always passes —
    which leaves the isolated plan-level condition as the sole decider, exactly
    as the Rust ``validate_management_plan`` (which has no separate base bracket)
    decides it. For the inconsistent-ordering fixture the first target is still
    on the profit side, so only the *plan's* monotonicity check fires.
    """
    first_target = legs[0][0]
    return ExecutionLevels(entry=entry, stop_loss=initial_stop, take_profit=first_target)


@pytest.mark.parametrize(
    "name,action,entry,initial_stop,legs,breakeven,atr_14,expected_tag",
    PARITY_FIXTURES,
    ids=[row[0] for row in PARITY_FIXTURES],
)
def test_python_validator_parity_fixture(
    name, action, entry, initial_stop, legs, breakeven, atr_14, expected_tag
):
    """Each shared fixture yields the expected outcome on the Python validator;
    the matching Rust ``plan_parity_fixtures`` test asserts the SAME tag for the
    SAME fixture, proving the two implementations agree (Requirement 5.6).
    """
    plan = _build_plan(action, entry, initial_stop, legs, breakeven)
    levels = _build_levels(action, entry, initial_stop, legs)

    outcome = validate_trade(
        action,
        levels,
        atr_14=atr_14,
        plan=plan,
        min_blended_reward_to_risk=MIN_BLENDED_RR,
    )

    if expected_tag is None:
        assert outcome.is_pass(), f"{name}: expected pass, got {outcome.reason}"
    else:
        assert not outcome.is_pass(), f"{name}: expected reject {expected_tag}, got pass"
        assert outcome.reason.tag == expected_tag, (
            f"{name}: expected tag {expected_tag}, got {outcome.reason.tag}"
        )


def test_parity_table_covers_every_rejection_class():
    """Guard: the shared table must exercise a valid plan plus each rejection
    class and the HOLD bypass, so the parity contract stays complete.
    """
    tags = {row[7] for row in PARITY_FIXTURES}
    assert tags == {
        None,  # valid plans + HOLD bypass
        "leg-fraction-out-of-range",
        "target-ordering-inconsistent",
        "breakeven-out-of-range",
        "blended-rr-too-low",
        "stop-too-tight",
    }
