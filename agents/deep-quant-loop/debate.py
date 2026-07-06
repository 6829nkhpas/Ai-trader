"""Pure debate core for the Multi-Agent Bull/Bear Debate (DEBATE mode).

Feature: multi-agent-debate

This module concentrates the deterministic, side-effect-free core of the
debate so it can be unit/property-tested in isolation, with no LLM calls and no
graph wiring. Task 1.1 establishes the foundation:

  * the consensus / lean enumerations,
  * the tunable threshold + weight constants used by the (later) consensus and
    conviction logic, and
  * the environment-driven ``DebateConfig`` plus ``resolve_debate_config()``,
    which applies documented defaults on unset/empty/whitespace/non-numeric/
    out-of-range values **without ever raising** (Requirements 6.1-6.5).

The stance model, consensus classification, and conviction derivation are added
by later tasks; this file is intentionally limited to the configuration core.

Environment parsing mirrors the conventions already used across the agent
(``regime.py``/``session.py`` ``_resolve_int``-style helpers and ``graph.py``'s
``_env_nonempty``): an empty/whitespace value is treated as "unset", and any
unparseable or out-of-range value silently falls back to the documented default.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional

# ── Enumerations ──────────────────────────────────────────────────────────────
# The Debate_Consensus is exactly one of these three categorical values; the
# Debate_Stance directional lean is exactly one of DEBATE_LEANS.
DEBATE_CONSENSUS_VALUES = ("strong_agree", "lean", "contested")
DEBATE_LEANS = ("long", "short", "neutral")

# ── Consensus-classification thresholds (consumed by later tasks) ─────────────
# Strengths are integers in [0, 100]. With hi = max(bull, bear) strength,
# lo = min(...), and gap = hi - lo:
#   * contested    -> lo  >= STRONG_FLOOR  AND gap <= CONTESTED_GAP
#   * strong_agree -> gap >= STRONG_GAP    AND hi  >= STRONG_FLOOR
#   * lean         -> everything else
# STRONG_GAP (30) > CONTESTED_GAP (15) guarantees the contested and strong_agree
# regions are mutually exclusive, so the classifier is total and unambiguous.
STRONG_FLOOR = 60       # a stance at/above this is "strong"
STRONG_GAP = 30         # gap at/above this makes one side clearly dominant
CONTESTED_GAP = 15      # gap at/below this (both strong) makes the debate contested

# ── Conviction-derivation weights (consumed by later tasks) ───────────────────
# conviction = clamp(round(W_BASE*winning_strength + W_SEP*separation)
#                    - (CONTESTED_PENALTY if consensus == "contested" else 0),
#                    0, 100)
# W_BASE + W_SEP == 1.0 keeps the unpenalized term within [0, 100] before
# clamping; CONTESTED_PENALTY explicitly attenuates a contested consensus so it
# is strictly less convicted than strong_agree over comparable evidence (R4.4).
W_BASE = 0.7
W_SEP = 0.3
CONTESTED_PENALTY = 25

# ── Round / turn bounds ───────────────────────────────────────────────────────
# DEBATE_ROUNDS is clamped to [1, MAX_ROUNDS]. One round is a Bull-then-Bear
# exchange (TURNS_PER_ROUND model turns); the Judge adds JUDGE_TURNS. The default
# global turn bound is derived from the configured rounds; an explicit
# DEBATE_MAX_TURNS override is honoured only when it is large enough to run the
# configured rounds and no larger than MAX_TURNS_CAP. These bounds guarantee the
# debate always terminates (R6.2).
MAX_ROUNDS = 5
TURNS_PER_ROUND = 2     # Bull + Bear per round
JUDGE_TURNS = 1         # the Judge's synthesis turn
MAX_TURNS_CAP = MAX_ROUNDS * TURNS_PER_ROUND + JUDGE_TURNS  # absolute upper bound

# ── Judge targeted-tool-call budget ───────────────────────────────────────────
# The Judge may issue at most this many targeted read-only analysis-tool calls
# before declaring, bounded so the debate always terminates (R2.4).
JUDGE_MAX_TOOL_CALLS_DEFAULT = 2
JUDGE_MAX_TOOL_CALLS_CAP = 5

# ── Documented defaults ───────────────────────────────────────────────────────
DEFAULT_ROUNDS = 1

# Environment variable names.
ENV_DEBATE_ROUNDS = "DEBATE_ROUNDS"
ENV_DEBATE_MAX_TURNS = "DEBATE_MAX_TURNS"
ENV_DEBATE_JUDGE_MAX_TOOL_CALLS = "DEBATE_JUDGE_MAX_TOOL_CALLS"
ENV_DEBATE_BULL_MODEL = "DEBATE_BULL_MODEL"
ENV_DEBATE_BEAR_MODEL = "DEBATE_BEAR_MODEL"
ENV_DEBATE_JUDGE_MODEL = "DEBATE_JUDGE_MODEL"

# System default model resolution (mirrors graph.py so debate.py stays free of a
# circular import on the graph module). When the caller does not pass an explicit
# system default, the model is resolved from LLM_MODEL with the same Gemini
# fallback graph.py uses.
ENV_SYSTEM_MODEL = "LLM_MODEL"
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"


@dataclass(frozen=True)
class DebateConfig:
    """Resolved, immutable debate configuration.

    Every field is guaranteed in-range by ``resolve_debate_config`` regardless of
    the raw environment, so downstream code can rely on the invariants without
    re-validating:

      * ``rounds`` in ``[1, MAX_ROUNDS]``
      * ``max_turns`` large enough for ``rounds`` and ``<= MAX_TURNS_CAP``
      * ``judge_max_tool_calls`` in ``[0, JUDGE_MAX_TOOL_CALLS_CAP]``
      * each role model is a non-empty string (the env value when valid, else the
        system default model)
    """

    rounds: int
    max_turns: int
    judge_max_tool_calls: int
    bull_model: str
    bear_model: str
    judge_model: str


def _env_str(env_name: str) -> Optional[str]:
    """Return the stripped env value, or ``None`` when unset/empty/whitespace.

    Mirrors the ``_env_nonempty`` semantics in ``graph.py``: a blank value (e.g.
    ``DEBATE_BULL_MODEL=`` left empty in .env) is treated as "unset".
    """
    raw = os.getenv(env_name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped if stripped else None


def _resolve_int_in_range(env_name: str, default: int, low: int, high: int) -> int:
    """Resolve one integer from its env var, clamped to ``[low, high]``.

    Falls back to ``default`` when the var is unset/empty/whitespace, cannot be
    parsed as an int, or parses but falls outside ``[low, high]``. Never raises.
    Modeled on the ``_resolve_int`` helpers in ``regime.py``/``session.py``.
    """
    raw = _env_str(env_name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return default
    if value < low or value > high:
        return default
    return value


def _derived_max_turns(rounds: int) -> int:
    """The default global turn bound derived from the configured rounds (R6.2)."""
    return rounds * TURNS_PER_ROUND + JUDGE_TURNS


def _resolve_role_model(env_name: str, default_model: str) -> str:
    """Resolve a per-role model to the env value when valid, else the default.

    Per R6.3/R6.4 an unset/empty/whitespace per-role variable falls back to the
    system default model without raising.
    """
    value = _env_str(env_name)
    return value if value is not None else default_model


def resolve_debate_config(default_model: Optional[str] = None) -> DebateConfig:
    """Resolve the full debate configuration from the environment.

    Total and never-raising (R6.5): unset/empty/whitespace/non-numeric/out-of-range
    values all degrade to documented defaults.

      * ``DEBATE_ROUNDS``               -> default 1, clamped to ``[1, MAX_ROUNDS]``
      * ``DEBATE_MAX_TURNS``            -> default derived from rounds; an override
        is honoured only when it is in ``[derived, MAX_TURNS_CAP]`` (an override
        too small to run the configured rounds reverts to the derived default)
      * ``DEBATE_JUDGE_MAX_TOOL_CALLS`` -> default 2, clamped to
        ``[0, JUDGE_MAX_TOOL_CALLS_CAP]``
      * ``DEBATE_BULL_MODEL`` / ``DEBATE_BEAR_MODEL`` / ``DEBATE_JUDGE_MODEL``
        -> the env value when set & non-empty, else ``default_model``

    Args:
        default_model: the system's configured model used as the per-role
            fallback. When ``None`` it is resolved from ``LLM_MODEL`` with the
            same Gemini fallback ``graph.py`` uses, so callers (and tests) may
            either pass the resolved system model or rely on the environment.
    """
    if default_model is None or not str(default_model).strip():
        default_model = _env_str(ENV_SYSTEM_MODEL) or GEMINI_DEFAULT_MODEL
    else:
        default_model = str(default_model).strip()

    rounds = _resolve_int_in_range(ENV_DEBATE_ROUNDS, DEFAULT_ROUNDS, 1, MAX_ROUNDS)

    # max_turns must be large enough to actually run the configured rounds, so
    # the lower bound is the derived default for this many rounds.
    derived = _derived_max_turns(rounds)
    max_turns = _resolve_int_in_range(
        ENV_DEBATE_MAX_TURNS, derived, derived, MAX_TURNS_CAP
    )

    judge_max_tool_calls = _resolve_int_in_range(
        ENV_DEBATE_JUDGE_MAX_TOOL_CALLS,
        JUDGE_MAX_TOOL_CALLS_DEFAULT,
        0,
        JUDGE_MAX_TOOL_CALLS_CAP,
    )

    return DebateConfig(
        rounds=rounds,
        max_turns=max_turns,
        judge_max_tool_calls=judge_max_tool_calls,
        bull_model=_resolve_role_model(ENV_DEBATE_BULL_MODEL, default_model),
        bear_model=_resolve_role_model(ENV_DEBATE_BEAR_MODEL, default_model),
        judge_model=_resolve_role_model(ENV_DEBATE_JUDGE_MODEL, default_model),
    )


# ── Stance bounds ─────────────────────────────────────────────────────────────
# A Debate_Stance strength is an integer in this inclusive range; everything is
# clamped into it so downstream consensus/conviction logic can rely on the bound.
STRENGTH_MIN = 0
STRENGTH_MAX = 100

# The default lean applied when the role's raw output carries no recognizable
# directional lean. "neutral" is the safe, non-committal default (R3.3).
DEFAULT_LEAN = "neutral"


@dataclass(frozen=True)
class DebateStance:
    """A role's structured debate output (Requirement 3.3).

    Immutable so a parsed stance can be threaded through the graph state and the
    defensibility record without risk of mutation. Every field is normalized by
    ``parse_stance`` so consumers can rely on the invariants without re-checking:

      * ``role``         -- the producing role, lower-cased ("bull" | "bear" | ...)
      * ``lean``         -- exactly one of ``DEBATE_LEANS`` (else ``DEFAULT_LEAN``)
      * ``strength``     -- an int clamped to ``[STRENGTH_MIN, STRENGTH_MAX]``
      * ``arguments``    -- a list of non-empty argument strings
      * ``biggest_risk`` -- a string (possibly empty) describing the single
        biggest risk to this role's own thesis
      * ``available``    -- ``False`` when the role failed to produce a usable
        stance (garbled/empty/missing output), so the Judge can proceed on the
        remaining evidence without a fabricated stance (R12.2)
    """

    role: str
    lean: str
    strength: int
    arguments: List[str]
    biggest_risk: str
    available: bool


def _coerce_to_mapping(raw: Any) -> Optional[Mapping[str, Any]]:
    """Best-effort coercion of an arbitrary role output into a mapping.

    Accepts a mapping directly, a JSON object string, or an arbitrary object with
    a ``__dict__`` (e.g. a namespace or dataclass instance). Returns ``None`` for
    ``None``, empty/whitespace strings, non-object JSON, and anything else that
    cannot defensibly be read as a key/value structure. Never raises.
    """
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, Mapping) else None
    # Arbitrary object: read its attribute namespace if it has one.
    namespace = getattr(raw, "__dict__", None)
    if isinstance(namespace, Mapping) and namespace:
        return namespace
    return None


def _coerce_lean(value: Any) -> str:
    """Constrain ``value`` to one of ``DEBATE_LEANS``, else ``DEFAULT_LEAN``."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in DEBATE_LEANS:
            return normalized
    return DEFAULT_LEAN


def _coerce_strength(value: Any) -> int:
    """Clamp ``value`` to an int in ``[STRENGTH_MIN, STRENGTH_MAX]``.

    Accepts ints, floats, and numeric strings; anything unparseable (including
    ``None``, booleans, and non-numeric strings) falls back to ``STRENGTH_MIN``.
    Never raises.
    """
    # bool is a subclass of int but is never a meaningful strength score.
    if isinstance(value, bool):
        return STRENGTH_MIN
    number: Optional[float]
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except (ValueError, TypeError):
            number = None
    else:
        number = None
    if number is None:
        return STRENGTH_MIN
    clamped = max(float(STRENGTH_MIN), min(float(STRENGTH_MAX), number))
    return int(round(clamped))


def _coerce_arguments(value: Any) -> List[str]:
    """Coerce ``value`` into a list of non-empty argument strings.

    A list/tuple is mapped element-wise to stripped strings (empties dropped); a
    lone non-empty string becomes a single-element list; anything else yields an
    empty list. Never raises.
    """
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        result: List[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                result.append(text)
        return result
    return []


def _coerce_biggest_risk(value: Any) -> str:
    """Coerce ``value`` to a stripped string; non-strings are stringified."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def parse_stance(role: str, raw: Any) -> DebateStance:
    """Best-effort parse of a role's structured output into a ``DebateStance``.

    ``raw`` may be a mapping, a JSON object string, an arbitrary object, or
    ``None`` -- it is parsed defensively and never raises. Normalization:

      * ``strength`` is clamped to ``[STRENGTH_MIN, STRENGTH_MAX]``
      * ``lean`` is constrained to ``DEBATE_LEANS`` (else ``DEFAULT_LEAN``)
      * ``arguments`` is coerced to a list of strings
      * ``biggest_risk`` is coerced to a string

    Garbled/empty/missing output yields ``available == False`` rather than a
    fabricated stance, so the Judge can proceed on the remaining evidence
    (R12.2). When the mapping carries an explicit boolean ``available`` it is
    honoured (so ``stance_to_dict`` round-trips exactly); otherwise availability
    is inferred from whether any usable field was present.
    """
    role_norm = str(role).strip().lower() if role is not None else ""
    data = _coerce_to_mapping(raw)
    if data is None:
        return DebateStance(
            role=role_norm,
            lean=DEFAULT_LEAN,
            strength=STRENGTH_MIN,
            arguments=[],
            biggest_risk="",
            available=False,
        )

    lean = _coerce_lean(data.get("lean"))
    strength = _coerce_strength(data.get("strength"))
    arguments = _coerce_arguments(data.get("arguments"))
    biggest_risk = _coerce_biggest_risk(data.get("biggest_risk"))

    explicit_available = data.get("available")
    if isinstance(explicit_available, bool):
        available = explicit_available
    else:
        # Infer: a stance is usable when the raw output carried at least one
        # recognizable, content-bearing field.
        recognized_lean = (
            isinstance(data.get("lean"), str)
            and data.get("lean", "").strip().lower() in DEBATE_LEANS
        )
        recognized_strength = not isinstance(data.get("strength"), bool) and (
            isinstance(data.get("strength"), (int, float))
            or (
                isinstance(data.get("strength"), str)
                and _is_number(data.get("strength"))
            )
        )
        available = bool(
            arguments or biggest_risk or recognized_lean or recognized_strength
        )

    return DebateStance(
        role=role_norm,
        lean=lean,
        strength=strength,
        arguments=arguments,
        biggest_risk=biggest_risk,
        available=available,
    )


def _is_number(text: str) -> bool:
    """Return whether ``text`` parses as a float, without raising."""
    try:
        float(text.strip())
    except (ValueError, TypeError):
        return False
    return True


def stance_to_dict(stance: DebateStance) -> dict:
    """Serialize a ``DebateStance`` to a plain dict that round-trips.

    ``parse_stance(stance.role, stance_to_dict(stance))`` reproduces a stance
    with the same ``lean``, ``strength``, ``arguments``, ``biggest_risk``, and
    ``available`` as ``stance`` (Property 5 / R3.3). The dict is JSON-serializable
    so it can be stored in the graph state and the defensibility record.
    """
    return {
        "role": stance.role,
        "lean": stance.lean,
        "strength": stance.strength,
        "arguments": list(stance.arguments),
        "biggest_risk": stance.biggest_risk,
        "available": stance.available,
    }


# ── Consensus classification + conviction derivation ──────────────────────────
# These three pure functions are the deterministic heart of the debate. They are
# total over every ``DebateStance`` (including unavailable stances, which are
# treated as strength ``0`` per R12.2) and never raise, so the Judge can rely on
# them without re-validating their output.


def _clamp_int(value: int, low: int, high: int) -> int:
    """Clamp ``value`` into ``[low, high]`` and return it as an int. Never raises."""
    if value < low:
        return low
    if value > high:
        return high
    return int(value)


def _effective_strength(stance: Any) -> int:
    """The strength used for consensus/conviction, clamped to ``[0, 100]``.

    An unavailable (or missing/garbled) stance is treated as strength ``0`` so a
    role that failed to produce a usable stance cannot inflate the verdict (R12.2).
    Defensive against arbitrary objects so the callers stay total.
    """
    if stance is None or not getattr(stance, "available", False):
        return STRENGTH_MIN
    raw = getattr(stance, "strength", STRENGTH_MIN)
    try:
        return _clamp_int(int(raw), STRENGTH_MIN, STRENGTH_MAX)
    except (ValueError, TypeError):
        return STRENGTH_MIN


def classify_consensus(bull: DebateStance, bear: DebateStance) -> str:
    """Classify the disagreement structure as one of ``DEBATE_CONSENSUS_VALUES``.

    Deterministic threshold rule over the two clamped strengths (an unavailable
    stance counts as ``0``). With ``hi = max``, ``lo = min``, ``gap = hi - lo``:

      * ``contested``    -> ``lo >= STRONG_FLOOR`` and ``gap <= CONTESTED_GAP``
        (both stances strong and close)
      * ``strong_agree`` -> ``gap >= STRONG_GAP`` and ``hi >= STRONG_FLOOR``
        (one stance clearly dominates)
      * ``lean``         -> everything else (a mild edge to one side)

    Because ``STRONG_GAP`` (30) > ``CONTESTED_GAP`` (15), the contested and
    strong_agree regions are disjoint, so the result is unambiguous and the
    function is total: it always returns exactly one enum value (R4.1-R4.3).
    """
    b = _effective_strength(bull)
    r = _effective_strength(bear)
    hi = max(b, r)
    lo = min(b, r)
    gap = hi - lo
    if lo >= STRONG_FLOOR and gap <= CONTESTED_GAP:
        return "contested"
    if gap >= STRONG_GAP and hi >= STRONG_FLOOR:
        return "strong_agree"
    return "lean"


def derive_conviction(bull: DebateStance, bear: DebateStance, consensus: str) -> int:
    """Derive the Judge's Conviction in ``[0, 100]`` from the two stances.

    Conviction increases with the winning side's dominance: it is a weighted sum
    of the winning strength (``base``) and how one-sided the debate is
    (``separation``), then clamped::

        base       = clamp(max(b, r), 0, 100)
        separation = clamp(gap,       0, 100)
        conviction = clamp(round(W_BASE*base + W_SEP*separation)
                           - (CONTESTED_PENALTY if consensus == "contested" else 0),
                           0, 100)

    Because ``W_BASE + W_SEP == 1`` the unpenalized term stays within ``[0, 100]``,
    and the explicit ``CONTESTED_PENALTY`` attenuation makes a ``contested``
    verdict strictly less convicted than ``strong_agree`` over equal strengths
    whenever there is any winning-side strength to convict on (R4.4). The function
    is total and never raises.
    """
    b = _effective_strength(bull)
    r = _effective_strength(bear)
    hi = max(b, r)
    gap = hi - min(b, r)
    base = _clamp_int(hi, 0, 100)
    separation = _clamp_int(gap, 0, 100)
    raw = W_BASE * base + W_SEP * separation
    penalty = CONTESTED_PENALTY if consensus == "contested" else 0
    return _clamp_int(int(round(raw)) - penalty, 0, 100)


def judge_directional_bias(
    bull: DebateStance, bear: DebateStance, consensus: str
) -> str:
    """Suggest an advisory directional bias (``"long"`` / ``"short"`` / ``"hold"``).

    This is advisory only — the Judge still validates any committed trade. The
    rule (R4.3):

      * a ``contested`` consensus always biases to ``"hold"`` (caution / smaller
        size), regardless of strengths;
      * otherwise the side with the strictly higher effective strength and a
        directional lean wins (its ``long``/``short`` lean maps through);
      * a tie in strength, or a winning side whose lean is ``neutral``, yields
        ``"hold"``.

    Total over all stances (unavailable -> strength ``0``); never raises.
    """
    if consensus == "contested":
        return "hold"
    b = _effective_strength(bull)
    r = _effective_strength(bear)
    if b > r:
        winner = bull
    elif r > b:
        winner = bear
    else:
        return "hold"
    lean = getattr(winner, "lean", DEFAULT_LEAN)
    if lean == "long":
        return "long"
    if lean == "short":
        return "short"
    return "hold"
