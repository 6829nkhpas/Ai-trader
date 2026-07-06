"""Trade_Manager — pure-math multi-leg exit simulation for Deep Quant.

The Deep Quant agent ("Alpha-Quant") declares directional trades as a single
entry / initial-stop / take-profit bracket. A veteran trader rarely exits in one
shot: they scale out at staged targets, move the stop to breakeven once the
trade has paid for itself, and trail the remainder. This module models that
*exit* discipline as pure math — a Management_Plan (scale-out legs, a breakeven
trigger, a trailing rule) simulated chronologically against an OHLCV candle
window to produce a fraction-weighted Realized_R.

Scope discipline: the Trade_Manager manages EXITS, not entries. It is the single
source of truth for the exit-simulation math (the journal-scoring path and the
backtest path both call back into it, feeding only different candle windows); it
emits only a SimulationResult (an Exit_Breakdown + Realized_R, or an
open / invalid marker), never a BUY/SELL/HOLD decision, never relaxes the hard
risk rules, and never places a live broker order.

Purity: this module is pure Python. It performs zero network calls (no
``httpx``), reads zero data sources other than its provided inputs, and touches
no file/clock. Parameter *resolution* (``resolve_trade_manager_config``) is the
only place the process environment is read, and it does so once up front,
deterministically, with documented defaults.

This file (task 1.1) provides the parameter-resolution foundation: the
documented default constants, the frozen ``TradeManagerConfig`` dataclass, and
``resolve_trade_manager_config()``. The plan / result data model, the
``simulate_plan`` simulator, the management-style tag, and the rest are added in
subsequent tasks. It reuses ``regime._resolve_float`` (the parse-with-default-
and-range helper) so the resolution semantics match the preceding context
features (regime / relative-strength / order-flow / forecaster) exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import regime

# ── Documented default parameters ─────────────────────────────────────────────
# Applied whenever a parameter env var is unset / empty / unparseable / out of
# range (Requirements 13.2-13.4). These are the single source of truth for the
# defaults on the live tool path, the journal-scoring path, AND the backtest /
# comparison path (Requirement 13.5).

DEFAULT_FIRST_TARGET_R = 1.0          # first scale-out target, in R multiples of the initial risk
DEFAULT_FIRST_TARGET_FRACTION = 0.5   # fraction of the position closed at the first target
DEFAULT_BREAKEVEN_TRIGGER_R = 1.0     # R-multiple of progress that moves the stop to breakeven
DEFAULT_TRAIL_ATR_MULTIPLE = 1.5      # trailing-stop distance, in ATR(14) multiples
DEFAULT_MIN_BLENDED_REWARD_TO_RISK = 2.0  # minimum fraction-weighted reward-to-risk a plan must clear

# ── Environment variable names ────────────────────────────────────────────────
ENV_DEFAULT_FIRST_TARGET_R = "TM_DEFAULT_FIRST_TARGET_R"
ENV_DEFAULT_FIRST_TARGET_FRACTION = "TM_DEFAULT_FIRST_TARGET_FRACTION"
ENV_DEFAULT_BREAKEVEN_TRIGGER_R = "TM_DEFAULT_BREAKEVEN_TRIGGER_R"
ENV_DEFAULT_TRAIL_ATR_MULTIPLE = "TM_DEFAULT_TRAIL_ATR_MULTIPLE"
ENV_MIN_BLENDED_REWARD_TO_RISK = "TM_MIN_BLENDED_REWARD_TO_RISK"

# ── Valid ranges ──────────────────────────────────────────────────────────────
# (per the design's range table). ``regime._resolve_float`` enforces an
# INCLUSIVE ``[low, high]`` band, so the parameters with an EXCLUSIVE lower bound
# (``(0, ...]``) are resolved with an inclusive low of ``0.0`` and then have the
# boundary value ``0.0`` reverted to their default below — a value can only land
# on ``0.0`` at that boundary because the helper already rejects negatives.
_FIRST_TARGET_R_LOW = 0.0     # exclusive lower bound (0, 100]
_FIRST_TARGET_R_HIGH = 100.0
_FIRST_TARGET_FRACTION_LOW = 0.0   # exclusive lower bound (0, 1]
_FIRST_TARGET_FRACTION_HIGH = 1.0
_BREAKEVEN_TRIGGER_R_LOW = 0.0     # exclusive lower bound (0, 100]
_BREAKEVEN_TRIGGER_R_HIGH = 100.0
_TRAIL_ATR_MULTIPLE_LOW = 0.0      # inclusive lower bound [0, 100]
_TRAIL_ATR_MULTIPLE_HIGH = 100.0
_MIN_BLENDED_REWARD_TO_RISK_LOW = 0.0   # inclusive lower bound [0, 100]
_MIN_BLENDED_REWARD_TO_RISK_HIGH = 100.0


@dataclass(frozen=True)
class TradeManagerConfig:
    """The resolved, validated parameter set used to build and score plans.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the Trade_Manager's purity guarantee). For identical
    environment-variable values the resolved configuration is identical on the
    tool path, the journal-scoring path, and the backtest / comparison path
    (Requirement 13.5).
    """

    default_first_target_r: float
    default_first_target_fraction: float
    default_breakeven_trigger_r: float
    default_trail_atr_multiple: float
    min_blended_reward_to_risk: float


def resolve_trade_manager_config() -> TradeManagerConfig:
    """Resolve every parameter from its own env var with documented defaults.

    Per-parameter rules (Requirement 13):
      * unset / empty            -> documented default (R13.2)
      * unparseable as a float   -> documented default (never raises) (R13.3)
      * parses but out of range  -> documented default (never raises) (R13.4)

    Each parameter is read from its own independent environment variable, reusing
    the ``regime._resolve_float`` parse-with-default-and-range helper so the
    resolution semantics match the preceding context features exactly. The same
    function is called on the tool path, the journal-scoring path, and the
    backtest / comparison path so the resolved values are identical for identical
    environment (Requirement 13.5). This function NEVER raises (R13.1).
    """
    # Targets and the breakeven trigger have an EXCLUSIVE lower bound of 0: a
    # zero R-multiple / zero fraction is not a usable parameter. ``_resolve_float``
    # only enforces an inclusive band, so resolve with an inclusive low of 0.0
    # and revert the boundary value 0.0 (the only way a non-negative value can be
    # at the exclusive bound) to the documented default per Requirement 13.4.
    first_target_r = regime._resolve_float(
        ENV_DEFAULT_FIRST_TARGET_R,
        DEFAULT_FIRST_TARGET_R,
        _FIRST_TARGET_R_LOW,
        _FIRST_TARGET_R_HIGH,
    )
    if first_target_r <= _FIRST_TARGET_R_LOW:
        first_target_r = DEFAULT_FIRST_TARGET_R

    first_target_fraction = regime._resolve_float(
        ENV_DEFAULT_FIRST_TARGET_FRACTION,
        DEFAULT_FIRST_TARGET_FRACTION,
        _FIRST_TARGET_FRACTION_LOW,
        _FIRST_TARGET_FRACTION_HIGH,
    )
    if first_target_fraction <= _FIRST_TARGET_FRACTION_LOW:
        first_target_fraction = DEFAULT_FIRST_TARGET_FRACTION

    breakeven_trigger_r = regime._resolve_float(
        ENV_DEFAULT_BREAKEVEN_TRIGGER_R,
        DEFAULT_BREAKEVEN_TRIGGER_R,
        _BREAKEVEN_TRIGGER_R_LOW,
        _BREAKEVEN_TRIGGER_R_HIGH,
    )
    if breakeven_trigger_r <= _BREAKEVEN_TRIGGER_R_LOW:
        breakeven_trigger_r = DEFAULT_BREAKEVEN_TRIGGER_R

    # The trail multiple and the minimum blended reward-to-risk allow an
    # INCLUSIVE lower bound of 0 (a flat / disabled trail, a zero minimum), so
    # the helper's inclusive band is sufficient.
    trail_atr_multiple = regime._resolve_float(
        ENV_DEFAULT_TRAIL_ATR_MULTIPLE,
        DEFAULT_TRAIL_ATR_MULTIPLE,
        _TRAIL_ATR_MULTIPLE_LOW,
        _TRAIL_ATR_MULTIPLE_HIGH,
    )

    min_blended_reward_to_risk = regime._resolve_float(
        ENV_MIN_BLENDED_REWARD_TO_RISK,
        DEFAULT_MIN_BLENDED_REWARD_TO_RISK,
        _MIN_BLENDED_REWARD_TO_RISK_LOW,
        _MIN_BLENDED_REWARD_TO_RISK_HIGH,
    )

    return TradeManagerConfig(
        default_first_target_r=first_target_r,
        default_first_target_fraction=first_target_fraction,
        default_breakeven_trigger_r=breakeven_trigger_r,
        default_trail_atr_multiple=trail_atr_multiple,
        min_blended_reward_to_risk=min_blended_reward_to_risk,
    )


# ── Management plan data model ────────────────────────────────────────────────
# The Management_Plan is the structured, multi-leg exit plan a directional trade
# carries (Requirement 1). Every dataclass below is FROZEN so a constructed plan
# (and any simulated result) is immutable — this supports the Trade_Manager's
# purity guarantee (a plan handed to ``simulate_plan`` can never be mutated by
# the simulator or any downstream consumer) and makes plans safely shareable
# between the live-tool, journal-scoring, and backtest paths.


@dataclass(frozen=True)
class ScaleOutLeg:
    """One partial-exit target: a price and the size fraction closed there.

    The ``fraction`` is the portion of the *whole* position closed when price
    reaches ``target`` (Requirement 1.2). Valid fractions lie in ``(0.0, 1.0]``
    and the fractions of all legs in a plan must sum to at most ``1.0``
    (Requirement 1.5) — those bounds are enforced by the Trade_Validator, not
    here, so this constructor stays pure and non-raising and can also hold a
    degenerate / not-yet-validated plan for inspection.
    """

    target: float       # target price for this partial exit
    fraction: float     # size fraction in (0.0, 1.0]


@dataclass(frozen=True)
class BreakevenTrigger:
    """The point at which the active stop is advanced to the entry price.

    Expressed as EITHER an absolute ``price`` OR an ``r_multiple`` of progress
    from entry toward the first target (Requirement 1.4). Exactly one field is
    normally populated; leaving both ``None`` represents "no breakeven move".
    """

    price: Optional[float] = None
    r_multiple: Optional[float] = None


@dataclass(frozen=True)
class TrailingStop:
    """An optional rule that advances the stop behind price after breakeven.

    Expressed as EITHER an ``atr_multiple`` (trail by N x ATR(14)) OR an
    ``r_increment`` (trail by a fixed R increment). Leaving both ``None``
    represents "no trailing".
    """

    atr_multiple: Optional[float] = None   # trail by N x ATR(14)
    r_increment: Optional[float] = None    # or by a fixed R increment


@dataclass(frozen=True)
class ManagementPlan:
    """The full multi-leg exit plan for a directional trade (Requirement 1.1).

    Comprises the entry, the initial stop-loss, an ordered tuple of one or more
    ``ScaleOutLeg`` (Requirement 1.1), an optional ``BreakevenTrigger``, and an
    optional ``TrailingStop``. ``atr_14`` carries the volatility reference for
    ATR-based trailing and the stop-distance check. A Single_Target_Trade is the
    degenerate plan ``legs=(ScaleOutLeg(target=take_profit, fraction=1.0),)`` with
    no breakeven and no trailing (Requirement 1.3); any residual fraction
    (``1.0 - sum(leg fractions)``) is the size carried to the final stop / exit.
    """

    action: str                          # "BUY" | "SELL"
    entry: float
    initial_stop: float
    legs: Tuple[ScaleOutLeg, ...]        # one or more, ordered (R1.1)
    breakeven: Optional[BreakevenTrigger] = None
    trailing: Optional[TrailingStop] = None
    atr_14: Optional[float] = None       # for ATR-based trailing / stop check


# ── Simulation result data model (Exit_Breakdown + Realized_R) ────────────────


@dataclass(frozen=True)
class LegFill:
    """One recorded exit in the Exit_Breakdown.

    A ``LegFill`` is produced either when a scale-out leg fills at its target or
    when the residual size is closed at the active stop / exit. ``leg_r`` is this
    fill's R-multiple measured against the INITIAL stop distance (Requirement
    2.7). ``kind`` records HOW the size left the book.
    """

    index: int                       # leg index in the plan (or -1 for the residual exit)
    price: float                     # fill price (the leg target, or the stop for residual)
    fraction: float                  # size closed at this fill
    leg_r: float                     # this fill's R vs the INITIAL stop distance
    timestamp_ms: Optional[int]
    kind: str                        # "target" | "stop" | "breakeven-stop" | "trail-stop"


@dataclass(frozen=True)
class SimulationResult:
    """The outcome of simulating a Management_Plan (Exit_Breakdown + Realized_R).

    ``status`` is one of:
      * ``"resolved"`` — the plan fully closed; ``realized_r`` is populated and
        the filled fractions plus ``residual_fraction`` sum to exactly ``1.0``
        (the conservation invariant, Requirement 3.3).
      * ``"open"``     — no candle reached any target or the stop; ``realized_r``
        is ``None`` and no fills are fabricated (Requirement 3.1).
      * ``"invalid"``  — the initial stop distance is zero (R measurement would
        divide by zero); ``realized_r`` is ``None`` (Requirement 3.4).

    ``realized_r`` is the fraction-weighted sum ``sum(fraction * leg_r)`` over all
    fills (Requirement 2.7), ``None`` when open / invalid.
    """

    status: str                          # "resolved" | "open" | "invalid"
    realized_r: Optional[float]          # None when open / invalid
    fills: Tuple[LegFill, ...]           # the Exit_Breakdown
    residual_fraction: float             # size closed at the final stop / exit (0 when fully scaled out)
    breakeven_moved_at: Optional[int] = None
    trailed: bool = False


# ── Plan-construction helpers ─────────────────────────────────────────────────
# Pure, non-raising builders. They never read the environment, the clock, or the
# network; they only assemble immutable plan objects from their arguments. A
# malformed bracket (for example a zero stop distance) still produces a plan
# object — validation (Trade_Validator) and the ``invalid`` simulation status
# are where degenerate brackets are caught, so these constructors never raise.


def single_target_plan(entry, stop_loss, take_profit) -> ManagementPlan:
    """Build the degenerate one-leg plan that models today's single bracket.

    The current single-target behavior is a Management_Plan with exactly one
    ``ScaleOutLeg`` at fraction ``1.0``, no ``BreakevenTrigger``, and no
    ``TrailingStop`` (Requirements 1.3, 3.6). The action is inferred from the
    bracket geometry — a take-profit above the entry is a BUY, otherwise a SELL —
    so the degenerate plan scores identically to the legacy single-target scorer.
    Pure and non-raising.
    """
    action = "BUY" if take_profit >= entry else "SELL"
    return ManagementPlan(
        action=action,
        entry=entry,
        initial_stop=stop_loss,
        legs=(ScaleOutLeg(target=take_profit, fraction=1.0),),
        breakeven=None,
        trailing=None,
        atr_14=None,
    )


def default_management_plan(action, entry, stop_loss, atr_14, config) -> ManagementPlan:
    """Build the uniform managed-run plan from configured defaults (R12.5).

    Used by the backtest's managed / unmanaged comparison (and as the canonical
    "default management") to apply one consistent plan to every signal: a first
    scale-out target at the configured R taking the configured fraction, a
    breakeven move after the first target, and the configured ATR trail.

    The first target price is ``entry +/- (default_first_target_r *
    initial_stop_distance)`` — placed ABOVE entry for a BUY and BELOW entry for a
    SELL — where the initial stop distance is ``abs(entry - stop_loss)``. Pure and
    non-raising: a degenerate bracket (zero stop distance) simply yields a target
    equal to entry, which the validator / simulator handle downstream.
    """
    side = "SELL" if str(action).upper() == "SELL" else "BUY"
    stop_distance = abs(entry - stop_loss)
    if side == "BUY":
        first_target = entry + config.default_first_target_r * stop_distance
    else:
        first_target = entry - config.default_first_target_r * stop_distance

    return ManagementPlan(
        action=side,
        entry=entry,
        initial_stop=stop_loss,
        legs=(ScaleOutLeg(target=first_target, fraction=config.default_first_target_fraction),),
        breakeven=BreakevenTrigger(r_multiple=config.default_breakeven_trigger_r),
        trailing=TrailingStop(atr_multiple=config.default_trail_atr_multiple),
        atr_14=atr_14,
    )


# ── Plan (de)serialization for persistence ────────────────────────────────────
# The Trade_Journal persists a recorded trade's Management_Plan as serialized
# JSON (a nullable ``management_plan`` TEXT column) so the trade can be re-scored
# reproducibly on later candles (Requirement 6.3). These two helpers are the
# single round-trip boundary between an in-memory ``ManagementPlan`` and its
# stored text form. They are PURE and TOTAL: ``plan_to_json`` returns ``None`` for
# an absent / non-plan input, and ``plan_from_json`` returns ``None`` for any
# ``None`` / malformed / out-of-shape text rather than raising, so a corrupted or
# legacy NULL column degrades to "no plan" instead of crashing the scorer.
#
# Round-trip guarantee: ``plan_from_json(plan_to_json(plan))`` reconstructs an
# equal ``ManagementPlan`` — action, entry, initial_stop, every leg's
# target / fraction, the breakeven price / r_multiple, the trailing
# atr_multiple / r_increment, and atr_14 are all preserved.

import json


def plan_to_json(plan):
    """Serialize a ``ManagementPlan`` to a JSON string for persistence (R6.3).

    Returns ``None`` (the stored NULL sentinel) for an absent plan, so a
    Single_Target_Trade with no managed plan persists as a NULL column. Returns
    ``None`` as well for any object that is not a ``ManagementPlan``-shaped value
    rather than raising, keeping the persistence path total. The produced JSON is
    a plain nested dict of legs / breakeven / trailing that ``plan_from_json``
    reconstructs into an equal plan.
    """
    if plan is None:
        return None
    try:
        legs = [
            {"target": leg.target, "fraction": leg.fraction}
            for leg in plan.legs
        ]
        breakeven = None
        if plan.breakeven is not None:
            breakeven = {
                "price": plan.breakeven.price,
                "r_multiple": plan.breakeven.r_multiple,
            }
        trailing = None
        if plan.trailing is not None:
            trailing = {
                "atr_multiple": plan.trailing.atr_multiple,
                "r_increment": plan.trailing.r_increment,
            }
        payload = {
            "action": plan.action,
            "entry": plan.entry,
            "initial_stop": plan.initial_stop,
            "legs": legs,
            "breakeven": breakeven,
            "trailing": trailing,
            "atr_14": plan.atr_14,
        }
        return json.dumps(payload)
    except (AttributeError, TypeError, ValueError):
        # Not a plan-shaped object / not JSON-serializable -> persist nothing
        # rather than raising (keeps the journal write path total).
        return None


def plan_from_json(text) -> Optional[ManagementPlan]:
    """Reconstruct a ``ManagementPlan`` from persisted JSON text (R6.3).

    Tolerates ``None`` (a NULL column), an empty string, malformed JSON, and a
    JSON value that is not a plan-shaped object by returning ``None`` rather than
    raising, so a legacy / corrupted column degrades to "no plan". On a
    well-formed payload it reconstructs an equal plan: action, entry,
    initial_stop, every leg's target / fraction, the breakeven price / r_multiple,
    the trailing atr_multiple / r_increment, and atr_14.
    """
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        raw_legs = payload["legs"]
        if not isinstance(raw_legs, (list, tuple)):
            return None
        legs = tuple(
            ScaleOutLeg(target=leg["target"], fraction=leg["fraction"])
            for leg in raw_legs
        )

        breakeven = None
        raw_be = payload.get("breakeven")
        if isinstance(raw_be, dict):
            breakeven = BreakevenTrigger(
                price=raw_be.get("price"),
                r_multiple=raw_be.get("r_multiple"),
            )

        trailing = None
        raw_trail = payload.get("trailing")
        if isinstance(raw_trail, dict):
            trailing = TrailingStop(
                atr_multiple=raw_trail.get("atr_multiple"),
                r_increment=raw_trail.get("r_increment"),
            )

        return ManagementPlan(
            action=payload["action"],
            entry=payload["entry"],
            initial_stop=payload["initial_stop"],
            legs=legs,
            breakeven=breakeven,
            trailing=trailing,
            atr_14=payload.get("atr_14"),
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        # Missing required field / wrong shape -> "no plan" rather than raising.
        return None


# ── Management-style tag ──────────────────────────────────────────────────────
# The journal fingerprint (``journal.derive_setup_tags``) appends exactly one
# management-style dimension so per-management-style win-rate / expectancy is
# groupable (Requirement 11). To keep that dimension low-cardinality and the
# resulting ``setup_key`` deterministic, the tag is drawn from a FIXED enumeration
# of at most 8 values (Requirement 11.2):
#
#     single | scale | scale-be | scale-trail | scale-be-trail | be | trail | unknown
#
# Prefix decision: ``management_style_tag`` returns the BARE enumeration value
# (e.g. ``"single"``, NOT ``"tm:single"``). The ``tm:`` namespace prefix is owned
# by the consumer — ``journal.derive_setup_tags`` appends ``f"tm:{value}"`` at the
# fixed tag position. Returning the bare value keeps this helper agnostic of the
# tag-namespacing convention, matches how the preceding context features expose
# their tag values, and avoids a doubled ``tm:tm:`` prefix at the call site.

# The fixed enumeration, exposed for the journal / tests to assert against.
MANAGEMENT_STYLE_TAGS = (
    "single",
    "scale",
    "scale-be",
    "scale-trail",
    "scale-be-trail",
    "be",
    "trail",
    "unknown",
)


def _has_breakeven(plan) -> bool:
    """True when the plan carries a breakeven move (a populated trigger)."""
    be = getattr(plan, "breakeven", None)
    if be is None:
        return False
    return be.price is not None or be.r_multiple is not None


def _has_trailing(plan) -> bool:
    """True when the plan carries a trailing rule (a populated trail)."""
    trail = getattr(plan, "trailing", None)
    if trail is None:
        return False
    return trail.atr_multiple is not None or trail.r_increment is not None


def _is_scaling(plan) -> bool:
    """True when the plan genuinely scales out.

    Real scaling means more than one leg OR a single leg that closes less than
    the whole position (a fraction < 1.0, leaving a residual carried to the final
    stop / exit). A lone leg at fraction 1.0 is a single-target trade, not a
    scale-out.
    """
    legs = getattr(plan, "legs", None) or ()
    if len(legs) > 1:
        return True
    return any(leg.fraction < 1.0 for leg in legs)


def management_style_tag(plan: Optional[ManagementPlan]) -> str:
    """Collapse a plan into exactly one fixed ``tm:`` enumeration value (R11.2).

    Returns the BARE value (no ``tm:`` prefix — the journal adds that). Total and
    pure; never raises. Mapping (per the design tag table):

      * absent plan (``None``)                          -> ``"unknown"``
      * single leg at fraction 1.0, no be, no trail     -> ``"single"``
      * scaling (>1 leg or a leg fraction < 1.0):
            none / be / trail / both                    -> ``"scale"`` /
                                                           ``"scale-be"`` /
                                                           ``"scale-trail"`` /
                                                           ``"scale-be-trail"``
      * single target + breakeven only                  -> ``"be"``
      * single target + trailing only                   -> ``"trail"``
      * single target + breakeven AND trailing          -> ``"trail"``
            (no dedicated single-target ``be-trail`` value exists in the fixed
            8-value enumeration; trailing is the dominant active-management
            style — a trail subsumes the breakeven move that precedes it — so a
            single-target plan carrying both collapses to ``"trail"``.)
    """
    if plan is None:
        return "unknown"

    try:
        has_be = _has_breakeven(plan)
        has_trail = _has_trailing(plan)

        if _is_scaling(plan):
            if has_be and has_trail:
                return "scale-be-trail"
            if has_be:
                return "scale-be"
            if has_trail:
                return "scale-trail"
            return "scale"

        # Single-target (one leg at fraction 1.0).
        if has_trail:
            # Covers trailing-only and the be+trail combination (trail dominates).
            return "trail"
        if has_be:
            return "be"
        return "single"
    except (AttributeError, TypeError):
        # Defensive: a malformed plan-shaped object collapses to unknown rather
        # than raising, keeping the journal fingerprint path total.
        return "unknown"


# ── Core multi-leg simulator ──────────────────────────────────────────────────
# ``simulate_plan`` is the SINGLE SOURCE OF TRUTH for the exit-simulation math
# (the journal-scoring path and the backtest / comparison path both call back
# into it, feeding only different candle windows). It is a pure, deterministic
# function of the Management_Plan, the candle sequence, and the resolved config
# (Requirement 2.8): it reads no environment, no clock, and no data source other
# than its arguments (Requirements 14.3, 14.4); it never mutates its inputs; and
# it NEVER raises (degenerate inputs are reported as data — ``open`` / ``invalid``
# — not exceptions, Requirement 3). It emits ONLY a ``SimulationResult`` (an
# Exit_Breakdown + Realized_R, or an open/invalid marker) and never a
# BUY/SELL/HOLD decision (Requirement 14.1).

import math

# A tiny tolerance for treating the remaining position size as fully closed,
# absorbing floating-point residue from repeated fraction subtraction so a plan
# whose leg fractions sum to 1.0 resolves cleanly via its targets.
_SIZE_TOL = 1e-9

# Timestamp keys a candle may carry, in priority order. The live system emits
# ``timestamp_ms`` (epoch milliseconds, the key the journal and backtest scorers
# read); ``timestamp`` / ``time`` are accepted as fallbacks so the simulator is
# robust to either shape without ever depending on a clock.
_TIMESTAMP_KEYS = ("timestamp_ms", "timestamp", "time")


def _finite_num(value):
    """Return ``float(value)`` when it is a finite real number, else ``None``.

    Mirrors ``journal._is_num`` semantics: rejects ``bool`` (a Python ``int``
    subclass that is never a real OHLCV value), non-numeric types, and
    non-finite floats (``nan`` / ``inf``). Total and non-raising — the building
    block for excluding non-finite / non-numeric candles (Requirement 3.2).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _candle_timestamp(candle):
    """Extract a finite numeric timestamp from a candle dict, else ``None``.

    Tries ``timestamp_ms`` first (the live key), then ``timestamp`` / ``time``.
    A candle without any usable numeric timestamp cannot be placed in the
    ascending-time ordering the simulation requires, so it is excluded.
    """
    for key in _TIMESTAMP_KEYS:
        if key in candle:
            ts = _finite_num(candle[key])
            if ts is not None:
                return ts
    return None


def _clean_candles(candles):
    """Filter, normalize, and deterministically order the candle sequence.

    Returns a list of ``(ts, open, high, low, close)`` tuples:
      * non-dict entries are skipped;
      * a candle with a non-finite / non-numeric ``open`` / ``high`` / ``low`` /
        ``close`` (or ``volume`` when that key is present) is excluded
        (Requirement 3.2);
      * a candle without a usable numeric timestamp is excluded (it cannot be
        ordered);
      * the survivors are sorted by ``(ts, open, high, low, close)`` so the
        ordering is TOTAL and input-order-independent — simulating the same
        multiset of candles in any order yields an identical sequence, and hence
        an identical result (Requirement 2.1, the confluence property).

    Pure and non-raising; builds a new list and never mutates the input candles.
    """
    cleaned = []
    for c in candles:
        if not isinstance(c, dict):
            continue
        o = _finite_num(c.get("open"))
        h = _finite_num(c.get("high"))
        lo = _finite_num(c.get("low"))
        cl = _finite_num(c.get("close"))
        if o is None or h is None or lo is None or cl is None:
            continue
        if "volume" in c:
            # Volume is validated only when present (Requirement 3.2 names volume
            # among the OHLCV fields); an absent volume key is acceptable.
            if _finite_num(c.get("volume")) is None:
                continue
        ts = _candle_timestamp(c)
        if ts is None:
            continue
        cleaned.append((ts, o, h, lo, cl))
    cleaned.sort(key=lambda row: (row[0], row[1], row[2], row[3], row[4]))
    return cleaned


def _realized_r(fills) -> float:
    """Fraction-weighted sum of every fill's R, against the INITIAL stop (R2.7)."""
    return sum(f.fraction * f.leg_r for f in fills)


def simulate_plan(plan, candles, config) -> SimulationResult:
    """Pure, deterministic multi-leg exit simulation (Requirements 2, 3, 14).

    Processes the candles in ascending timestamp order, excluding any candle with
    a non-finite / non-numeric OHLCV field (Requirements 2.1, 3.2). On each
    candle, in this order:

      1. The active stop is resolved FIRST. If the candle's range reaches the
         active stop (BUY: ``low <= stop``; SELL: ``high >= stop``) the entire
         remaining size is closed at the stop price and the simulation ends
         (Requirement 2.3). Resolving the stop before any target means a single
         candle that straddles BOTH the stop and an unfilled target is a
         worst-case stop fill that never flatters the plan (Requirement 2.6).
      2. Otherwise, every unfilled leg whose target the candle reaches (BUY:
         ``high >= target``; SELL: ``low <= target``) is filled at its target
         price for its size fraction and recorded (Requirement 2.2). If that
         closes the whole position the plan resolves via its targets.
      3. The active stop is then advanced for SUBSEQUENT candles: to the entry
         price once the Breakeven_Trigger (a price, or an R-multiple of progress)
         is reached (Requirement 2.4), and behind price per the Trailing_Stop
         rule, never moving adversely — non-decreasing for a BUY, non-increasing
         for a SELL (Requirement 2.5).

    ``Realized_R`` is the fraction-weighted sum of each fill's R, every leg's R
    measured against the INITIAL stop distance (Requirement 2.7); the filled
    fractions plus the residual closed at the final stop/exit sum to ``1.0`` for
    a resolved plan (Requirement 3.3, conservation).

    Status:
      * ``"resolved"`` — the whole position closed (all legs filled, or the stop
        closed the residual).
      * ``"open"``     — no candle reached the stop and the position never fully
        scaled out; no exit is fabricated and ``realized_r`` is ``None``
        (Requirement 3.1).
      * ``"invalid"``  — the initial stop distance is zero (or a level is
        non-finite); ``realized_r`` is ``None`` and no division by zero occurs
        (Requirement 3.4).

    Never raises and never mutates ``plan`` / ``candles`` / ``config``
    (Requirements 2.8, 3.5); emits only a ``SimulationResult`` (Requirement
    14.1).
    """
    try:
        return _simulate_plan_impl(plan, candles, config)
    except Exception:
        # Defensive backstop: the contract is "failures are data, not
        # exceptions". Any unforeseen degeneracy collapses to an unresolved
        # (open) result rather than raising into the journal / backtest loop.
        return SimulationResult(
            status="open",
            realized_r=None,
            fills=(),
            residual_fraction=1.0,
            breakeven_moved_at=None,
            trailed=False,
        )


def _simulate_plan_impl(plan, candles, config) -> SimulationResult:
    action = str(getattr(plan, "action", "")).upper()
    is_sell = action == "SELL"
    is_buy = not is_sell

    entry = plan.entry
    initial_stop = plan.initial_stop

    # Levels must be finite for any R measurement; a zero (or non-finite) initial
    # stop distance is reported as ``invalid`` rather than dividing by zero
    # (Requirement 3.4).
    entry_f = _finite_num(entry)
    stop_f = _finite_num(initial_stop)
    if entry_f is None or stop_f is None:
        return SimulationResult(
            status="invalid", realized_r=None, fills=(), residual_fraction=0.0,
        )
    stop_distance = abs(entry_f - stop_f)
    if stop_distance == 0.0 or not math.isfinite(stop_distance):
        return SimulationResult(
            status="invalid", realized_r=None, fills=(), residual_fraction=0.0,
        )

    legs = tuple(plan.legs or ())

    def r_of(price: float) -> float:
        """Signed R of a fill price vs entry, over the INITIAL stop distance."""
        if is_buy:
            return (price - entry_f) / stop_distance
        return (entry_f - price) / stop_distance

    # ── Breakeven trigger level ───────────────────────────────────────────────
    breakeven = getattr(plan, "breakeven", None)
    be_level = None
    if breakeven is not None:
        be_price = _finite_num(breakeven.price)
        be_rmult = _finite_num(breakeven.r_multiple)
        if be_price is not None:
            be_level = be_price
        elif be_rmult is not None:
            # R-multiple of progress from entry toward the first target.
            be_level = entry_f + be_rmult * stop_distance if is_buy else entry_f - be_rmult * stop_distance
    has_breakeven = be_level is not None

    # ── Trailing rule ─────────────────────────────────────────────────────────
    trailing = getattr(plan, "trailing", None)
    atr_14 = _finite_num(getattr(plan, "atr_14", None))
    trail_atr_mult = _finite_num(getattr(trailing, "atr_multiple", None)) if trailing is not None else None
    trail_r_incr = _finite_num(getattr(trailing, "r_increment", None)) if trailing is not None else None

    def trail_distance():
        """The trailing offset for this plan, or ``None`` when no usable rule.

        ``atr_multiple`` trails by ``N x ATR(14)`` (needs a finite ``atr_14``);
        ``r_increment`` trails by a fixed R increment of the INITIAL stop
        distance. A negative offset is rejected (it would move the stop the wrong
        way). The ATR rule takes precedence when both are configured.
        """
        if trail_atr_mult is not None and atr_14 is not None:
            dist = trail_atr_mult * atr_14
        elif trail_r_incr is not None:
            dist = trail_r_incr * stop_distance
        else:
            return None
        if not math.isfinite(dist) or dist < 0.0:
            return None
        return dist

    has_trailing = trail_distance() is not None

    # ── Mutable simulation state ──────────────────────────────────────────────
    fills = []
    filled_indices = set()
    remaining = 1.0                  # fraction of the position still open
    active_stop = stop_f             # current stop price
    stop_origin = "stop"             # "stop" | "breakeven-stop" | "trail-stop"
    breakeven_moved = False
    breakeven_moved_at = None
    trailed = False

    def trailing_active() -> bool:
        # Trailing advances the stop behind price AFTER the breakeven trigger
        # (per the glossary). With no breakeven configured it is active from the
        # first candle; with a breakeven it engages once the stop has moved up.
        if not has_trailing:
            return False
        return breakeven_moved if has_breakeven else True

    cleaned = _clean_candles(candles)

    for (ts, _o, hi, lo, cl) in cleaned:
        ts_ms = int(ts)

        # 1. Stop FIRST (worst-case straddle resolution, Requirements 2.3, 2.6).
        stop_hit = (lo <= active_stop) if is_buy else (hi >= active_stop)
        if stop_hit:
            fills.append(
                LegFill(
                    index=-1,
                    price=active_stop,
                    fraction=remaining,
                    leg_r=r_of(active_stop),
                    timestamp_ms=ts_ms,
                    kind=stop_origin,
                )
            )
            residual = remaining
            remaining = 0.0
            return SimulationResult(
                status="resolved",
                realized_r=_realized_r(fills),
                fills=tuple(fills),
                residual_fraction=residual,
                breakeven_moved_at=breakeven_moved_at,
                trailed=trailed,
            )

        # 2. Target fills for any unfilled leg the candle reaches (Requirement 2.2).
        for i, leg in enumerate(legs):
            if i in filled_indices or remaining <= _SIZE_TOL:
                continue
            target = _finite_num(leg.target)
            if target is None:
                continue
            target_hit = (hi >= target) if is_buy else (lo <= target)
            if target_hit:
                frac = leg.fraction if leg.fraction <= remaining else remaining
                filled_indices.add(i)
                if frac <= 0.0:
                    continue
                fills.append(
                    LegFill(
                        index=i,
                        price=target,
                        fraction=frac,
                        leg_r=r_of(target),
                        timestamp_ms=ts_ms,
                        kind="target",
                    )
                )
                remaining -= frac

        # Fully scaled out via targets -> resolved with no residual (R3.3).
        if remaining <= _SIZE_TOL:
            return SimulationResult(
                status="resolved",
                realized_r=_realized_r(fills),
                fills=tuple(fills),
                residual_fraction=0.0,
                breakeven_moved_at=breakeven_moved_at,
                trailed=trailed,
            )

        # 3. Advance the stop for SUBSEQUENT candles (never adversely).
        # 3a. Breakeven -> move the stop to entry once the trigger is reached
        #     (Requirement 2.4). Applied from the next candle on.
        if has_breakeven and not breakeven_moved:
            be_reached = (hi >= be_level) if is_buy else (lo <= be_level)
            if be_reached:
                if is_buy and entry_f > active_stop:
                    active_stop = entry_f
                    stop_origin = "breakeven-stop"
                elif is_sell and entry_f < active_stop:
                    active_stop = entry_f
                    stop_origin = "breakeven-stop"
                breakeven_moved = True
                breakeven_moved_at = ts_ms

        # 3b. Trailing -> advance behind price, monotone & non-adverse (R2.5).
        if trailing_active():
            dist = trail_distance()
            if dist is not None:
                candidate = (cl - dist) if is_buy else (cl + dist)
                if math.isfinite(candidate):
                    if is_buy and candidate > active_stop:
                        active_stop = candidate
                        stop_origin = "trail-stop"
                        trailed = True
                    elif is_sell and candidate < active_stop:
                        active_stop = candidate
                        stop_origin = "trail-stop"
                        trailed = True

    # No candle reached the stop and the position never fully scaled out: the
    # plan is unresolved (open). No exit is fabricated; any genuine partial
    # target fills are retained, and ``realized_r`` is ``None`` (Requirement 3.1).
    return SimulationResult(
        status="open",
        realized_r=None,
        fills=tuple(fills),
        residual_fraction=remaining,
        breakeven_moved_at=breakeven_moved_at,
        trailed=trailed,
    )
