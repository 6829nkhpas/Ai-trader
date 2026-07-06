"""Trade_Validator — Python mirror of the Rust pure validator (R6.1–R6.5).

Feature: deep-quant-analysis-hardening (task 5.2)

This module mirrors ``frontend/src-tauri/src/quant/mod.rs`` (`validate_trade`,
`Action`, `ExecutionLevels`, `ValidatorReason`, `ValidatorOutcome`) EXACTLY so
that ``declare_trade`` can validate a proposed trade on the Python side before
(or alongside) the Rust Tool Server. The two implementations must always agree
on the same inputs.

Hard risk rules (BUY/SELL only — HOLD bypasses every level check):

1. MissingLevels (R6.1)        — entry/stop-loss/take-profit all required and finite.
2. DirectionInconsistent (R6.4/R6.5) — BUY needs ``stop_loss < entry < take_profit``;
                                       SELL needs ``take_profit < entry < stop_loss``.
3. StopTooTight (R6.3)         — when ATR is known/finite/positive, the stop distance
                                 ``|entry - stop_loss|`` must be at least ``1.5 × ATR``.
4. RiskRewardTooLow (R6.2)     — ``|take_profit - entry| / |entry - stop_loss|`` must be
                                 at least ``2.0`` (the boundary value 2.0 passes).

The function is pure: identical inputs always yield an identical outcome.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ── Constants (must match the Rust constants exactly) ─────────────────────────

# The minimum acceptable Risk_Reward_Ratio (reward / risk). A value exactly at
# the boundary (2.0) passes; below 2.0 fails (R6.2).
MIN_RISK_REWARD: float = 2.0

# The minimum stop-loss distance expressed as a multiple of ATR. A stop distance
# exactly at ``1.5 × ATR`` passes; below fails (R6.3).
MIN_STOP_ATR_MULTIPLE: float = 1.5

# Tolerance applied to the leg-fraction *sum* upper bound so that a plan whose
# fractions sum to exactly ``1.0`` is not rejected by floating-point noise (for
# example ``0.3 + 0.3 + 0.4`` not summing to a clean ``1.0``). Individual
# fractions are still bounded strictly to ``(0.0, 1.0]`` (Requirement 5.1). The
# Rust validator mirror (task 6.2) MUST use the same tolerance so the two
# implementations agree on identical inputs.
LEG_FRACTION_SUM_TOLERANCE: float = 1e-9


class Action(Enum):
    """The directional intent of a declared trade.

    ``BUY``/``SELL`` are directional and subject to the full set of
    Trade_Validator checks. ``HOLD`` is an abstention and bypasses all
    level-based checks (R6).
    """

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"

    @staticmethod
    def from_str_lenient(s: Optional[str]) -> "Action":
        """Parse an action from a free-form string (case-insensitive, whitespace
        tolerant). Anything that is not BUY or SELL maps to ``HOLD`` so that an
        unrecognized/empty action conservatively abstains rather than trades.

        Mirrors ``Action::from_str_lenient`` in the Rust module.
        """
        if s is None:
            return Action.HOLD
        normalized = s.strip().upper()
        if normalized == "BUY":
            return Action.BUY
        if normalized == "SELL":
            return Action.SELL
        return Action.HOLD


@dataclass(frozen=True)
class ExecutionLevels:
    """The execution levels for a proposed/declared trade.

    A complete set of all three prices is required for a BUY/SELL declaration
    (R6.1). The presence of the object itself indicates the levels were
    supplied; finiteness is validated inside :func:`validate_trade`.
    """

    entry: float
    stop_loss: float
    take_profit: float


class ValidatorReason(Enum):
    """Why a Trade_Validator check failed. Each variant maps to a requirement."""

    # One or more of entry/stop-loss/take-profit was missing or non-finite (R6.1).
    MISSING_LEVELS = "missing-levels"
    # ``reward / risk`` is below the 1:2 minimum (R6.2).
    RISK_REWARD_TOO_LOW = "risk-reward-too-low"
    # Stop-loss distance from entry is smaller than ``1.5 × ATR`` (R6.3).
    STOP_TOO_TIGHT = "stop-too-tight"
    # Level ordering is inconsistent with the trade direction (R6.4 / R6.5).
    DIRECTION_INCONSISTENT = "direction-inconsistent"
    # A Management_Plan leg fraction is not in (0.0, 1.0], or the leg fractions
    # sum to more than 1.0 (R5.1).
    LEG_FRACTION_OUT_OF_RANGE = "leg-fraction-out-of-range"
    # A Management_Plan's scale-out targets are not strictly on the profit side
    # of entry and in monotone order for the trade direction (R5.2).
    TARGET_ORDERING_INCONSISTENT = "target-ordering-inconsistent"
    # A Management_Plan's breakeven trigger is not strictly between the entry and
    # the first scale-out target on the trade's profit side (R5.3).
    BREAKEVEN_OUT_OF_RANGE = "breakeven-out-of-range"
    # A Management_Plan's fraction-weighted (blended) reward-to-risk is below the
    # configured minimum (R5.4).
    BLENDED_RR_TOO_LOW = "blended-rr-too-low"

    @property
    def tag(self) -> str:
        """A stable machine-readable tag, suitable for surfacing to the agent /
        serializing in a tool result. Matches ``ValidatorReason::as_tag`` in Rust.
        """
        return self.value

    @property
    def message(self) -> str:
        """A human-readable description. Matches the Rust ``Display`` impl."""
        return {
            ValidatorReason.MISSING_LEVELS: (
                "missing execution levels: entry, stop-loss, and take-profit are all required"
            ),
            ValidatorReason.RISK_REWARD_TOO_LOW: "risk-reward ratio below the 1:2 minimum",
            ValidatorReason.STOP_TOO_TIGHT: "stop-loss is tighter than 1.5x ATR",
            ValidatorReason.DIRECTION_INCONSISTENT: (
                "execution levels are inconsistent with the trade direction"
            ),
            ValidatorReason.LEG_FRACTION_OUT_OF_RANGE: (
                "a scale-out leg fraction is not in (0.0, 1.0] or the fractions sum to more than 1.0"
            ),
            ValidatorReason.TARGET_ORDERING_INCONSISTENT: (
                "scale-out targets are inconsistent with the trade direction or not in monotone order"
            ),
            ValidatorReason.BREAKEVEN_OUT_OF_RANGE: (
                "breakeven trigger is not strictly between entry and the first scale-out target"
            ),
            ValidatorReason.BLENDED_RR_TOO_LOW: (
                "blended (fraction-weighted) reward-to-risk is below the configured minimum"
            ),
        }[self]


@dataclass(frozen=True)
class ValidatorOutcome:
    """The outcome of validating a declared trade.

    Mirrors the Rust enum ``ValidatorOutcome`` with two shapes:

    * ``Pass`` — all applicable checks passed; carries the computed
      Risk_Reward_Ratio. For a HOLD this is ``0.0`` (no levels to evaluate).
    * ``Fail`` — a check failed; the trade must not be committed (R6.6); carries
      the failing :class:`ValidatorReason`.
    """

    # Exactly one of these is set, matching the Rust enum variants.
    risk_reward: Optional[float] = None
    reason: Optional[ValidatorReason] = None

    @staticmethod
    def passed(risk_reward: float) -> "ValidatorOutcome":
        return ValidatorOutcome(risk_reward=risk_reward, reason=None)

    @staticmethod
    def failed(reason: ValidatorReason) -> "ValidatorOutcome":
        return ValidatorOutcome(risk_reward=None, reason=reason)

    def is_pass(self) -> bool:
        """Convenience: did the trade pass all checks?"""
        return self.reason is None


def _is_finite(x: float) -> bool:
    """True when ``x`` is a finite number (mirrors Rust ``f64::is_finite``)."""
    try:
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


def _field(obj, name):
    """Read ``name`` from ``obj`` whether it is a dataclass (``ManagementPlan`` /
    ``ScaleOutLeg`` / ``BreakevenTrigger`` from ``trade_manager.py``) or a plain
    ``dict``.

    Returns ``None`` when ``obj`` is ``None`` or the field is absent. This lets
    :func:`validate_trade` accept either the structured plan objects or a plain
    JSON-shaped ``dict`` without coupling to a specific type, and keeps the
    function pure and non-raising.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _validate_management_plan(
    action: Action,
    plan,
    entry: float,
    initial_stop: float,
    min_blended_reward_to_risk: Optional[float],
) -> ValidatorOutcome:
    """Run the multi-leg consistency checks for a BUY/SELL Management_Plan (R5).

    Invoked by :func:`validate_trade` ONLY after the existing hard rules on the
    base bracket have already passed (direction ordering and the ``stop ≥ 1.5×ATR``
    floor). These checks ADD to — and never relax — the base rules (R14.2). The
    checks run in this order, each mapping to an acceptance criterion:

    1. **LEG_FRACTION_OUT_OF_RANGE (R5.1)** — every leg fraction must lie in
       ``(0.0, 1.0]`` and the fractions must sum to at most ``1.0``.
    2. **TARGET_ORDERING_INCONSISTENT (R5.2)** — for a BUY ``initial_stop < entry``
       with every target strictly greater than entry and in non-decreasing order;
       the mirror image (``initial_stop > entry``, targets strictly below entry in
       non-increasing order) for a SELL.
    3. **BREAKEVEN_OUT_OF_RANGE (R5.3)** — when present, the breakeven trigger must
       fall strictly between the entry and the first target on the profit side.
       A trigger expressed as a price is range-checked directly; a trigger
       expressed as an ``r_multiple`` is checked in R-space — the entry sits at
       ``0R`` and the first target at ``first_target_r = |first_target - entry| /
       stop_distance``, so the trigger is accepted iff ``0 < r_multiple <
       first_target_r`` (equivalent to converting it to the implied price
       ``entry ± r_multiple × stop_distance`` and range-checking that).
    4. **BLENDED_RR_TOO_LOW (R5.4)** — the fraction-weighted target distance from
       entry over the initial stop distance must meet the configured minimum;
       this blended reward-to-risk REPLACES the single-target reward-to-risk check
       when a plan is present.

    Pure and non-raising. ``min_blended_reward_to_risk`` is resolved from the
    Trade_Manager configuration when ``None`` is supplied.
    """
    is_buy = action == Action.BUY
    legs = _field(plan, "legs") or ()

    # A plan with no legs cannot define any reward target / ordering.
    if len(legs) == 0:
        return ValidatorOutcome.failed(ValidatorReason.TARGET_ORDERING_INCONSISTENT)

    # R5.1 — every leg fraction in (0.0, 1.0] and the sum at most 1.0.
    fraction_sum = 0.0
    for leg in legs:
        fraction = _field(leg, "fraction")
        if (
            fraction is None
            or not _is_finite(fraction)
            or fraction <= 0.0
            or fraction > 1.0
        ):
            return ValidatorOutcome.failed(ValidatorReason.LEG_FRACTION_OUT_OF_RANGE)
        fraction_sum += fraction
    if fraction_sum > 1.0 + LEG_FRACTION_SUM_TOLERANCE:
        return ValidatorOutcome.failed(ValidatorReason.LEG_FRACTION_OUT_OF_RANGE)

    targets = [_field(leg, "target") for leg in legs]

    # R5.2 — targets strictly on the profit side of entry, in monotone order,
    # with the initial stop on the loss side. Comparisons against a non-finite
    # value (None/NaN) are always False, so a malformed target is rejected here.
    if not (_is_finite(entry) and _is_finite(initial_stop)):
        return ValidatorOutcome.failed(ValidatorReason.TARGET_ORDERING_INCONSISTENT)

    ordering_ok = initial_stop < entry if is_buy else initial_stop > entry
    previous = entry
    for target in targets:
        if target is None or not _is_finite(target):
            ordering_ok = False
            break
        if is_buy:
            if not (target > entry and target >= previous):
                ordering_ok = False
                break
        else:
            if not (target < entry and target <= previous):
                ordering_ok = False
                break
        previous = target
    if not ordering_ok:
        return ValidatorOutcome.failed(ValidatorReason.TARGET_ORDERING_INCONSISTENT)

    first_target = targets[0]
    # Ordering guarantees initial_stop is strictly on the opposite side of entry,
    # so the stop distance is strictly positive (no divide-by-zero below).
    stop_distance = abs(entry - initial_stop)

    # R5.3 — breakeven trigger strictly between entry and the first target.
    breakeven = _field(plan, "breakeven")
    if breakeven is not None:
        be_price = _field(breakeven, "price")
        be_r = _field(breakeven, "r_multiple")
        if be_price is not None:
            if is_buy:
                placement_ok = _is_finite(be_price) and entry < be_price < first_target
            else:
                placement_ok = _is_finite(be_price) and first_target < be_price < entry
            if not placement_ok:
                return ValidatorOutcome.failed(ValidatorReason.BREAKEVEN_OUT_OF_RANGE)
        elif be_r is not None:
            # Check in R-space: entry == 0R, first target == first_target_r.
            first_target_r = abs(first_target - entry) / stop_distance
            if not (_is_finite(be_r) and 0.0 < be_r < first_target_r):
                return ValidatorOutcome.failed(ValidatorReason.BREAKEVEN_OUT_OF_RANGE)
        # Both fields None -> "no breakeven move" -> nothing to check.

    # R5.4 — blended (fraction-weighted) reward-to-risk replaces the single-target
    # reward-to-risk check. blended = sum(fraction_i * |target_i - entry|) / stop.
    blended_reward = 0.0
    for leg, target in zip(legs, targets):
        fraction = _field(leg, "fraction")
        blended_reward += fraction * (abs(target - entry) / stop_distance)

    if min_blended_reward_to_risk is None:
        # Resolved deterministically from the Trade_Manager configuration; imported
        # lazily to avoid any import-time coupling and only when needed.
        import trade_manager

        min_blended_reward_to_risk = (
            trade_manager.resolve_trade_manager_config().min_blended_reward_to_risk
        )

    if blended_reward < min_blended_reward_to_risk:
        return ValidatorOutcome.failed(ValidatorReason.BLENDED_RR_TOO_LOW)

    return ValidatorOutcome.passed(blended_reward)


def validate_trade(
    action: Action,
    levels: Optional[ExecutionLevels],
    atr_14: Optional[float],
    plan=None,
    min_blended_reward_to_risk: Optional[float] = None,
) -> ValidatorOutcome:
    """Validate a proposed/declared trade against the hard risk rules (R6.1–R6.5),
    plus the multi-leg Management_Plan checks (R5) when a ``plan`` is supplied.

    ``HOLD`` bypasses all level AND plan checks and always passes with a
    ``risk_reward`` of ``0.0`` (R5.5). For ``BUY``/``SELL`` the base checks are
    applied in this exact order (matching the Rust implementation):

    1. **MissingLevels (R6.1)** — ``levels`` must be present and every price finite.
    2. **DirectionInconsistent (R6.4/R6.5)** — BUY requires
       ``stop_loss < entry < take_profit``; SELL requires
       ``take_profit < entry < stop_loss``.
    3. **StopTooTight (R6.3)** — when ``atr_14`` is available and finite, the stop
       distance ``|entry - stop_loss|`` must be at least ``1.5 × atr_14``.

    Then, depending on whether a Management_Plan is present:

    * **No plan (backward-compatible)** — the single-target **RiskRewardTooLow
      (R6.2)** check applies: ``|take_profit - entry| / |entry - stop_loss|`` must
      be at least ``2.0``. Behavior is byte-for-byte identical to before this
      feature when ``plan`` is ``None``.
    * **Plan present** — the multi-leg checks run on top of the base rules
      (leg fractions, target ordering, breakeven placement) and the
      fraction-weighted **blended** reward-to-risk REPLACES the single-target
      reward-to-risk check (R5.1–R5.4). See :func:`_validate_management_plan`.

    The plan may be a ``trade_manager.ManagementPlan`` or a plain ``dict`` with the
    same field names; its ``entry`` / ``initial_stop`` / ``legs`` drive the plan
    checks. ``min_blended_reward_to_risk`` defaults to the resolved Trade_Manager
    configuration minimum when ``None``.

    The function is pure: identical inputs (and environment, when the blended
    minimum is resolved from configuration) always yield an identical outcome.
    """
    # HOLD abstains — no execution levels OR plan checks to run (R6 / R5.5).
    if action == Action.HOLD:
        return ValidatorOutcome.passed(0.0)

    # R6.1 — all three levels must be present and finite.
    if (
        levels is None
        or not _is_finite(levels.entry)
        or not _is_finite(levels.stop_loss)
        or not _is_finite(levels.take_profit)
    ):
        return ValidatorOutcome.failed(ValidatorReason.MISSING_LEVELS)

    entry = levels.entry
    stop_loss = levels.stop_loss
    take_profit = levels.take_profit

    # R6.4 / R6.5 — level ordering must match the trade direction.
    if action == Action.BUY:
        direction_ok = stop_loss < entry < take_profit
    else:  # Action.SELL
        direction_ok = take_profit < entry < stop_loss
    if not direction_ok:
        return ValidatorOutcome.failed(ValidatorReason.DIRECTION_INCONSISTENT)

    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry)

    # Direction consistency guarantees a non-zero risk, but guard anyway so a
    # degenerate stop == entry can never divide by zero.
    if risk <= 0.0:
        return ValidatorOutcome.failed(ValidatorReason.DIRECTION_INCONSISTENT)

    # R6.3 — stop must not be tighter than 1.5x ATR (only when ATR is known).
    if atr_14 is not None and _is_finite(atr_14) and atr_14 > 0.0:
        if risk < MIN_STOP_ATR_MULTIPLE * atr_14:
            return ValidatorOutcome.failed(ValidatorReason.STOP_TOO_TIGHT)

    # When a Management_Plan is present, the multi-leg checks run AFTER the base
    # hard rules above and the blended reward-to-risk REPLACES the single-target
    # reward-to-risk check (R5). The plan's own entry / initial_stop drive the
    # multi-leg checks; fall back to the base bracket when the plan omits them.
    if plan is not None:
        plan_entry = _field(plan, "entry")
        if plan_entry is None:
            plan_entry = entry
        plan_initial_stop = _field(plan, "initial_stop")
        if plan_initial_stop is None:
            plan_initial_stop = stop_loss
        return _validate_management_plan(
            action,
            plan,
            plan_entry,
            plan_initial_stop,
            min_blended_reward_to_risk,
        )

    # R6.2 — single-target risk-reward must meet the 1:2 minimum (boundary passes).
    risk_reward = reward / risk
    if risk_reward < MIN_RISK_REWARD:
        return ValidatorOutcome.failed(ValidatorReason.RISK_REWARD_TOO_LOW)

    return ValidatorOutcome.passed(risk_reward)
