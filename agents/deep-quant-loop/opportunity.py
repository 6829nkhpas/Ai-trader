"""Adaptive_Opportunity_Engine — pure decision/loop-control core for Deep Quant.

The Deep Quant agent's "Self-Defending Hunter" policy is binary: either it finds
an A+ setup and commits via ``declare_trade``, or it registers a
``watch_price_condition`` and suspends. Because the Rust watcher auto-resumes on
target OR invalidation, and invalidation is price-only (noise trips it), an
invalidation wakes the agent, the A+-only prompt re-arms another watch, and the
run loops ``analyze -> watch -> invalidate -> re-watch`` with no cap. The
reasoning-turn safety net never fires (a watch is a tool call that resets the
counter), so the loop is structurally unbounded and the user gets nothing.

The Adaptive Opportunity Engine replaces that binary policy with how an
experienced trader actually works — take the best *available* opportunity at
appropriate size, bound the hunt, and learn from an invalidation instead of
blindly re-arming — across five structural axes: a tiered opportunity ladder
(``a_plus -> b_continuation -> scalp -> stand_aside``) with per-tier size
scaling, a hard Watch_Cap plus a Session_Budget, an invalidation post-mortem, an
opt-in Heartbeat_Monitor with cheap Delta_Recheck on resume, deterministic
session-context pruning, and an interim Best_Current_Read surfaced even when no
trade is taken.

This module holds all of that binding logic as a **pure, deterministic Python
core** over plain dicts/values — no I/O, no clock read inside the pure functions
(the clock is passed in as ``now``), no LLM — exactly like ``attribution.py`` /
``telemetry.py`` / ``debate.py``. It adds no new market-data source, places no
live orders, and never relaxes the Trade_Validator hard rules (stop >= 1.5x ATR,
min R:R) for any tier.

The module deliberately mirrors the conventions already established across the
deep-quant-loop: config-from-env with documented defaults via the
``_resolve_int`` / ``_resolve_float`` / ``_resolve_bool`` helper convention
(``attribution.py`` / ``telemetry.py`` / ``regime.py`` / ``rs.py``), a frozen
config dataclass, and a pure functional core.

This file (task 1.1) provides the configuration foundation: the documented
default constants, the environment-variable names, the frozen
``OpportunityConfig`` dataclass, and ``resolve_opportunity_config()``. The tier
ladder, bounded-hunt predicates, invalidation post-mortem, cheap-resume /
heartbeat accounting, context pruning, interim read, and tier tag are added in
subsequent tasks.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

# ── Documented default configuration ──────────────────────────────────────────
# Applied whenever a parameter's env var is unset / empty / whitespace /
# unparseable / out of range (Requirement 11.2). Single source of truth for the
# defaults across the resolver, the graph loop control, and the boundary
# integrations. The lower-tier and heartbeat enable flags default to the
# pre-engine A+-only, heartbeat-off policy (Requirement 11.3): setting
# ``lower_tiers_enabled=False`` and ``heartbeat_enabled=False`` restores the
# original A+-only, cap-bounded hunter.

DEFAULT_WATCH_CAP = 3                     # max watch cycles/session before forced terminal (>= 1)
DEFAULT_SESSION_MAX_TURNS = 40            # model-turn budget per session (>= 1)
DEFAULT_SESSION_MAX_WALL_SECS = 3600.0    # wall-clock budget in seconds (> 0)
DEFAULT_SIZE_FACTOR_A_PLUS = 1.0          # full size for a_plus, in (0.0, 1.0]
DEFAULT_SIZE_FACTOR_B_CONTINUATION = 0.6  # reduced size for b_continuation, in (0.0, 1.0]
DEFAULT_SIZE_FACTOR_SCALP = 0.3           # small size for scalp, in (0.0, 1.0]
DEFAULT_LOWER_TIERS_ENABLED = True        # b_continuation + scalp on/off
DEFAULT_HEARTBEAT_ENABLED = False         # opt-in; default off so its cost is only incurred when enabled
DEFAULT_HEARTBEAT_CADENCE_SECS = 300.0    # heartbeat cadence in seconds (> 0)
DEFAULT_HEARTBEAT_MAX = 6                  # max heartbeats/session (>= 0; 0 == none even if enabled)
DEFAULT_PRUNE_KEEP_RECENT_TURNS = 8       # recent turns always retained (>= 1)
DEFAULT_PRUNE_MAX_MESSAGES = 40           # pruning target ceiling (>= 1)

# ── Environment variable names ────────────────────────────────────────────────
ENV_WATCH_CAP = "OPPORTUNITY_WATCH_CAP"
ENV_SESSION_MAX_TURNS = "OPPORTUNITY_SESSION_MAX_TURNS"
ENV_SESSION_MAX_WALL_SECS = "OPPORTUNITY_SESSION_MAX_WALL_SECS"
ENV_SIZE_FACTOR_A_PLUS = "OPPORTUNITY_SIZE_FACTOR_A_PLUS"
ENV_SIZE_FACTOR_B_CONTINUATION = "OPPORTUNITY_SIZE_FACTOR_B_CONTINUATION"
ENV_SIZE_FACTOR_SCALP = "OPPORTUNITY_SIZE_FACTOR_SCALP"
ENV_LOWER_TIERS_ENABLED = "OPPORTUNITY_LOWER_TIERS_ENABLED"
ENV_HEARTBEAT_ENABLED = "OPPORTUNITY_HEARTBEAT_ENABLED"
ENV_HEARTBEAT_CADENCE_SECS = "OPPORTUNITY_HEARTBEAT_CADENCE_SECS"
ENV_HEARTBEAT_MAX = "OPPORTUNITY_HEARTBEAT_MAX"
ENV_PRUNE_KEEP_RECENT_TURNS = "OPPORTUNITY_PRUNE_KEEP_RECENT_TURNS"
ENV_PRUNE_MAX_MESSAGES = "OPPORTUNITY_PRUNE_MAX_MESSAGES"

# ── Valid ranges ──────────────────────────────────────────────────────────────
# The Watch_Cap and Session_Budget turn count are integers >= 1 with no upper
# bound (a zero cap/budget would terminate before the agent could ever analyze).
# The prune thresholds are integers >= 1 (a zero would prune everything). The
# heartbeat maximum is an integer >= 0 (0 == no heartbeats even if enabled). The
# wall-clock budget and heartbeat cadence are strictly positive seconds
# (EXCLUSIVE lower bound of 0.0 — a zero/negative value would terminate or fire
# immediately and is not usable). The per-tier Size_Factors lie in the half-open
# interval (0.0, 1.0]: a zero size would make a committed tier a no-op, and a
# factor above full size is meaningless (Requirement 11.1).
_CAP_MIN = 1            # watch_cap minimum
_TURNS_MIN = 1          # session_max_turns minimum
_HEARTBEAT_MAX_MIN = 0  # heartbeat_max minimum (0 disables heartbeats)
_PRUNE_MIN = 1          # prune_keep_recent_turns / prune_max_messages minimum
_SECS_LOW = 0.0         # EXCLUSIVE lower bound for wall-clock / cadence seconds
_FACTOR_LOW = 0.0       # EXCLUSIVE lower bound for a Size_Factor
_FACTOR_HIGH = 1.0      # inclusive upper bound for a Size_Factor

# Truthy / falsy spellings accepted for the boolean enable flags. Anything else
# (including empty / whitespace / garbage) degrades to the documented default.
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class OpportunityConfig:
    """The resolved, validated configuration for the Adaptive Opportunity Engine.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the pure core's purity guarantee). For identical
    environment-variable values the resolved configuration is identical on
    repeated runs (Requirement 11.2 determinism). Every field is guaranteed to
    sit within its documented valid range because ``resolve_opportunity_config``
    clamps unset / empty / unparseable / out-of-range inputs to the documented
    default.

    Setting ``lower_tiers_enabled=False`` and ``heartbeat_enabled=False`` restores
    the pre-engine A+-only, cap-bounded hunter (Requirement 11.3).
    """

    watch_cap: int                     # max watch cycles/session before forced terminal (>= 1)
    session_max_turns: int             # model-turn budget per session (>= 1)
    session_max_wall_secs: float       # wall-clock budget in seconds (> 0)
    size_factor_a_plus: float          # full size for a_plus, in (0.0, 1.0]
    size_factor_b_continuation: float  # reduced size for b_continuation, in (0.0, 1.0]
    size_factor_scalp: float           # small size for scalp, in (0.0, 1.0]
    lower_tiers_enabled: bool          # b_continuation + scalp on/off (default: True)
    heartbeat_enabled: bool            # heartbeat monitoring on/off (default: False, opt-in)
    heartbeat_cadence_secs: float      # heartbeat cadence in seconds (> 0)
    heartbeat_max: int                 # max heartbeats/session (>= 0; 0 == none even if enabled)
    prune_keep_recent_turns: int       # recent turns always retained (>= 1)
    prune_max_messages: int            # pruning target ceiling (>= 1)


def _resolve_int(env_name: str, default: int, low: int) -> int:
    """Resolve one integer parameter from its own env var (Requirement 11.1-11.2).

    Falls back to ``default`` when the var is unset/empty/whitespace, cannot be
    parsed as an int, or parses but is below ``low`` (the minimum valid value).
    Never raises. Mirrors the ``_resolve_int`` convention in ``attribution.py`` /
    ``telemetry.py`` / ``regime.py`` / ``rs.py``.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except (ValueError, TypeError):
        return default
    if value < low:
        return default
    return value


def _resolve_float(env_name: str, default: float, low: float, high: float) -> float:
    """Resolve one float parameter from its own env var (Requirement 11.1-11.2).

    Falls back to ``default`` when the var is unset/empty/whitespace, cannot be
    parsed as a float, is non-finite (NaN/inf), or parses but falls outside the
    inclusive band ``[low, high]``. Never raises. Mirrors the ``_resolve_float``
    convention in ``attribution.py`` / ``telemetry.py`` / ``regime.py`` /
    ``order_flow.py``. Callers needing an EXCLUSIVE lower bound resolve with an
    inclusive low and then revert the boundary value to the default (see
    ``resolve_opportunity_config``).
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except (ValueError, TypeError):
        return default
    if not math.isfinite(value):
        return default
    if value < low or value > high:
        return default
    return value


def _resolve_bool(env_name: str, default: bool) -> bool:
    """Resolve one boolean flag from its own env var (Requirement 11.1-11.2).

    Falls back to ``default`` when the var is unset/empty/whitespace or carries a
    token outside the recognized truthy/falsy spellings. Never raises. Parsing is
    case-insensitive and whitespace-tolerant. Mirrors the ``_resolve_bool``
    convention in ``attribution.py`` / ``telemetry.py``.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return default


def resolve_opportunity_config() -> OpportunityConfig:
    """Resolve every parameter from its own env var with documented defaults.

    Per-parameter rules (Requirement 11.1-11.2):
      * unset / empty / whitespace  -> documented default
      * unparseable as its type     -> documented default (never raises)
      * parses but out of range     -> documented default (never raises)

    ``session_max_wall_secs`` and ``heartbeat_cadence_secs`` have an EXCLUSIVE
    lower bound of 0: a zero (or negative) wall-clock budget would terminate every
    session immediately and a zero cadence would fire heartbeats without pause —
    neither is a usable parameter. ``_resolve_float`` only enforces an inclusive
    band, so each is resolved with an inclusive low of 0.0 and the boundary value
    0.0 (the only way a non-negative value can sit at the exclusive bound) is
    reverted to the documented default — clamping the result to ``(0.0, inf)``.
    The per-tier Size_Factors are resolved the same way into the half-open
    interval ``(0.0, 1.0]`` (mirrors the ``attribution`` / ``trade_manager``
    exclusive-bound convention).

    Identical environments resolve to identical configuration (Requirement 11.2
    determinism). This function NEVER raises. Every returned field is guaranteed
    to sit within its documented valid range.
    """
    watch_cap = _resolve_int(ENV_WATCH_CAP, DEFAULT_WATCH_CAP, _CAP_MIN)

    session_max_turns = _resolve_int(
        ENV_SESSION_MAX_TURNS, DEFAULT_SESSION_MAX_TURNS, _TURNS_MIN
    )

    session_max_wall_secs = _resolve_float(
        ENV_SESSION_MAX_WALL_SECS,
        DEFAULT_SESSION_MAX_WALL_SECS,
        _SECS_LOW,
        math.inf,
    )
    # Clamp to the open interval (0.0, inf): a resolved 0.0 sits on the exclusive
    # lower bound and is not usable, so revert it to the documented default.
    if session_max_wall_secs <= _SECS_LOW:
        session_max_wall_secs = DEFAULT_SESSION_MAX_WALL_SECS

    size_factor_a_plus = _resolve_float(
        ENV_SIZE_FACTOR_A_PLUS, DEFAULT_SIZE_FACTOR_A_PLUS, _FACTOR_LOW, _FACTOR_HIGH
    )
    if size_factor_a_plus <= _FACTOR_LOW:
        size_factor_a_plus = DEFAULT_SIZE_FACTOR_A_PLUS

    size_factor_b_continuation = _resolve_float(
        ENV_SIZE_FACTOR_B_CONTINUATION,
        DEFAULT_SIZE_FACTOR_B_CONTINUATION,
        _FACTOR_LOW,
        _FACTOR_HIGH,
    )
    if size_factor_b_continuation <= _FACTOR_LOW:
        size_factor_b_continuation = DEFAULT_SIZE_FACTOR_B_CONTINUATION

    size_factor_scalp = _resolve_float(
        ENV_SIZE_FACTOR_SCALP, DEFAULT_SIZE_FACTOR_SCALP, _FACTOR_LOW, _FACTOR_HIGH
    )
    if size_factor_scalp <= _FACTOR_LOW:
        size_factor_scalp = DEFAULT_SIZE_FACTOR_SCALP

    lower_tiers_enabled = _resolve_bool(
        ENV_LOWER_TIERS_ENABLED, DEFAULT_LOWER_TIERS_ENABLED
    )

    heartbeat_enabled = _resolve_bool(
        ENV_HEARTBEAT_ENABLED, DEFAULT_HEARTBEAT_ENABLED
    )

    heartbeat_cadence_secs = _resolve_float(
        ENV_HEARTBEAT_CADENCE_SECS,
        DEFAULT_HEARTBEAT_CADENCE_SECS,
        _SECS_LOW,
        math.inf,
    )
    if heartbeat_cadence_secs <= _SECS_LOW:
        heartbeat_cadence_secs = DEFAULT_HEARTBEAT_CADENCE_SECS

    heartbeat_max = _resolve_int(
        ENV_HEARTBEAT_MAX, DEFAULT_HEARTBEAT_MAX, _HEARTBEAT_MAX_MIN
    )

    prune_keep_recent_turns = _resolve_int(
        ENV_PRUNE_KEEP_RECENT_TURNS, DEFAULT_PRUNE_KEEP_RECENT_TURNS, _PRUNE_MIN
    )

    prune_max_messages = _resolve_int(
        ENV_PRUNE_MAX_MESSAGES, DEFAULT_PRUNE_MAX_MESSAGES, _PRUNE_MIN
    )

    return OpportunityConfig(
        watch_cap=watch_cap,
        session_max_turns=session_max_turns,
        session_max_wall_secs=session_max_wall_secs,
        size_factor_a_plus=size_factor_a_plus,
        size_factor_b_continuation=size_factor_b_continuation,
        size_factor_scalp=size_factor_scalp,
        lower_tiers_enabled=lower_tiers_enabled,
        heartbeat_enabled=heartbeat_enabled,
        heartbeat_cadence_secs=heartbeat_cadence_secs,
        heartbeat_max=heartbeat_max,
        prune_keep_recent_turns=prune_keep_recent_turns,
        prune_max_messages=prune_max_messages,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tiered opportunity ladder (task 2.1)
# ══════════════════════════════════════════════════════════════════════════════
#
# The ordered evaluation A+ -> B/continuation -> scalp -> stand-aside. The agent
# descends the ladder and takes the best *available* setup at appropriate size,
# rather than only waiting for perfection (Requirement 1). Lower tiers remain
# gated by the regime and session filters (Requirement 2.1) and a minimum
# evidence bar (Requirement 2.2), and can be disabled entirely to restore the
# pre-engine A+-only policy (Requirement 2.4 / 11.3).
#
# ``evaluate_tier`` and ``size_factor`` are PURE and TOTAL: they never call the
# Trade_Validator, never size a position, never perform I/O, and never raise on
# malformed / partial / None input. They reason over a plain ``evidence`` dict —
# the defensibility-shaped read the graph assembles from the Analysis_Tool
# results already in message history — described below.
#
# ── The ``evidence`` dict shape ───────────────────────────────────────────────
# Every field is optional; a missing / malformed field degrades gracefully to a
# neutral / absent reading (never an exception). Downstream test tasks (2.2-2.7)
# and the graph wiring (task 9.x) construct this exact shape:
#
#   {
#     # Structural pattern confidence in [0.0, 1.0] (get_chart_patterns). Values
#     # outside the band are clamped; a missing / non-numeric value reads 0.0.
#     "pattern_confidence": float,
#
#     # The defensible execution triple. Either supply the explicit levels and
#     # let the module derive defensibility (stop and target must sit on OPPOSITE
#     # sides of entry — a real risk/reward bracket), OR pass an explicit
#     # ``has_defensible_triple`` bool which, when present, is authoritative.
#     "entry":  float | None,
#     "stop":   float | None,
#     "target": float | None,
#     "has_defensible_triple": bool | None,
#
#     # Alignment / availability signals. Each is an optional sub-dict; an absent
#     # sub-dict, ``available: False``, or an unrecognized state all read neutral.
#     # A signal is POSITIVE when favorable/aligned and NEGATIVE when
#     # unfavorable/misaligned.
#     "regime":            {"available": bool, "favorability":       "favorable"|"neutral"|"unfavorable"},
#     "session":           {"available": bool, "time_favorability":  "favorable"|"neutral"|"unfavorable"},
#     "relative_strength": {"available": bool, "alignment":          "aligned"|"neutral"|"misaligned"},
#     "forecast":          {"available": bool, "forecast_alignment": "aligned"|"neutral"|"misaligned"},
#     "macro":             {"available": bool, "alignment":          "aligned"|"neutral"|"misaligned"},
#     "options":           {"available": bool, "alignment":          "aligned"|"neutral"|"misaligned"},
#   }
#
# ── Tier criteria ─────────────────────────────────────────────────────────────
# Let ``triple`` be whether a defensible entry/stop/target triple exists,
# ``conf`` the structural pattern confidence, ``aligned`` the count of POSITIVE
# signals among the six above, and ``misaligned`` the count of NEGATIVE signals.
#
#   a_plus         : triple AND conf >= A_PLUS_MIN_PATTERN_CONF AND
#                    aligned >= A_PLUS_MIN_ALIGNED AND misaligned == 0.
#                    (NOT gated on regime/session directly — but because an
#                    unfavorable regime or session is a NEGATIVE signal, the
#                    ``misaligned == 0`` clause structurally excludes a_plus in
#                    an unfavorable regime/session, so a_plus is only the pristine
#                    full-confluence setup.)
#   b_continuation : triple AND conf >= B_CONTINUATION_MIN_PATTERN_CONF AND
#                    aligned >= B_CONTINUATION_MIN_ALIGNED, GATED by regime/session
#                    (Requirement 2.1) and the lower-tier config flag (2.4).
#   scalp          : triple AND conf >= SCALP_MIN_PATTERN_CONF AND
#                    aligned >= SCALP_MIN_ALIGNED, GATED as above.
#   stand_aside    : nothing else met — returned with a NON-EMPTY rationale and a
#                    ``gated_by`` reason (Requirement 1.2).

OPPORTUNITY_TIERS = ("a_plus", "b_continuation", "scalp", "stand_aside")

# Minimum structural pattern confidence per tier (get_chart_patterns confidence,
# in [0.0, 1.0]). Non-increasing down the ladder — a stronger structure is
# required for a higher tier.
A_PLUS_MIN_PATTERN_CONF = 0.75
B_CONTINUATION_MIN_PATTERN_CONF = 0.55
SCALP_MIN_PATTERN_CONF = 0.40

# Minimum count of POSITIVE (favorable/aligned) confluence signals per tier, out
# of the six alignment signals. Non-increasing down the ladder.
A_PLUS_MIN_ALIGNED = 3
B_CONTINUATION_MIN_ALIGNED = 2
SCALP_MIN_ALIGNED = 1

# Recognized alignment-signal states (case-insensitive, whitespace-tolerant).
_POSITIVE_STATES = frozenset({"favorable", "aligned"})
_NEGATIVE_STATES = frozenset({"unfavorable", "misaligned"})


@dataclass(frozen=True)
class TierEvaluation:
    """The pure result of ``evaluate_tier`` (Data Models > TierEvaluation).

    Frozen so a computed evaluation cannot be mutated by a downstream consumer,
    preserving the pure core's guarantees.

    Fields:
      * ``tier``        — one of ``OPPORTUNITY_TIERS``.
      * ``size_factor`` — ``size_factor(tier, cfg)``; ``stand_aside`` -> 0.0.
      * ``rationale``   — human-readable, NON-EMPTY explanation citing the
        evidence behind the selected tier (Requirement 1.2 requires a stated
        reason on ``stand_aside``).
      * ``gated_by``    — why a higher/lower tier was NOT taken, one of
        ``'regime'`` | ``'session'`` | ``'evidence-bar'`` | ``'config'`` | ``None``.
        ``None`` when a tier was affirmatively selected on its own merits.
    """

    tier: str
    size_factor: float
    rationale: str
    gated_by: Optional[str]


def _is_finite_number(value) -> bool:
    """True for a real, finite int/float (bool excluded — a bool is not a level)."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _pattern_confidence(ev: dict) -> float:
    """Structural pattern confidence, clamped into [0.0, 1.0]; 0.0 if absent."""
    val = ev.get("pattern_confidence")
    if not _is_finite_number(val):
        return 0.0
    return max(0.0, min(1.0, float(val)))


def _has_defensible_triple(ev: dict) -> bool:
    """Whether a defensible entry/stop/target triple exists (Requirement 2.2).

    An explicit ``has_defensible_triple`` bool, when present, is authoritative.
    Otherwise the triple is defensible only when entry/stop/target are all finite
    numbers AND the stop and target sit on OPPOSITE sides of the entry (a real
    risk/reward bracket, long or short). Total — never raises.
    """
    override = ev.get("has_defensible_triple")
    if isinstance(override, bool):
        return override
    entry = ev.get("entry")
    stop = ev.get("stop")
    target = ev.get("target")
    if not (_is_finite_number(entry) and _is_finite_number(stop) and _is_finite_number(target)):
        return False
    # (target - entry) and (entry - stop) share a sign iff stop and target are on
    # opposite sides of entry: long -> target > entry > stop; short -> reverse.
    return (target - entry) * (entry - stop) > 0.0


def _signal_state(sub, keys) -> int:
    """Normalize one alignment signal to +1 (positive) / 0 (neutral) / -1 (negative).

    Reads the first present string field named in ``keys``. An absent/malformed
    sub-dict, an explicit ``available: False``, or an unrecognized state all read
    neutral (0). Total — never raises.
    """
    if not isinstance(sub, dict):
        return 0
    if sub.get("available") is False:
        return 0
    raw = None
    for key in keys:
        candidate = sub.get(key)
        if isinstance(candidate, str):
            raw = candidate.strip().lower()
            break
    if raw in _POSITIVE_STATES:
        return 1
    if raw in _NEGATIVE_STATES:
        return -1
    return 0


def _signal_states(ev: dict) -> dict:
    """The +1/0/-1 state of each of the six confluence signals."""
    return {
        "regime": _signal_state(ev.get("regime"), ("favorability",)),
        "session": _signal_state(ev.get("session"), ("time_favorability", "favorability")),
        "relative_strength": _signal_state(ev.get("relative_strength"), ("alignment",)),
        "forecast": _signal_state(ev.get("forecast"), ("forecast_alignment", "alignment")),
        "macro": _signal_state(ev.get("macro"), ("alignment",)),
        "options": _signal_state(ev.get("options"), ("alignment",)),
    }


def size_factor(tier: str, cfg: OpportunityConfig) -> float:
    """Map an Opportunity_Tier to its Size_Factor multiplier (Requirement 1.3).

    ``a_plus`` -> full, ``b_continuation`` -> reduced, ``scalp`` -> small,
    ``stand_aside`` (and any unknown tier) -> exactly 0.0. The three tradeable
    factors are read from ``cfg`` and clamped monotonic non-increasing down the
    ladder (``a_plus >= b_continuation >= scalp``) so the Size_Factor ordering
    holds for any configuration, even one whose env-resolved factors were not
    themselves ordered. Because the resolver guarantees every configured factor
    is in ``(0.0, 1.0]``, the clamped scalp factor stays strictly positive.

    PURE and TOTAL: reads ``cfg`` via ``getattr`` with a 0.0 fallback so a
    malformed ``cfg`` yields 0.0 rather than raising, and never sizes a position
    or touches the validated levels (Requirements 1.4 / 2.3 / 10.2).
    """
    a_plus = _cfg_factor(cfg, "size_factor_a_plus")
    b_cont = _cfg_factor(cfg, "size_factor_b_continuation")
    scalp = _cfg_factor(cfg, "size_factor_scalp")
    # Enforce a monotonic non-increasing ladder regardless of the raw config.
    b_cont = min(b_cont, a_plus)
    scalp = min(scalp, b_cont)
    if tier == "a_plus":
        return a_plus
    if tier == "b_continuation":
        return b_cont
    if tier == "scalp":
        return scalp
    return 0.0


def _cfg_factor(cfg, name: str) -> float:
    """Read a Size_Factor field from ``cfg``, defaulting to 0.0 if absent/malformed."""
    value = getattr(cfg, name, 0.0)
    if not _is_finite_number(value):
        return 0.0
    return float(value)


def evaluate_tier(evidence, cfg: OpportunityConfig) -> TierEvaluation:
    """Descend the Tier_Ladder and return the highest satisfied tier (Requirement 1).

    Evaluates ``a_plus -> b_continuation -> scalp`` and returns the highest tier
    whose criteria are met, else ``stand_aside`` with a non-empty rationale
    (Requirement 1.2). Applies the overtrading guardrails — the regime/session
    gate on lower tiers (Requirement 2.1) and the minimum evidence bar
    (Requirement 2.2) — and the config gate that disables tiers below ``a_plus``
    entirely (Requirement 2.4 / 11.3).

    PURE and TOTAL: reads a plain ``evidence`` dict, never calls the
    Trade_Validator, never sizes, and never raises on malformed / partial / None
    input.
    """
    ev = evidence if isinstance(evidence, dict) else {}

    triple = _has_defensible_triple(ev)
    conf = _pattern_confidence(ev)
    states = _signal_states(ev)
    aligned = sum(1 for s in states.values() if s > 0)
    misaligned = sum(1 for s in states.values() if s < 0)
    regime_unfavorable = states["regime"] < 0
    session_unfavorable = states["session"] < 0
    lower_enabled = bool(getattr(cfg, "lower_tiers_enabled", False))

    # ── A+ : the pristine, full-confluence setup ─────────────────────────────
    # Not gated on regime/session directly, but the ``misaligned == 0`` clause
    # structurally excludes an unfavorable regime/session (each a NEGATIVE
    # signal) from qualifying as A+.
    if triple and conf >= A_PLUS_MIN_PATTERN_CONF and aligned >= A_PLUS_MIN_ALIGNED and misaligned == 0:
        rationale = (
            f"A+ setup: defensible entry/stop/target triple, structural pattern "
            f"confidence {conf:.2f} >= {A_PLUS_MIN_PATTERN_CONF:.2f}, "
            f"{aligned} aligned confluence signals with no misalignment."
        )
        return TierEvaluation("a_plus", size_factor("a_plus", cfg), rationale, None)

    # ── Lower tiers: evidence bar first (Requirement 2.2), then gates ─────────
    b_bar = triple and conf >= B_CONTINUATION_MIN_PATTERN_CONF and aligned >= B_CONTINUATION_MIN_ALIGNED
    scalp_bar = triple and conf >= SCALP_MIN_PATTERN_CONF and aligned >= SCALP_MIN_ALIGNED

    if b_bar or scalp_bar:
        # A lower tier's minimum evidence bar is met; now apply the gates.
        if not lower_enabled:
            # Config gate (Requirement 2.4 / 11.3): lower tiers disabled entirely.
            return TierEvaluation(
                "stand_aside",
                0.0,
                "Stand aside: a lower-tier setup is present but tiers below A+ are "
                "disabled by configuration (A+-only policy).",
                "config",
            )
        if regime_unfavorable:
            # Regime gate (Requirement 2.1): unfavorable regime forbids lower tiers.
            return TierEvaluation(
                "stand_aside",
                0.0,
                "Stand aside: a lower-tier setup is present but the market regime is "
                "unfavorable, so no b_continuation or scalp trade is taken.",
                "regime",
            )
        if session_unfavorable:
            # Session gate (Requirement 2.1): unfavorable session forbids lower tiers.
            return TierEvaluation(
                "stand_aside",
                0.0,
                "Stand aside: a lower-tier setup is present but the session "
                "time-favorability is unfavorable, so no b_continuation or scalp "
                "trade is taken.",
                "session",
            )
        # Gates cleared — select the highest lower tier whose bar is met.
        if b_bar:
            rationale = (
                f"B/continuation setup: defensible triple, structural pattern "
                f"confidence {conf:.2f} >= {B_CONTINUATION_MIN_PATTERN_CONF:.2f}, "
                f"{aligned} aligned confluence signals, regime and session not "
                f"unfavorable."
            )
            return TierEvaluation("b_continuation", size_factor("b_continuation", cfg), rationale, None)
        rationale = (
            f"Scalp/micro setup: defensible triple, structural pattern confidence "
            f"{conf:.2f} >= {SCALP_MIN_PATTERN_CONF:.2f}, {aligned} aligned "
            f"confluence signal(s), regime and session not unfavorable."
        )
        return TierEvaluation("scalp", size_factor("scalp", cfg), rationale, None)

    # ── No tier's criteria met — stand aside on the evidence bar (R1.2) ──────
    if not triple:
        reason = "no defensible entry/stop/target triple is present"
    elif conf < SCALP_MIN_PATTERN_CONF:
        reason = (
            f"structural pattern confidence {conf:.2f} is below the "
            f"{SCALP_MIN_PATTERN_CONF:.2f} floor for even a scalp"
        )
    else:
        reason = (
            f"only {aligned} aligned confluence signal(s) — below the minimum "
            f"evidence bar for any tradeable tier"
        )
    return TierEvaluation(
        "stand_aside",
        0.0,
        f"Stand aside: {reason}.",
        "evidence-bar",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Bounded hunt — Watch_Cap and Session_Budget predicates (task 3.1)
# ══════════════════════════════════════════════════════════════════════════════
#
# The bounded-hunt predicates are the terminal safety net that closes the
# unbounded ``analyze -> watch -> invalidate -> re-watch`` loop (Requirement 3).
# ``should_continue`` (task 9.2) consults them BEFORE the ``suspend`` route: when
# either bound is met the graph routes to ``force_terminal`` and commits a
# terminal decision instead of registering another watch. Because each
# invalidation counts toward the Watch_Cap (Requirement 4.4) and both the turn
# and wall-clock budgets are monotonically approached, every session is
# guaranteed to terminate (Requirement 3.3).
#
# All three functions are PURE and TOTAL: they read a plain ``state`` dict and an
# injected clock ``now`` (no clock is read inside the pure function — the caller
# passes ``time.time()``), never perform I/O, and never raise on malformed /
# partial / ``None`` state (a missing / ``None`` / non-numeric counter reads 0; a
# missing / ``None`` / non-numeric ``session_started_at`` or ``now`` means the
# wall-clock condition simply cannot fire).
#
# ── The ``state`` dict shape ──────────────────────────────────────────────────
# Every field is optional and degrades gracefully. The graph's ``AgentState``
# (task 9.1) carries at least:
#
#   {
#     # Watch_Cycles registered so far this session (each watch registration and
#     # each invalidation increments it). Missing / None / non-numeric -> 0.
#     "watch_cycles": int,
#
#     # Model turns taken so far this session. Missing / None / non-numeric -> 0.
#     "session_turns": int,
#
#     # Monotonic wall-clock seconds (``time.time()``) stamped on the first /run
#     # turn. None (not yet stamped) / non-numeric -> the wall-clock budget
#     # condition cannot fire.
#     "session_started_at": Optional[float],
#   }
#
# ── Termination precedence (Requirement 3.5) ──────────────────────────────────
# When BOTH bounds hold, ``watch-cap-reached`` takes precedence over
# ``session-budget-exhausted``. Rationale: the Watch_Cap is the tighter, more
# specific loop-control bound (it fires on the exact re-arm cycle the engine
# exists to close), so it is the more informative reason to surface on the
# committed decision. The precedence is a fixed, deterministic ordering so the
# same state always yields the same reason.


def _coerce_count(value) -> int:
    """Coerce a session counter to a non-negative int; missing/None/malformed -> 0.

    A missing key, an explicit ``None``, a bool, a non-numeric value, or a
    non-finite float all read 0 (Requirement 3 totality). A finite numeric value
    is floored toward zero and clamped to be non-negative so a corrupt negative
    counter can never make a bound spuriously true or false. Total — never raises.
    """
    if not _is_finite_number(value):
        return 0
    count = int(value)
    return count if count > 0 else 0


def _cfg_int(cfg, name: str, default: int) -> int:
    """Read an int bound from ``cfg`` via getattr, defaulting if absent/malformed."""
    value = getattr(cfg, name, default)
    if not _is_finite_number(value):
        return default
    return int(value)


def _cfg_float(cfg, name: str, default: float) -> float:
    """Read a float bound from ``cfg`` via getattr, defaulting if absent/malformed."""
    value = getattr(cfg, name, default)
    if not _is_finite_number(value):
        return default
    return float(value)


def watch_cap_reached(state, cfg: OpportunityConfig) -> bool:
    """True iff the Watch_Cap has been reached (Requirements 3.1, 3.3, 4.4).

    Returns ``True`` once ``watch_cycles >= watch_cap``. Because each watch
    registration AND each invalidation increments ``watch_cycles`` (Requirement
    4.4), repeated invalidations converge on this bound and force a terminal
    decision rather than another re-arm.

    PURE and TOTAL: a missing / ``None`` / non-numeric ``watch_cycles`` reads 0,
    and the cap is read from ``cfg`` with the documented default so a malformed
    ``cfg`` never disables the safety bound. Never raises.
    """
    st = state if isinstance(state, dict) else {}
    watch_cycles = _coerce_count(st.get("watch_cycles"))
    watch_cap = _cfg_int(cfg, "watch_cap", DEFAULT_WATCH_CAP)
    return watch_cycles >= watch_cap


def session_budget_exhausted(state, cfg: OpportunityConfig, now) -> bool:
    """True iff the Session_Budget is exhausted (Requirements 3.2, 3.3).

    Returns ``True`` iff the model-turn budget is reached
    (``session_turns >= session_max_turns``) OR the wall-clock budget has elapsed
    (``(now - session_started_at) >= session_max_wall_secs``). The clock ``now``
    is INJECTED by the caller — no clock is read inside this pure function.

    PURE and TOTAL: a missing / ``None`` / non-numeric ``session_turns`` reads 0;
    a missing / ``None`` / non-numeric ``session_started_at`` or a non-numeric
    ``now`` means the wall-clock condition simply cannot fire (the turn condition
    is still evaluated). Never raises.
    """
    st = state if isinstance(state, dict) else {}

    session_turns = _coerce_count(st.get("session_turns"))
    session_max_turns = _cfg_int(cfg, "session_max_turns", DEFAULT_SESSION_MAX_TURNS)
    if session_turns >= session_max_turns:
        return True

    started_at = st.get("session_started_at")
    if not _is_finite_number(started_at) or not _is_finite_number(now):
        # Not yet stamped (or an unusable clock) — the wall-clock bound cannot fire.
        return False
    session_max_wall_secs = _cfg_float(
        cfg, "session_max_wall_secs", DEFAULT_SESSION_MAX_WALL_SECS
    )
    return (float(now) - float(started_at)) >= session_max_wall_secs


def termination_reason(state, cfg: OpportunityConfig, now) -> Optional[str]:
    """The stated reason a session must terminate now, or ``None`` (Requirement 3.5).

    Returns exactly one of ``'watch-cap-reached'`` | ``'session-budget-exhausted'``
    | ``None`` under a deterministic precedence: the Watch_Cap is checked first, so
    when BOTH bounds hold ``'watch-cap-reached'`` is returned (the tighter, more
    specific loop-control bound). This reason is the one the graph's
    ``force_terminal`` node (task 9.2) carries on the committed terminal decision.

    PURE and TOTAL over malformed / partial / ``None`` state (delegates to the two
    predicates above). Never raises.
    """
    if watch_cap_reached(state, cfg):
        return "watch-cap-reached"
    if session_budget_exhausted(state, cfg, now):
        return "session-budget-exhausted"
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Invalidation post-mortem core (task 4.1)
# ══════════════════════════════════════════════════════════════════════════════
#
# After a session resumes on an invalidation, the engine forces a strategic pivot
# rather than a blind re-arm of the identical thesis (Requirement 4). The graph's
# ``tool_node`` gate (task 9.3) uses these three PURE, TOTAL functions to decide
# whether a proposed ``watch_price_condition`` re-arm is the SAME thesis as the
# just-invalidated one (and must therefore be suppressed), and to re-size a
# re-armed invalidation level so a noise-level stop does not immediately re-trip.
#
# All three are side-effect-free, do no I/O, and NEVER raise on malformed /
# partial / ``None`` input. They deliberately FAIL OPEN: on missing / malformed
# input ``is_rearm_unchanged`` returns ``False`` (so a malformed re-arm is NEVER
# falsely blocked — the agent is only ever blocked when we are confident the
# thesis is genuinely unchanged), and ``volatility_floored_invalidation`` returns
# ``None`` when the inputs are unusable (so the caller keeps the model's own
# level rather than fabricating one).
#
# ── The ``watch_args`` / thesis-shaped dict ───────────────────────────────────
# ``thesis_fingerprint`` accepts either the raw ``watch_price_condition`` tool
# arguments OR an already-computed fingerprint, so callers may pass either:
#
#   raw watch_args : { "symbol": str, "timeframe": str, "price_level": float,
#                      "direction": "above"|"up"|"below"|"down", ... }
#   fingerprint    : { "symbol": str|None, "timeframe": str|None,
#                      "direction": "above"|"below"|None, "level": float|None }
#
# Every field is optional; a missing / malformed field degrades to ``None`` in the
# returned fingerprint (never an exception).

# Half an ATR: a proposed re-arm whose level sits within this volatility-scaled
# band of the just-invalidated level is treated as the SAME level (noise-level
# jitter, not a genuinely different thesis) and is blocked (Requirement 4.2).
REARM_LEVEL_ATR_TOLERANCE_MULT = 0.5

# One ATR: a re-armed invalidation level must sit at least this volatility-scaled
# floor away from the reference on the OPPOSITE side, so a noise-level stop does
# not immediately re-trip on resume (Requirement 4.3). Chosen strictly greater
# than ``REARM_LEVEL_ATR_TOLERANCE_MULT`` so a floored invalidation always clears
# the "same level" tolerance band.
VOL_FLOOR_ATR_MULT = 1.0

# Number of decimal places the price level is quantized to in a fingerprint. This
# removes floating-point representation noise (e.g. 2500.00000001 vs 2500.0) so
# fingerprints of an identical thesis compare equal; the volatility-scaled
# "sameness" comparison itself is done against the ATR tolerance in
# ``is_rearm_unchanged`` (this quantization is only float-noise hygiene).
_THESIS_LEVEL_QUANTIZE_DECIMALS = 4

# Recognized direction spellings, normalized to the two canonical sides. "above"/
# "up" (and the long-trade synonyms) mean the target is ABOVE the reference and
# the invalidation sits BELOW it; "below"/"down" (and the short synonyms) are the
# mirror. Case-insensitive, whitespace-tolerant.
_DIRECTION_ABOVE = frozenset({"above", "up", "long", "buy"})
_DIRECTION_BELOW = frozenset({"below", "down", "short", "sell"})


def _normalize_direction(value) -> Optional[str]:
    """Normalize a direction spelling to ``'above'`` | ``'below'`` | ``None``.

    ``above``/``up``/``long``/``buy`` -> ``'above'`` (target above the reference,
    invalidation below); ``below``/``down``/``short``/``sell`` -> ``'below'`` (the
    mirror). Anything unrecognized / non-string reads ``None``. Total.
    """
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    if token in _DIRECTION_ABOVE:
        return "above"
    if token in _DIRECTION_BELOW:
        return "below"
    return None


def _quantize_level(value) -> Optional[float]:
    """Quantize a price level to kill float noise; ``None`` if not a finite number."""
    if not _is_finite_number(value):
        return None
    return round(float(value), _THESIS_LEVEL_QUANTIZE_DECIMALS)


def thesis_fingerprint(watch_args) -> dict:
    """A comparable fingerprint of a watch thesis (Requirement 4.2).

    Produces the four-field fingerprint used to detect an unchanged re-arm:
    ``symbol`` (upper-cased, stripped), ``timeframe`` (lower-cased, stripped),
    ``direction`` (normalized to ``'above'`` | ``'below'``), and ``level`` (the
    quantized target price level). Reads the target price from ``price_level``
    (raw ``watch_price_condition`` args) or, failing that, ``level`` (an
    already-computed fingerprint), so it is idempotent when fed its own output.

    PURE and TOTAL: any missing / malformed / ``None`` field degrades to ``None``
    in the returned dict (a non-dict / ``None`` input yields an all-``None``
    fingerprint). Never raises.
    """
    args = watch_args if isinstance(watch_args, dict) else {}

    symbol_raw = args.get("symbol")
    symbol = symbol_raw.strip().upper() if isinstance(symbol_raw, str) and symbol_raw.strip() else None

    tf_raw = args.get("timeframe")
    timeframe = tf_raw.strip().lower() if isinstance(tf_raw, str) and tf_raw.strip() else None

    direction = _normalize_direction(args.get("direction"))

    level_raw = args.get("price_level")
    if level_raw is None:
        level_raw = args.get("level")
    level = _quantize_level(level_raw)

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "level": level,
    }


def is_rearm_unchanged(prior, proposed, atr, cfg: OpportunityConfig) -> bool:
    """True iff a proposed re-arm is the SAME thesis as the just-invalidated one.

    A re-arm is "unchanged" (and thus blocked by the ``tool_node`` gate so the
    agent must change approach — Requirement 4.2) when it shares the prior
    thesis's symbol, timeframe, and direction AND its target level sits within a
    volatility tolerance (``REARM_LEVEL_ATR_TOLERANCE_MULT`` x ``atr``) of the
    prior level. Any change of symbol, timeframe, direction, or a level beyond the
    tolerance is a genuinely different thesis and is NOT flagged.

    FAILS OPEN to ``False`` (never blocks) whenever it cannot be confident the
    thesis is unchanged: a non-dict / ``None`` ``prior`` or ``proposed``, a
    fingerprint missing any of symbol / timeframe / direction / level, or an
    ``atr`` that is missing / non-finite / non-positive (so the volatility
    tolerance cannot be derived). ``cfg`` is accepted for interface symmetry; the
    tolerance multiple is a documented module constant.

    PURE and TOTAL — fingerprints both inputs internally (so callers may pass raw
    watch_args or fingerprints) and never raises.
    """
    del cfg  # reserved for future tolerance tuning; tolerance is a module constant

    fp_prior = thesis_fingerprint(prior)
    fp_prop = thesis_fingerprint(proposed)

    # Fail open: an incomplete fingerprint means we cannot be confident -> False.
    for fp in (fp_prior, fp_prop):
        if fp["symbol"] is None or fp["timeframe"] is None or fp["direction"] is None or fp["level"] is None:
            return False

    # A change of symbol / timeframe / direction is a genuinely different thesis.
    if fp_prior["symbol"] != fp_prop["symbol"]:
        return False
    if fp_prior["timeframe"] != fp_prop["timeframe"]:
        return False
    if fp_prior["direction"] != fp_prop["direction"]:
        return False

    # Fail open: without a usable ATR the volatility tolerance cannot be derived.
    if not _is_finite_number(atr) or float(atr) <= 0.0:
        return False

    tolerance = REARM_LEVEL_ATR_TOLERANCE_MULT * float(atr)
    return abs(fp_prior["level"] - fp_prop["level"]) <= tolerance


def volatility_floored_invalidation(direction, ref_price, proposed_inv, atr) -> Optional[float]:
    """Re-size an invalidation level to sit at least a volatility floor away (R4.3).

    Returns an invalidation level on the OPPOSITE side of ``ref_price`` from the
    thesis direction — BELOW the reference for an ``above``/long thesis, ABOVE it
    for a ``below``/short thesis — whose distance from the reference is at least
    the volatility floor (``VOL_FLOOR_ATR_MULT`` x ``atr``), so a noise-level stop
    cannot immediately re-trip on resume.

    When ``proposed_inv`` is already on the correct opposite side AND at least the
    floor away, it is returned unchanged; otherwise (too close, on the wrong side,
    missing, or malformed) it is pushed out to exactly the floor distance.

    Returns ``None`` on unusable input — an unrecognized ``direction``, a
    non-finite ``ref_price``, or a missing / non-finite / non-positive ``atr``
    (without which the floor cannot be derived) — so the caller keeps the model's
    own level rather than fabricating one. PURE and TOTAL; never raises.
    """
    side = _normalize_direction(direction)
    if side is None:
        return None
    if not _is_finite_number(ref_price):
        return None
    if not _is_finite_number(atr) or float(atr) <= 0.0:
        return None

    ref = float(ref_price)
    floor = VOL_FLOOR_ATR_MULT * float(atr)

    if side == "above":
        # Long/bullish thesis -> invalidation sits BELOW the reference.
        floored = ref - floor
        if _is_finite_number(proposed_inv) and float(proposed_inv) <= floored:
            # Already on the correct side and at least the floor away — keep it.
            return float(proposed_inv)
        return floored

    # side == "below": short/bearish thesis -> invalidation sits ABOVE the reference.
    floored = ref + floor
    if _is_finite_number(proposed_inv) and float(proposed_inv) >= floored:
        return float(proposed_inv)
    return floored


# ══════════════════════════════════════════════════════════════════════════════
# Cheap resume — Delta_Recheck & heartbeat accounting (task 5.1)
# ══════════════════════════════════════════════════════════════════════════════
#
# When a suspended session resumes — on target, invalidation, or heartbeat — the
# engine must re-check only the data relevant to that trigger instead of re-running
# the full order-of-operations every time (Requirement 6). A full re-scan on every
# wake-up is what makes a bounded hunt blow up tool and LLM cost; a cheap, focused
# Delta_Recheck keeps each resume affordable (Requirements 6.1, 6.2, 6.3).
#
# These functions are PURE and TOTAL: they read plain values, never perform I/O,
# and never raise on malformed / partial / None input.
#
# ── The full order-of-operations tool set ─────────────────────────────────────
# ``FULL_ORDER_OF_OPERATIONS_TOOLS`` is the authoritative list of the Analysis_Tools
# the ``DEEP_QUANT_SYSTEM_PROMPT`` ``<order_of_operations>`` walks through on a full
# analysis, in prompt order (steps 1 -> 8 including the lettered sub-steps). It is
# the exact set of ``@tool`` analysis functions registered in ``tools.py`` for that
# loop. Each Delta_Recheck plan is a NON-EMPTY, STRICT (proper) subset of this set
# (Property 13), so a resume always re-checks something yet always strictly less
# than a full re-scan.
#
# NOTE: ``get_order_flow`` is a registered analysis tool but is deliberately NOT
# part of this constant, because it is not one of the numbered/lettered steps in
# the ``<order_of_operations>`` the agent is instructed to walk; this constant is
# scoped to that documented full-scan loop so "strict subset of the full scan"
# means exactly the loop the prompt defines.

FULL_ORDER_OF_OPERATIONS_TOOLS = (
    "get_multi_tf_trend",     # 1.  MACRO ALIGNMENT   — 1H/4H/1D bias
    "get_consensus_report",   # 2.  MICROSTRUCTURE    — full raw indicators
    "get_market_regime",      # 2b. MARKET REGIME GATE
    "get_relative_strength",  # 2c. RELATIVE STRENGTH & INDEX CONTEXT
    "get_session_context",    # 2d. SESSION & EXPIRY CONTEXT
    "get_options_analytics",  # 2e. OPTIONS POSITIONING
    "get_event_risk",         # 2f. EVENT-DATE RISK GATE — scheduled-event proximity
    "get_support_resistance", # 3.  KEY LEVELS
    "get_volume_profile",     # 3b. AUCTION STRUCTURE
    "get_chart_patterns",     # 4.  STRUCTURAL PATTERNS
    "get_candles",            # 5.  PRICE ACTION
    "get_forecast",           # 6.  PREDICTIVE CROSS-CHECK (primary)
    "get_prediction",         # 6.  PREDICTIVE CROSS-CHECK (secondary)
    "get_news_context",       # 7.  NEWS CATALYST
    "get_trade_performance",  # 8.  TRACK-RECORD CALIBRATION
)

# Canonical resume trigger kinds (Requirement 6.1). These mirror the Rust
# watcher / ``/resume`` contract: ``target`` and ``invalidation`` are the two
# existing ``WatcherTrigger`` kinds, and ``heartbeat`` is the additional bounded
# re-evaluation channel (Requirement 5).
RESUME_TARGET = "target"
RESUME_INVALIDATION = "invalidation"
RESUME_HEARTBEAT = "heartbeat"
RESUME_KINDS = (RESUME_TARGET, RESUME_INVALIDATION, RESUME_HEARTBEAT)

# Documented fallback for an unrecognized / None / malformed trigger kind
# (``classify_resume`` totality). We fall back to ``target`` — the canonical
# "the watched condition was met, wake and re-verify the thesis" resume — for two
# reasons: (1) the Rust ``/resume`` contract's own default ``trigger_kind`` is
# ``"target"`` (an unlabelled resume is treated as a target reach), and (2) the
# ``target`` Delta_Recheck plan is the fullest of the three (thesis-confirmation
# before any commit), so mis-labelling an unknown resume as ``target`` errs toward
# MORE re-verification rather than less — the safe direction. It never errs toward
# skipping the invalidation post-mortem, because the post-mortem is driven by the
# graph's ``postmortem_pending`` flag set on the actual invalidation resume path,
# not by this classifier alone.
_RESUME_FALLBACK = RESUME_TARGET

# Recognized spellings for each canonical kind (case-insensitive, whitespace- and
# separator-tolerant). Anything outside these degrades to ``_RESUME_FALLBACK``.
_TARGET_TOKENS = frozenset({"target", "targets", "target_reached", "target-reached", "hit", "reached"})
_INVALIDATION_TOKENS = frozenset(
    {"invalidation", "invalidated", "invalid", "stop", "stopped", "stop_out", "stop-out"}
)
_HEARTBEAT_TOKENS = frozenset({"heartbeat", "heart_beat", "heart-beat", "hb", "tick", "pulse"})


def classify_resume(trigger_kind) -> str:
    """Normalize any resume trigger kind to one canonical value (Requirement 6.1).

    Returns exactly one of ``'target'`` | ``'invalidation'`` | ``'heartbeat'``.
    Parsing is case-insensitive and whitespace-tolerant. An unrecognized / empty /
    ``None`` / non-string / malformed ``trigger_kind`` degrades to the documented
    fallback ``'target'`` (see ``_RESUME_FALLBACK`` for the rationale).

    PURE and TOTAL — never raises.
    """
    if not isinstance(trigger_kind, str):
        return _RESUME_FALLBACK
    token = trigger_kind.strip().lower()
    if not token:
        return _RESUME_FALLBACK
    if token in _TARGET_TOKENS:
        return RESUME_TARGET
    if token in _INVALIDATION_TOKENS:
        return RESUME_INVALIDATION
    if token in _HEARTBEAT_TOKENS:
        return RESUME_HEARTBEAT
    return _RESUME_FALLBACK


# ── Per-trigger minimal Delta_Recheck plans ───────────────────────────────────
# Each plan is the MINIMAL set of Analysis_Tools genuinely relevant to re-verifying
# the thesis for that trigger — a NON-EMPTY, STRICT subset of
# ``FULL_ORDER_OF_OPERATIONS_TOOLS`` (Property 13). Tools within a plan are listed
# in ``FULL_ORDER_OF_OPERATIONS_TOOLS`` order so the returned plan is deterministic.
#
#   target       — the watched level was reached: CONFIRM the entry is still valid
#                  before committing. Re-check fresh price action, momentum/
#                  indicators, the scheduled-event proximity, the key levels, the
#                  structural pattern, and the predictive cross-check (6 tools).
#   invalidation — the price-only invalidation tripped: DIAGNOSE why the thesis
#                  failed. Re-check what actually happened at the level, whether
#                  momentum flipped, whether the regime changed, and whether the
#                  levels still hold (4 tools).
#   heartbeat    — a bounded mid-wait pulse: the CHEAPEST check — has the setup
#                  developed or decayed? Re-check only fresh candles and the
#                  indicator consensus (2 tools).
_DELTA_RECHECK_PLANS = {
    RESUME_TARGET: (
        "get_candles",
        "get_consensus_report",
        "get_event_risk",
        "get_support_resistance",
        "get_chart_patterns",
        "get_forecast",
    ),
    RESUME_INVALIDATION: (
        "get_candles",
        "get_consensus_report",
        "get_market_regime",
        "get_support_resistance",
    ),
    RESUME_HEARTBEAT: (
        "get_candles",
        "get_consensus_report",
    ),
}


def delta_recheck_plan(trigger_kind, thesis=None) -> list:
    """The minimal, trigger-relevant Analysis_Tool set to consult on resume (R6).

    Given a resume ``trigger_kind`` (any spelling — normalized via
    ``classify_resume``), returns the MINIMAL list of Analysis_Tools genuinely
    relevant to re-verifying the thesis for that trigger. The returned list is
    guaranteed NON-EMPTY and a STRICT (proper) subset of
    ``FULL_ORDER_OF_OPERATIONS_TOOLS`` (Property 13), so every resume re-checks
    only the trigger-relevant data and is strictly cheaper than a full re-scan
    (Requirements 6.1, 6.2, 6.3). Tools are returned in canonical
    order-of-operations order, so the plan is deterministic for a given trigger.

    ``thesis`` is accepted for interface symmetry with the graph/resume wiring
    (task 12.1) and future thesis-aware refinement; the current minimal plan is
    trigger-driven and deterministic, so ``thesis`` does not change the result.

    PURE and TOTAL — an unrecognized / ``None`` / malformed trigger normalizes to
    the documented fallback (``'target'``) and still yields a valid plan. Never
    raises.
    """
    del thesis  # reserved for future thesis-aware refinement; plan is trigger-driven
    kind = classify_resume(trigger_kind)
    plan = _DELTA_RECHECK_PLANS.get(kind, _DELTA_RECHECK_PLANS[_RESUME_FALLBACK])
    return list(plan)


# ── Heartbeat accounting ──────────────────────────────────────────────────────
# The Heartbeat_Monitor is bounded by a maximum number of heartbeats per session
# and each heartbeat counts toward the Session_Budget so it cannot run unbounded
# (Requirement 5.2). ``account_heartbeat`` is the pure accounting helper the
# ``/resume`` heartbeat branch (task 12.1) calls to decide whether another
# heartbeat is within the cap and, if so, to advance the checkpointed counters.


@dataclass(frozen=True)
class HeartbeatAccounting:
    """The pure result of ``account_heartbeat`` (Requirement 5.2).

    Frozen so a computed accounting cannot be mutated by a downstream consumer.

    Fields:
      * ``accepted``        — ``True`` iff another heartbeat was within the cap and
        has been consumed; ``False`` once the ``heartbeat_max`` ceiling is reached
        (no further heartbeat is granted).
      * ``heartbeat_count`` — the updated heartbeat count. Incremented by one on an
        accepted heartbeat; otherwise the prior count clamped to never exceed
        ``heartbeat_max``. GUARANTEED ``<= heartbeat_max`` (the invariant).
      * ``session_turns``   — the updated Session_Budget turn accounting.
        Incremented by one on an accepted heartbeat (a heartbeat counts toward the
        budget); unchanged when the heartbeat is not accepted.
    """

    accepted: bool
    heartbeat_count: int
    session_turns: int


def account_heartbeat(state, cfg: OpportunityConfig) -> HeartbeatAccounting:
    """Pure heartbeat accounting: bound the count and charge the budget (R5.2).

    Reads the checkpointed ``heartbeat_count`` and ``session_turns`` from ``state``
    and the ``heartbeat_max`` ceiling from ``cfg``, then returns the updated counts
    WITHOUT mutating either input (the graph merges the returned values into
    ``AgentState``). Two guarantees hold for ANY input:

      * ``result.heartbeat_count <= heartbeat_max`` — the heartbeat count NEVER
        exceeds the configured maximum (even from a corrupt over-count state, which
        is clamped down), so the Heartbeat_Monitor cannot run unbounded.
      * an ACCEPTED heartbeat increments ``session_turns`` by exactly one, so every
        heartbeat is charged against the Session_Budget (Requirement 5.2).

    Returns ``accepted=False`` (counts unchanged, count clamped to the ceiling) once
    ``heartbeat_count >= heartbeat_max`` — including the ``heartbeat_max == 0`` case,
    where no heartbeat is ever accepted (no heartbeats even if the monitor is
    enabled).

    PURE and TOTAL: a missing / ``None`` / non-numeric ``heartbeat_count`` or
    ``session_turns`` reads 0, and a missing / malformed / negative ``heartbeat_max``
    degrades to the documented default (then floored at 0). Never raises.
    """
    st = state if isinstance(state, dict) else {}
    prior_hb = _coerce_count(st.get("heartbeat_count"))
    prior_turns = _coerce_count(st.get("session_turns"))

    hb_max = _cfg_int(cfg, "heartbeat_max", DEFAULT_HEARTBEAT_MAX)
    if hb_max < 0:
        hb_max = 0  # a negative ceiling is not usable — treat as "no heartbeats"

    if prior_hb >= hb_max:
        # Ceiling reached (or a corrupt over-count) — no further heartbeat. Clamp
        # the reported count down so the invariant heartbeat_count <= heartbeat_max
        # holds even for a corrupt input state.
        return HeartbeatAccounting(False, min(prior_hb, hb_max), prior_turns)

    # Within the ceiling — consume one heartbeat and charge the Session_Budget.
    return HeartbeatAccounting(True, prior_hb + 1, prior_turns + 1)


# ══════════════════════════════════════════════════════════════════════════════
# Deterministic session-context pruning (task 6.1)
# ══════════════════════════════════════════════════════════════════════════════
#
# A bounded hunt that resumes many times accumulates an ever-growing message
# history; left unbounded it bloats context cost and degrades reasoning
# (Requirement 7). ``prune_messages`` bounds that history deterministically while
# preserving the context the defensibility record and Q&A grounding depend on
# (Requirement 7.2): the system instruction, the most-recent usable result of
# every tool seen so far, and the most-recent ``prune_keep_recent_turns`` turns.
#
# This function is PURE and TOTAL: it reads a plain list of message-like objects
# (duck-typed via ``getattr`` — it deliberately does NOT import LangChain so the
# module stays dependency-free like the rest of the pure core) and never raises
# on a malformed / partial / ``None`` list. It returns a SUBSEQUENCE of the input
# (original order preserved) so message/tool-call pairing is never reordered, and
# it prunes at the granularity of whole turn-groups (an assistant tool-call
# message plus its trailing tool results) so a retained tool result is never
# orphaned from the assistant call that issued it (the invariant strict
# OpenAI-compatible providers require — see ``flatten_prior_tool_history`` in
# ``graph.py``). For identical inputs it returns a byte-identical result
# (Requirement 7.3 determinism).


def _msg_type(m) -> Optional[str]:
    """Duck-typed message role: ``'system'`` | ``'human'`` | ``'ai'`` | ``'tool'`` | None.

    Reads the LangChain ``.type`` attribute without importing LangChain. Anything
    without a recognizable string type (e.g. a raw ``("user", text)`` tuple from an
    initial state) reads ``None`` and is treated as an opaque standalone message.
    Total — never raises.
    """
    t = getattr(m, "type", None)
    return t if isinstance(t, str) else None


def _ai_has_tool_calls(m) -> bool:
    """True when an assistant message carries tool calls (typed or raw kwargs)."""
    if getattr(m, "tool_calls", None):
        return True
    raw = (getattr(m, "additional_kwargs", None) or {})
    if isinstance(raw, dict):
        return bool(raw.get("tool_calls"))
    return False


def _msg_tool_name(m) -> Optional[str]:
    """The tool name a ToolMessage answers for, or ``None``."""
    name = getattr(m, "name", None)
    return name if isinstance(name, str) and name.strip() else None


def _group_messages(msgs: list) -> list:
    """Segment messages into ordered turn-groups preserving tool-call pairing.

    Each group is a list of ``(index, message)`` pairs. A ToolMessage attaches to
    the immediately-preceding group when that group's head is an assistant
    tool-call message (so the assistant call and its results stay together);
    otherwise every message forms its own standalone group. Deterministic.
    """
    groups: list = []
    for idx, m in enumerate(msgs):
        if (
            _msg_type(m) == "tool"
            and groups
            and _msg_type(groups[-1][0][1]) == "ai"
            and _ai_has_tool_calls(groups[-1][0][1])
        ):
            groups[-1].append((idx, m))
        else:
            groups.append([(idx, m)])
    return groups


def prune_messages(messages, cfg: OpportunityConfig) -> list:
    """Deterministically bound the session message history (Requirement 7).

    Returns a subsequence of ``messages`` no longer than ``prune_max_messages``
    that retains — as budget allows and in original order — the system message,
    the most-recent usable result of every tool present before pruning, and the
    most-recent ``prune_keep_recent_turns`` turn-groups. Pruning is at whole
    turn-group granularity so a retained tool result is never orphaned from its
    assistant call. Byte-identical on repeated calls with the same inputs
    (Requirement 7.3).

    PURE and TOTAL: a non-list / ``None`` ``messages`` reads as empty; a malformed
    / missing ``cfg`` field degrades to the documented default. Never raises. When
    the history already fits within the ceiling it is returned UNCHANGED.
    """
    msgs = list(messages) if isinstance(messages, (list, tuple)) else []

    max_messages = _cfg_int(cfg, "prune_max_messages", DEFAULT_PRUNE_MAX_MESSAGES)
    if max_messages < _PRUNE_MIN:
        max_messages = DEFAULT_PRUNE_MAX_MESSAGES
    keep_recent = _cfg_int(cfg, "prune_keep_recent_turns", DEFAULT_PRUNE_KEEP_RECENT_TURNS)
    if keep_recent < _PRUNE_MIN:
        keep_recent = DEFAULT_PRUNE_KEEP_RECENT_TURNS

    # Nothing to do when the history already fits (the common case).
    if len(msgs) <= max_messages:
        return msgs

    groups = _group_messages(msgs)
    n = len(groups)

    def _group_type(gi: int) -> Optional[str]:
        return _msg_type(groups[gi][0][1])

    def _flat_len(gis) -> int:
        return sum(len(groups[gi]) for gi in gis)

    # ── Build the keep set: system + latest-per-tool + recent turn-groups ──────
    keep = set()
    for gi in range(n):
        if _group_type(gi) == "system":
            keep.add(gi)

    latest_tool_group: dict = {}
    for gi, g in enumerate(groups):
        for _idx, m in g:
            if _msg_type(m) == "tool":
                name = _msg_tool_name(m)
                if name is not None:
                    latest_tool_group[name] = gi  # a later group overwrites -> latest
    keep.update(latest_tool_group.values())

    recent_start = max(0, n - keep_recent)
    for gi in range(recent_start, n):
        keep.add(gi)

    selected = sorted(keep)

    # Fast path: the full keep set already fits under the ceiling.
    if _flat_len(selected) <= max_messages:
        return [m for gi in selected for (_idx, m) in groups[gi]]

    # ── Over the ceiling: drop oldest OPTIONAL groups first (Requirement 7.1) ──
    # The system group and the recent-turn groups are mandatory (grounding +
    # continuity); the latest-per-tool groups are dropped oldest-first to fit.
    mandatory = {gi for gi in selected if _group_type(gi) == "system"}
    mandatory.update(gi for gi in range(recent_start, n) if gi in keep)

    i = 0
    while _flat_len(selected) > max_messages and i < len(selected):
        gi = selected[i]
        if gi in mandatory:
            i += 1
            continue
        selected.pop(i)

    # Still over (mandatory recent groups alone exceed the ceiling): drop the
    # oldest recent groups too, but never the system group.
    i = 0
    while _flat_len(selected) > max_messages and i < len(selected):
        gi = selected[i]
        if _group_type(gi) == "system":
            i += 1
            continue
        selected.pop(i)

    result = [m for gi in selected for (_idx, m) in groups[gi]]

    # Hard-cap backstop: only reachable when a single indivisible group is itself
    # larger than the ceiling. Tail-slice to guarantee the bound always holds.
    if len(result) > max_messages:
        result = result[-max_messages:]

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Interim Best_Current_Read and the tier tag (task 7.1)
# ══════════════════════════════════════════════════════════════════════════════
#
# Even a hunt that takes no trade should give the trader something actionable
# (Requirement 8): ``best_current_read`` surfaces the current directional bias,
# the key reference levels, and why the engine is standing aside — a NON-COMMITTAL
# assessment drawn ONLY from the real tool evidence, never a committed trade
# (Requirement 8.3). ``tier_tag`` collapses the committed tier into one
# low-cardinality value for the Trade_Journal fingerprint and telemetry
# (Requirement 9.2). Both are PURE and TOTAL — never raise on malformed input.


def best_current_read(evidence, tier_eval) -> dict:
    """A non-committal interim read from current evidence only (Requirement 8).

    Returns ``{"bias", "levels", "why_standing_aside"}`` where ``bias`` is a coarse
    ``'bullish'`` | ``'bearish'`` | ``'neutral'`` derived from the net of the
    confluence signal states and the defensible-triple direction, ``levels`` are
    the finite reference entry/stop/target observed in the evidence (reads, NOT an
    execution plan), and ``why_standing_aside`` cites the tier evaluation's
    rationale. The result NEVER carries a committed-trade ``action``,
    ``execution_plan``, or ``conviction_score`` (Requirement 8.3) — it is an
    assessment, not a trade.

    PURE and TOTAL: a non-dict / ``None`` ``evidence`` reads neutral/empty; a
    ``None`` / malformed ``tier_eval`` degrades to a generic rationale. Never
    raises.
    """
    ev = evidence if isinstance(evidence, dict) else {}

    states = _signal_states(ev)
    net = sum(states.values())

    entry = ev.get("entry")
    target = ev.get("target")
    triple_dir = 0
    if _is_finite_number(entry) and _is_finite_number(target):
        if float(target) > float(entry):
            triple_dir = 1
        elif float(target) < float(entry):
            triple_dir = -1

    score = net + triple_dir
    if score > 0:
        bias = "bullish"
    elif score < 0:
        bias = "bearish"
    else:
        bias = "neutral"

    levels: dict = {}
    for key in ("entry", "stop", "target"):
        val = ev.get(key)
        if _is_finite_number(val):
            levels[key] = float(val)

    why = ""
    rationale = getattr(tier_eval, "rationale", None)
    tier = getattr(tier_eval, "tier", None)
    if isinstance(rationale, str) and rationale.strip():
        why = rationale.strip()
    elif isinstance(tier, str) and tier.strip():
        why = f"Current best available tier: {tier.strip()}."
    if not why:
        why = "No committed trade; interim read derived from current evidence only."

    return {
        "bias": bias,
        "levels": levels,
        "why_standing_aside": why,
    }


# The at-most-five low-cardinality values a committed decision's tier collapses to
# for the Trade_Journal fingerprint / telemetry (Requirement 9.2). ``unknown`` is
# the total fallback for a missing / malformed / unrecognized tier.
TIER_TAG_VALUES = ("a_plus", "b_continuation", "scalp", "stand_aside", "unknown")

_KNOWN_TIER_TAGS = frozenset({"a_plus", "b_continuation", "scalp", "stand_aside"})


def tier_tag(decision) -> str:
    """Collapse a committed decision's Opportunity_Tier to a low-cardinality tag.

    Reads ``decision["opportunity_tier"]`` and returns one of ``TIER_TAG_VALUES``.
    A missing / non-dict / non-string / unrecognized tier degrades to ``'unknown'``
    (Requirement 9.2), so the tag is always one of at most five values and is
    deterministic for identical inputs.

    PURE and TOTAL — never raises.
    """
    d = decision if isinstance(decision, dict) else {}
    tier = d.get("opportunity_tier")
    if isinstance(tier, str):
        token = tier.strip().lower()
        if token in _KNOWN_TIER_TAGS:
            return token
    return "unknown"
