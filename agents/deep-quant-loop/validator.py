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


def validate_trade(
    action: Action,
    levels: Optional[ExecutionLevels],
    atr_14: Optional[float],
) -> ValidatorOutcome:
    """Validate a proposed/declared trade against the hard risk rules (R6.1–R6.5).

    ``HOLD`` bypasses all level checks and always passes with a ``risk_reward``
    of ``0.0``. For ``BUY``/``SELL`` the checks are applied in this exact order
    (matching the Rust implementation):

    1. **MissingLevels (R6.1)** — ``levels`` must be present and every price finite.
    2. **DirectionInconsistent (R6.4/R6.5)** — BUY requires
       ``stop_loss < entry < take_profit``; SELL requires
       ``take_profit < entry < stop_loss``.
    3. **StopTooTight (R6.3)** — when ``atr_14`` is available and finite, the stop
       distance ``|entry - stop_loss|`` must be at least ``1.5 × atr_14``.
    4. **RiskRewardTooLow (R6.2)** — ``|take_profit - entry| / |entry - stop_loss|``
       must be at least ``2.0``.

    The function is pure: identical inputs always yield an identical outcome.
    """
    # HOLD abstains — no execution levels to check (R6).
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

    # R6.2 — risk-reward must meet the 1:2 minimum (boundary passes).
    risk_reward = reward / risk
    if risk_reward < MIN_RISK_REWARD:
        return ValidatorOutcome.failed(ValidatorReason.RISK_REWARD_TOO_LOW)

    return ValidatorOutcome.passed(risk_reward)
