"""Session_Telemetry — non-invasive, best-effort measurement of Deep Quant runs.

The Trade_Journal records the trades the agent *commits*, but it is blind to
everything that happens on the way there — the analyses that never convert, the
watch/invalidate cycles a hunt burns, how long a decision takes, and how much a
run costs. Session Telemetry closes that gap. It records the full lifecycle of
every analysis run — a **Session** spanning a ``/run`` and all of its ``/resume``
continuations for a ``thread_id`` — into a dedicated SQLite store, and aggregates
those records into a **Telemetry_Report** (conversion rate, invalidation rate,
watch cycles, time-to-decision, cost proxies, broken down by symbol / timeframe /
mode, with weak-prior flagging).

Telemetry is **measurement only**. It observes the run's existing event lifecycle
(the SSE stream and the ``/run`` / ``/resume`` entry points), is read-only with
respect to the Trade_Journal, never influences a trade decision, and is
best-effort: a telemetry failure must never raise into or slow the agent loop
(Requirement 6, 10).

The module deliberately mirrors the conventions already established across the
deep-quant-loop: a pure numeric/aggregation core over in-memory records (like
``attribution.py`` / ``calibration.py``), a thin defensive I/O layer (a
passthrough observation tee, a background writer, a dedicated SQLite store, and a
read-only loader), config-from-env with documented defaults via the
``_resolve_int`` / ``_resolve_float`` / ``_resolve_bool`` helper convention, and
an ``argparse`` CLI mirroring ``backtest.py`` / ``attribution.py``.

This file (task 1.1) provides the configuration foundation: the documented
default constants, the environment-variable names, the frozen ``TelemetryConfig``
dataclass, and ``resolve_telemetry_config()``. The data models, pure
interpretation / classification / finalization / aggregation core, the SQLite
store, the recording layer (observation tee + background writer), and the CLI are
added in subsequent tasks.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import sqlite3
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

# ── Documented default configuration ──────────────────────────────────────────
# Applied whenever a parameter's env var is unset / empty / whitespace /
# unparseable / out of range (Requirement 9.2). Single source of truth for the
# defaults on both the recording path and the CLI report path.

DEFAULT_WEAK_PRIOR_MIN_SESSIONS = 20            # below this per group => weak prior (R4.6, R5.2)
DEFAULT_INCOMPLETE_HORIZON_SECONDS = 24 * 3600  # paused longer than this => incomplete (R1.5)
DEFAULT_QUEUE_MAXSIZE = 10000                   # bounded observation queue (drop-on-full)

# ── Environment variable names ────────────────────────────────────────────────
ENV_TELEMETRY_DB_PATH = "TELEMETRY_DB_PATH"
ENV_WEAK_PRIOR_MIN_SESSIONS = "TELEMETRY_WEAK_PRIOR_MIN_SESSIONS"
ENV_INCOMPLETE_HORIZON = "TELEMETRY_INCOMPLETE_HORIZON_SECONDS"

# ── Store path default ────────────────────────────────────────────────────────
# The store defaults to ``telemetry.db`` beside this module and is overridable via
# ``TELEMETRY_DB_PATH`` (Requirements 7.3, 9.1). The default filename is covered by
# the existing ``*.db`` git-ignore rule, so the store is git-ignored like the trade
# journal without a new rule.
_DEFAULT_DB = os.path.join(os.path.abspath(os.path.dirname(__file__)), "telemetry.db")

# ── Valid ranges ──────────────────────────────────────────────────────────────
# The weak-prior minimum is an integer >= 1 (a zero minimum would flag nothing as
# a weak prior). The incomplete horizon is a strictly positive number of seconds
# (a zero/negative horizon would classify everything as incomplete immediately);
# it therefore has an EXCLUSIVE lower bound of 0.0 (Requirement 9.2).
_MIN_SESSIONS_MIN = 1
_HORIZON_LOW = 0.0      # EXCLUSIVE lower bound (see resolve_telemetry_config)


@dataclass(frozen=True)
class TelemetryConfig:
    """The resolved, validated configuration for recording and reporting.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the pure core's purity guarantee, Requirement 8). For
    identical environment-variable values the resolved configuration is identical
    on repeated runs (Requirement 8.2).
    """

    db_path: str                       # Telemetry_Store path (dedicated SQLite file)
    weak_prior_min_sessions: int       # below this per group => weak prior; >= 1
    incomplete_horizon_seconds: float  # paused longer than this => incomplete; > 0


def _resolve_int(env_name: str, default: int, low: int) -> int:
    """Resolve one integer parameter from its own env var (Requirement 9.1-9.2).

    Falls back to ``default`` when the var is unset/empty/whitespace, cannot be
    parsed as an int, or parses but is below ``low`` (the minimum valid value).
    Never raises. Mirrors the ``_resolve_int`` convention in ``attribution.py`` /
    ``regime.py`` / ``rs.py``.
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
    """Resolve one float parameter from its own env var (Requirement 9.1-9.2).

    Falls back to ``default`` when the var is unset/empty/whitespace, cannot be
    parsed as a float, is non-finite (NaN/inf), or parses but falls outside the
    inclusive band ``[low, high]``. Never raises. Mirrors the ``_resolve_float``
    convention in ``attribution.py`` / ``regime.py`` / ``order_flow.py``.
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
    """Resolve one boolean flag from its own env var (Requirement 9.1-9.2).

    Falls back to ``default`` when the var is unset/empty/whitespace or carries a
    token outside the recognized truthy/falsy spellings. Never raises. Parsing is
    case-insensitive and whitespace-tolerant. Provided for parity with the
    ``attribution.py`` env-resolution convention and for forward-compatible flags.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    token = raw.strip().lower()
    if token in ("1", "true", "yes", "on"):
        return True
    if token in ("0", "false", "no", "off"):
        return False
    return default


def _resolve_str(env_name: str, default: str) -> str:
    """Resolve one string parameter from its own env var (Requirement 9.1-9.2).

    Falls back to ``default`` when the var is unset/empty/whitespace; otherwise
    returns the stripped value. Never raises. Used for the Telemetry_Store path.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def resolve_telemetry_config() -> TelemetryConfig:
    """Resolve every parameter from its own env var with documented defaults.

    Per-parameter rules (Requirement 9):
      * unset / empty / whitespace  -> documented default
      * unparseable as its type     -> documented default (never raises)
      * parses but out of range     -> documented default (never raises)

    ``incomplete_horizon_seconds`` has an EXCLUSIVE lower bound of 0: a zero (or
    negative) horizon would classify every still-open session as ``incomplete``
    immediately and is not a usable parameter. ``_resolve_float`` only enforces an
    inclusive band, so it is resolved with an inclusive low of 0.0 and the
    boundary value 0.0 (the only way a non-negative value can sit at the exclusive
    bound) is reverted to the documented default — clamping the result to
    ``(0.0, inf)`` (mirrors the ``attribution`` / ``trade_manager`` exclusive-bound
    convention).

    Identical environments resolve to identical configuration (Requirement 8.2).
    This function NEVER raises.
    """
    db_path = _resolve_str(ENV_TELEMETRY_DB_PATH, _DEFAULT_DB)

    weak_prior_min_sessions = _resolve_int(
        ENV_WEAK_PRIOR_MIN_SESSIONS,
        DEFAULT_WEAK_PRIOR_MIN_SESSIONS,
        _MIN_SESSIONS_MIN,
    )

    incomplete_horizon_seconds = _resolve_float(
        ENV_INCOMPLETE_HORIZON,
        DEFAULT_INCOMPLETE_HORIZON_SECONDS,
        _HORIZON_LOW,
        math.inf,
    )
    # Clamp to the open interval (0.0, inf): a resolved 0.0 sits on the exclusive
    # lower bound and is not usable, so revert it to the documented default.
    if incomplete_horizon_seconds <= _HORIZON_LOW:
        incomplete_horizon_seconds = DEFAULT_INCOMPLETE_HORIZON_SECONDS

    return TelemetryConfig(
        db_path=db_path,
        weak_prior_min_sessions=weak_prior_min_sessions,
        incomplete_horizon_seconds=incomplete_horizon_seconds,
    )


# ── Session_Outcome enumeration ───────────────────────────────────────────────
# The terminal classification of a Session is exactly one of these (Requirement
# 1.4). ``incomplete`` covers a Session that ended without a terminal decision
# (abandoned, or still paused past the configured horizon). A ``hold`` carries a
# ``hold_reason`` sub-classification (see HOLD_REASONS below).
OUTCOME_TRADE_BUY = "trade_buy"
OUTCOME_TRADE_SELL = "trade_sell"
OUTCOME_HOLD = "hold"
OUTCOME_ERROR = "error"
OUTCOME_INCOMPLETE = "incomplete"

SESSION_OUTCOMES = (
    OUTCOME_TRADE_BUY,
    OUTCOME_TRADE_SELL,
    OUTCOME_HOLD,
    OUTCOME_ERROR,
    OUTCOME_INCOMPLETE,
)

# ── hold_reason sub-classification (only meaningful for a ``hold`` outcome) ─────
# Derived purely from the decision record; defaults to ``voluntary`` when no
# forced/gated marker is present (Requirement 1.4).
HOLD_VOLUNTARY = "voluntary"
HOLD_FORCED = "forced"
HOLD_DATA_GATED = "data-gated"

HOLD_REASONS = (HOLD_VOLUNTARY, HOLD_FORCED, HOLD_DATA_GATED)

# ── trigger_kind for resume Funnel_Events ─────────────────────────────────────
# A ``/resume`` continuation is tagged with the kind of tripwire that fired
# (Requirement 2.2).
TRIGGER_TARGET = "target"
TRIGGER_INVALIDATION = "invalidation"

# ── Funnel_Event kinds (the ordered lifecycle vocabulary, Requirement 2.4) ─────
FUNNEL_SESSION_STARTED = "session_started"
FUNNEL_WATCH_REGISTERED = "watch_registered"
FUNNEL_RESUMED = "resumed"
FUNNEL_REASONING_TURN = "reasoning_turn"
FUNNEL_TOOL_CALL = "tool_call"
FUNNEL_DECISION = "decision"
FUNNEL_ERROR = "error"

# The tool whose registration constitutes a Watch_Cycle (Requirement 2.1).
WATCH_TOOL_NAME = "watch_price_condition"


@dataclass(frozen=True)
class RunEntry:
    """The identity/entry metadata for one ``/run`` or ``/resume`` observation.

    ``observe_stream`` receives a ``RunEntry`` alongside the SSE frame source; it
    supplies the Session identity captured off the run's existing entry points
    (Requirement 1.1, 2.5). ``trigger_kind`` is set only on ``/resume`` entries and
    tags the resume as a Target_Event or an Invalidation_Event (Requirement 2.2).

    Frozen: an entry is an immutable observation input to the pure core.
    """

    kind: str                             # "run" | "resume"
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    mode: Optional[str] = None
    trigger_kind: Optional[str] = None    # "target" | "invalidation" (resume only)


@dataclass(frozen=True)
class FunnelEvent:
    """One recorded lifecycle event within a Session, in observation order.

    The ordered list of Funnel_Events reconstructs a run's path
    (analyze -> watch -> invalidate -> re-watch -> ...) exactly (Requirement 2.4).
    ``seq`` is the 0-based, contiguous position of this event within its Session.
    ``ts`` is the wall-clock time (seconds) when the event was observed, when that
    is available. ``extra`` is a forward-compatible JSON-serializable bag reserved
    for the Adaptive Opportunity Engine (tier / budget events).

    Frozen: a derived funnel event is an immutable product of the pure core.
    """

    seq: int                                    # 0-based order within the Session (R2.4)
    kind: str                                   # one of the FUNNEL_* kinds
    ts: Optional[float] = None                  # wall-clock seconds when observed
    trigger_kind: Optional[str] = None          # target | invalidation (resumed events)
    tool_name: Optional[str] = None             # for watch_registered / tool_call
    extra: Optional[Dict[str, Any]] = None      # forward-compat, JSON-serialized to `extra`


@dataclass(frozen=True)
class SessionRecord:
    """The persisted, immutable summary of one Session (Requirement 1, 2, 3).

    Produced by ``finalize_session`` folding an accumulated ``SessionState`` into
    an immutable value. ``session_id`` is the surrogate ``f"{thread_id}:{started_at}"``
    that distinguishes a fresh ``/run`` on a reused thread_id from an earlier,
    already-closed Session. Fields left ``None`` while the Session is still open
    (``ended_at``, ``outcome``, ``time_to_decision_s``) drive the ``incomplete``
    classification during aggregation. ``tokens`` is ``None`` when the run exposed
    no token usage — it is never fabricated (Requirement 3.4).

    Frozen: a finalized record is immutable so the pure aggregation core cannot
    observe or cause mutation (Requirement 8.4).
    """

    session_id: str                    # surrogate: f"{thread_id}:{started_at}"
    thread_id: str
    symbol: Optional[str]
    timeframe: Optional[str]
    mode: Optional[str]
    started_at: float
    ended_at: Optional[float]          # None while still open
    outcome: Optional[str]             # Session_Outcome; None while open (=> incomplete-eligible)
    hold_reason: Optional[str]         # voluntary | forced | data-gated (only for hold)

    # Funnel counters (Requirement 2.3)
    watch_cycles: int
    target_events: int
    invalidation_events: int
    resume_count: int
    reasoning_turns: int

    # Cost proxies (Requirement 3.3, 3.4)
    tool_calls_total: int
    tool_calls_by_name: Dict[str, int]  # {tool_name: count}
    model_turns: int
    tokens: Optional[int]               # None when the run did not expose token usage (R3.4)

    # Timing (Requirement 3.1, 3.2)
    time_to_decision_s: Optional[float]  # ended_at - started_at; None while open
    suspended_s: Optional[float]         # total time in watch cycles when observable, else None

    # Ordered funnel path (Requirement 2.4)
    funnel: List[FunnelEvent]            # FunnelEvents in seq order

    # ── Adaptive Opportunity Engine measurement (adaptive-opportunity-engine R9.3)
    # Additive, defaulted fields persisted to the forward-compatible columns
    # (``opportunity_tier`` / ``session_budget``) and the generic ``extra`` JSON bag
    # (which carries the termination reason + heartbeat usage). All None for a run
    # without the engine — never fabricated (R3.4).
    opportunity_tier: Optional[str] = None
    session_budget: Optional[float] = None
    extra: Optional[Dict[str, Any]] = None


@dataclass
class SessionState:
    """The MUTABLE accumulator the writer builds up before finalization.

    Distinct from the frozen ``SessionRecord``: the background ``SessionWriter``
    drains observations for a ``thread_id`` and folds them into a ``SessionState``,
    which ``finalize_session`` then converts into an immutable ``SessionRecord``
    (it reads this state without mutating it, preserving purity — Requirement 8.4).

    Holds the accumulating identity/entry metadata, the funnel counters, the cost
    proxies, the timings, the per-tool counts, and the ordered list of derived
    ``FunnelEvent``s. ``tokens`` stays ``None`` unless the run exposes a real token
    count (never fabricated — Requirement 3.4). ``watch_starts`` tracks the
    wall-clock timestamps at which watch cycles began so ``finalize_session`` can
    sum observable watch->resume suspend intervals (Requirement 3.2); it is
    bookkeeping and does not itself appear on the finalized record.

    NOT frozen: it is intentionally mutable working state (this is the one mutable
    model in the module).
    """

    # Identity / entry metadata (Requirement 1.1)
    thread_id: str
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    mode: Optional[str] = None

    # Timings (Requirement 3.1, 3.2)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None

    # Terminal classification (Requirement 1.3, 1.4)
    outcome: Optional[str] = None
    hold_reason: Optional[str] = None

    # Funnel counters (Requirement 2.3)
    watch_cycles: int = 0
    target_events: int = 0
    invalidation_events: int = 0
    resume_count: int = 0
    reasoning_turns: int = 0

    # Cost proxies (Requirement 3.3, 3.4)
    tool_calls_total: int = 0
    tool_calls_by_name: Dict[str, int] = field(default_factory=dict)
    model_turns: int = 0
    tokens: Optional[int] = None       # None until the run exposes a real token count (R3.4)

    # Ordered funnel path being accumulated (Requirement 2.4)
    funnel: List[FunnelEvent] = field(default_factory=list)

    # Suspend-interval bookkeeping: wall-clock starts of open watch cycles (R3.2).
    watch_starts: List[float] = field(default_factory=list)
    suspended_s: Optional[float] = None

    # ── Adaptive Opportunity Engine measurement (adaptive-opportunity-engine R9.3)
    # Captured from the committed DECISION payload. All Optional and None by default
    # so a run without the engine records NULLs (never fabricated, R3.4).
    opportunity_tier: Optional[str] = None                 # committed tier (a_plus | ... | stand_aside)
    opportunity_termination_reason: Optional[str] = None   # watch-cap-reached | session-budget-exhausted


# ── Observed SSE event vocabulary (read-only) ─────────────────────────────────
# Telemetry adds no events; it OBSERVES the glass-box vocabulary that
# ``stream_events.py`` / ``main.py`` already emit (Requirement 2.5, 10.3). The
# event names are matched by their string literals rather than imported, so the
# pure core stays decoupled from ``stream_events`` (and its heavy ``graph``
# imports) and can be property-tested in isolation. These MUST stay in lock-step
# with the ``stream_events`` literals of the same name.
EVENT_RUN_STARTED = "RUN_STARTED"
EVENT_RUN_FINISHED = "RUN_FINISHED"
EVENT_ERROR = "ERROR"
EVENT_REASONING = "REASONING"
EVENT_TOOL_CALL_START = "TOOL_CALL_START"
EVENT_DECISION = "DECISION"

# ── RunEntry.kind values ──────────────────────────────────────────────────────
ENTRY_KIND_RUN = "run"
ENTRY_KIND_RESUME = "resume"


def _extract_ts(payload: Any) -> Optional[float]:
    """Best-effort extraction of a wall-clock timestamp from an event payload.

    Reads a numeric ``ts`` (or ``timestamp`` / ``time``) field from the payload
    when the observation layer stamped one, returning it as a finite ``float``.
    Returns ``None`` when the payload is not a dict, carries no timestamp, or the
    value is non-numeric / non-finite (a bool is never treated as a timestamp).
    Pure and total — never raises (Requirement 8, telemetry ``ts`` is optional).
    """
    if not isinstance(payload, dict):
        return None
    for key in ("ts", "timestamp", "time"):
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
    return None


def _tool_name_of(payload: Any) -> Optional[str]:
    """Extract the ``tool`` name from a ``TOOL_CALL_START`` payload.

    Returns the stripped tool name string when present and non-empty, else
    ``None``. Pure and total — never raises.
    """
    if isinstance(payload, dict):
        name = payload.get("tool")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _normalize_trigger_kind(trigger_kind: Any) -> str:
    """Map a resume's ``trigger_kind`` to exactly one of the two tripwire kinds.

    A ``/resume`` continuation is tagged as a Target_Event or an Invalidation_Event
    (Requirement 2.2). To keep the funnel total and guarantee every resume carries
    exactly one ``trigger_kind`` (so ``target_events + invalidation_events`` can
    equal ``resume_count`` downstream — Property 5), the value is normalized
    case-insensitively: an explicit ``invalidation`` maps to
    ``TRIGGER_INVALIDATION`` and anything else (``target``, unknown, missing) maps
    to the neutral default ``TRIGGER_TARGET``. Pure and total — never raises.
    """
    if isinstance(trigger_kind, str) and trigger_kind.strip().lower() == TRIGGER_INVALIDATION:
        return TRIGGER_INVALIDATION
    return TRIGGER_TARGET


def interpret_events(
    entry: RunEntry,
    events: List[Tuple[str, Dict[str, Any]]],
) -> List[FunnelEvent]:
    """Map one observed ``(event_name, payload)`` stream + its entry into a funnel.

    Reconstructs the ordered list of ``FunnelEvent``s for a single ``/run`` or
    ``/resume`` observation, so a run's path (analyze -> watch -> invalidate ->
    re-watch -> ...) can be replayed exactly (Requirement 2.4). The returned events
    carry contiguous, 0-based ``seq`` numbers and appear in the same relative order
    as their source events (Property 6). The higher-level session model folds the
    fragments produced for a ``/run`` and each of its ``/resume`` continuations
    into a single Session keyed by ``thread_id`` and re-sequences them; this
    function produces the fragment for one entry.

    Derivation (mirrors the design's observation model, Requirements 1.1, 1.2,
    2.1, 2.2, 2.4, 2.5):

      * The entry itself opens the fragment: a ``/run`` emits a leading
        ``session_started`` marker (Requirement 1.1); a ``/resume`` emits a leading
        ``resumed`` marker tagged with its normalized ``trigger_kind``
        (target / invalidation, Requirement 2.2). The marker's ``ts`` is taken from
        the first source event that exposes one.
      * ``REASONING``        -> ``reasoning_turn``
      * ``TOOL_CALL_START``  -> ``watch_registered`` when ``tool ==
                                "watch_price_condition"`` (a Watch_Cycle,
                                Requirement 2.1), otherwise ``tool_call``; both
                                carry the observed ``tool_name``.
      * ``DECISION``         -> ``decision``
      * ``ERROR``            -> ``error``
      * every other event (``RUN_STARTED``, ``RUN_FINISHED``, ``TOOL_CALL_RESULT``,
        ``TOOL_CALL_END``, ``VERIFICATION_STEP``, anything unrecognized) carries no
        funnel semantics and is skipped — ``RUN_STARTED`` in particular is
        represented by the leading ``session_started`` marker, not a second event.

    This function is PURE and TOTAL: it reads only its arguments, never mutates
    them (Requirement 8.4), holds no ambient state, and NEVER raises on any input —
    including a malformed ``entry``, a non-list ``events``, tuples of the wrong
    shape, or non-dict payloads (Requirement 8.3). It observes the existing
    lifecycle without changing control flow (Requirement 2.5).
    """
    funnel: List[FunnelEvent] = []

    # Tolerate any events container; a non-iterable degrades to "no events".
    if isinstance(events, (list, tuple)):
        source = list(events)
    else:
        source = []

    # The opening marker's ts is the first observable timestamp in the fragment
    # (typically the RUN_STARTED / resume frame). Scanning is read-only.
    opening_ts: Optional[float] = None
    for item in source:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            ts = _extract_ts(item[1])
            if ts is not None:
                opening_ts = ts
                break

    # 1. Open the fragment from the entry (Requirement 1.1, 1.2, 2.2).
    kind = getattr(entry, "kind", None)
    seq = 0
    if kind == ENTRY_KIND_RESUME:
        trigger_kind = _normalize_trigger_kind(getattr(entry, "trigger_kind", None))
        funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_RESUMED, ts=opening_ts, trigger_kind=trigger_kind))
    else:
        # Default (including a missing / unknown kind) opens a fresh session.
        funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_SESSION_STARTED, ts=opening_ts))
    seq += 1

    # 2. Fold the observed events into funnel events, preserving relative order.
    for item in source:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue  # malformed tuple: skip (totality)
        name, payload = item[0], item[1]
        ts = _extract_ts(payload)

        if name == EVENT_REASONING:
            funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_REASONING_TURN, ts=ts))
            seq += 1
        elif name == EVENT_TOOL_CALL_START:
            tool_name = _tool_name_of(payload)
            if tool_name == WATCH_TOOL_NAME:
                funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_WATCH_REGISTERED, ts=ts, tool_name=tool_name))
            else:
                funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_TOOL_CALL, ts=ts, tool_name=tool_name))
            seq += 1
        elif name == EVENT_DECISION:
            funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_DECISION, ts=ts))
            seq += 1
        elif name == EVENT_ERROR:
            funnel.append(FunnelEvent(seq=seq, kind=FUNNEL_ERROR, ts=ts))
            seq += 1
        # RUN_STARTED / RUN_FINISHED / TOOL_CALL_RESULT / TOOL_CALL_END /
        # VERIFICATION_STEP / unrecognized: no funnel semantics — skipped.

    return funnel


# ── Outcome classification (pure, total) ──────────────────────────────────────
# Markers on the observed decision record that identify a forced or data-gated
# HOLD. The Deep Quant graph emits a forced HOLD with ``source == "forced_hold"``
# and ``reason == "no-decision-reached"`` (reasoning budget exhausted / no
# consensus), and a data-gated HOLD with ``reason == "directional-data-unavailable"``
# (a directional call was attempted before any market data was available). These
# literals are matched case-insensitively, and robust substring fallbacks plus
# forward-compatible boolean flags (``forced`` / ``data_gated`` / ``gated``) are
# also honored so a marker-shape change does not silently mis-classify. Absent any
# marker a HOLD is ``voluntary`` (the agent chose to stand aside), Requirement 1.4.
_FORCED_HOLD_SOURCES = frozenset({"forced_hold", "force_hold"})
_FORCED_HOLD_REASONS = frozenset({"no-decision-reached"})
_DATA_GATED_REASONS = frozenset({"directional-data-unavailable"})

# The recognized directional / hold action tokens on a decision record. Matched
# case-insensitively after stripping; anything else is treated as "no recognized
# terminal action" so classification stays total (Requirement 1.4, 8.3).
_ACTION_BUY = "BUY"
_ACTION_SELL = "SELL"
_ACTION_HOLD = "HOLD"


def _normalize_action(decision: Any) -> Optional[str]:
    """Return the normalized directional action token from a decision record.

    Reads the decision's ``action`` field and normalizes it to one of ``"BUY"`` /
    ``"SELL"`` / ``"HOLD"`` (upper-cased, whitespace-stripped, case-insensitive).
    Returns ``None`` when the decision is not a dict, carries no string ``action``,
    or the action is unrecognized — so an unexpected action string degrades to
    "no recognized terminal decision" rather than mis-classifying. Pure and total:
    never raises (Requirement 8.3).
    """
    if not isinstance(decision, dict):
        return None
    action = decision.get("action")
    if not isinstance(action, str):
        return None
    normalized = action.strip().upper()
    if normalized in (_ACTION_BUY, _ACTION_SELL, _ACTION_HOLD):
        return normalized
    return None


def classify_hold_reason(decision: Any) -> str:
    """Classify a HOLD's sub-reason as voluntary / forced / data-gated (R1.4).

    Derived PURELY from the decision record; only meaningful when the outcome is a
    HOLD (``classify_outcome`` calls this only on the HOLD branch). Recognition,
    in the design's stated precedence (forced marker, then data-gated marker, else
    the default), and all case-insensitive / whitespace-tolerant:

      * ``forced``     — an explicit ``forced`` boolean flag, a ``source`` in
                         ``{forced_hold, force_hold}`` (or containing ``forced``),
                         or a ``reason`` of ``no-decision-reached`` (or containing
                         ``forced``). This is the reasoning-budget-exhausted /
                         no-consensus HOLD the graph injects.
      * ``data-gated`` — an explicit ``data_gated`` / ``gated`` boolean flag, a
                         ``reason`` of ``directional-data-unavailable``, or a
                         ``reason`` mentioning missing/gated data. This is the HOLD
                         emitted when a directional call was attempted before any
                         market data was available.
      * ``voluntary``  — the default when no forced/gated marker is present: the
                         agent deliberately stood aside.

    Returns EXACTLY one of ``HOLD_VOLUNTARY`` / ``HOLD_FORCED`` / ``HOLD_DATA_GATED``
    (all three members of ``HOLD_REASONS``). Pure and total: reads only its
    argument, never mutates it, and NEVER raises on any input — including a
    non-dict / ``None`` decision or non-string marker fields (Requirement 8.3, 8.4).
    """
    if not isinstance(decision, dict):
        return HOLD_VOLUNTARY

    source = decision.get("source")
    source_l = source.strip().lower() if isinstance(source, str) else ""
    reason = decision.get("reason")
    reason_l = reason.strip().lower() if isinstance(reason, str) else ""

    # 1. Forced marker (explicit flag, forced source, or forced/no-decision reason).
    if decision.get("forced") is True:
        return HOLD_FORCED
    if source_l in _FORCED_HOLD_SOURCES or reason_l in _FORCED_HOLD_REASONS:
        return HOLD_FORCED
    if "forced" in source_l or "forced" in reason_l:
        return HOLD_FORCED

    # 2. Data-gated marker (explicit flag, gated reason, or a data-unavailable reason).
    if decision.get("data_gated") is True or decision.get("gated") is True:
        return HOLD_DATA_GATED
    if reason_l in _DATA_GATED_REASONS:
        return HOLD_DATA_GATED
    if "data" in reason_l and ("unavailable" in reason_l or "gat" in reason_l):
        return HOLD_DATA_GATED

    # 3. Default: a deliberate stand-aside HOLD.
    return HOLD_VOLUNTARY


def classify_outcome(
    decision: Optional[dict],
    run_status: Optional[str],
    errored: bool,
) -> Tuple[str, Optional[str]]:
    """Classify a Session's terminal outcome (Requirement 1.4).

    Returns ``(outcome, hold_reason)`` where ``outcome`` is EXACTLY one member of
    ``SESSION_OUTCOMES`` (``trade_buy`` / ``trade_sell`` / ``hold`` / ``error`` /
    ``incomplete``) and ``hold_reason`` is a member of ``HOLD_REASONS``
    (``voluntary`` / ``forced`` / ``data-gated``) IF AND ONLY IF the outcome is
    ``hold`` — otherwise ``hold_reason`` is ``None`` (Property 3).

    Classification, in precedence order:

      1. ``errored`` True                 -> (``error``, None). An errored run is
         terminal regardless of any partial decision (the ERROR path emits no
         DECISION, so a decision is not expected here anyway).
      2. A recognized directional/hold ``action`` on the decision record:
           * ``BUY``  -> (``trade_buy``, None)
           * ``SELL`` -> (``trade_sell``, None)
           * ``HOLD`` -> (``hold``, <voluntary|forced|data-gated>) via
                         :func:`classify_hold_reason`.
         The action is matched case-insensitively (Deep Quant emits upper-case
         ``BUY`` / ``SELL`` / ``HOLD``, but any casing is accepted).
      3. Otherwise — no error and no recognized terminal decision (a ``None`` /
         malformed decision, an unexpected action string, or a run that ended
         while still paused) -> (``incomplete``, None). The horizon-based
         ``incomplete`` classification for still-open sessions is handled
         separately by ``aggregate`` using ``now_ref``; this branch keeps
         ``classify_outcome`` total for the no-terminal-decision case (R1.5).

    ``run_status`` is accepted as part of the observed terminal context (the
    ``RUN_FINISHED`` status) but does not override the decision/error signals
    above — a committed decision or an error is authoritative, and a run that
    finished without either is ``incomplete`` regardless of its status string.

    This function is PURE and TOTAL: it reads only its arguments, never mutates
    them (Requirement 8.4), holds no ambient state, and NEVER raises on any input —
    including a ``None`` or malformed ``decision``, an unexpected ``action``
    string, or a non-string ``run_status`` (Requirement 8.3).
    """
    # 1. An errored run is terminal regardless of any partial decision.
    if errored:
        return OUTCOME_ERROR, None

    # 2. A structured decision record with a recognized action is terminal.
    action = _normalize_action(decision)
    if action == _ACTION_BUY:
        return OUTCOME_TRADE_BUY, None
    if action == _ACTION_SELL:
        return OUTCOME_TRADE_SELL, None
    if action == _ACTION_HOLD:
        return OUTCOME_HOLD, classify_hold_reason(decision)

    # 3. No error and no recognized terminal decision -> incomplete.
    return OUTCOME_INCOMPLETE, None

# ── Session finalization (pure, total, non-mutating) ──────────────────────────


def _finite_number(value: Any) -> bool:
    """True iff ``value`` is a finite real number (and not a ``bool``).

    Booleans are excluded because ``True`` / ``False`` are ``int`` subclasses and
    are never valid timestamps, counts, or token counts here. Pure and total.
    """
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _int_or_zero(value: Any) -> int:
    """Coerce a counter to a non-negative-safe ``int`` (defensive, total).

    Returns the value as ``int`` when it is a real integer count, otherwise ``0``.
    Guards ``finalize_session`` against a malformed accumulator without raising.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _opportunity_extra(state) -> Optional[Dict[str, Any]]:
    """Assemble the Adaptive Opportunity Engine ``extra`` bag, or ``None`` (R9.3).

    Carries the bounded-hunt termination reason and heartbeat usage when observed;
    returns ``None`` when the run recorded neither (so a non-engine run persists a
    NULL ``extra`` rather than an empty object). Pure and total — never raises.
    """
    extra: Dict[str, Any] = {}
    reason = getattr(state, "opportunity_termination_reason", None)
    if isinstance(reason, str) and reason:
        extra["termination_reason"] = reason
    heartbeats = getattr(state, "heartbeats_used", None)
    if isinstance(heartbeats, int) and not isinstance(heartbeats, bool) and heartbeats > 0:
        extra["heartbeats_used"] = heartbeats
    return extra or None


def finalize_session(state: SessionState) -> SessionRecord:
    """Fold an accumulated ``SessionState`` into an immutable ``SessionRecord``.

    This is the pure boundary between the mutable working state the background
    ``SessionWriter`` builds up (draining observations for a ``thread_id``) and the
    frozen record that is persisted and later aggregated. It READS the state and
    returns a NEW ``SessionRecord``; it NEVER mutates the state (Requirement 8.4) —
    every mutable container it carries over (``tool_calls_by_name``, ``funnel``) is
    copied so the record never aliases the accumulator, and no field of ``state``
    is written.

    Folding rules (Requirements 1.3, 3.1, 3.2, 3.3, 3.4):

      * ``session_id`` is the surrogate ``f"{thread_id}:{started_at}"`` that
        distinguishes a fresh ``/run`` on a reused ``thread_id`` from an earlier,
        already-closed Session.
      * Funnel counters (``watch_cycles``, ``target_events``,
        ``invalidation_events``, ``resume_count``, ``reasoning_turns``) and cost
        proxies (``tool_calls_total``, ``model_turns``) are carried over as-is;
        ``tool_calls_by_name`` is carried over as a NEW dict so the record does not
        alias the state's dict (Requirement 3.3).
      * ``tokens`` is carried over ONLY when the run exposed a real integer token
        count; otherwise it stays ``None`` — a token count is never fabricated
        (Requirement 3.4).
      * ``ended_at`` / ``outcome`` / ``hold_reason`` are taken from the state (set
        by the writer on the terminal event, Requirement 1.3).
      * ``time_to_decision_s`` is ``ended_at - started_at`` when both timestamps are
        observable, else ``None`` while the Session is still open (Requirement 3.1).
      * ``suspended_s`` is the sum of the observable watch->resume suspend intervals
        — each watch cycle's start (``state.watch_starts``) paired, in order, with
        the timestamp of the resume that ended it (the ``resumed`` funnel events) —
        and is bounded to ``0 <= suspended_s <= time_to_decision_s`` (Property 8,
        Requirement 3.2). When no suspend interval is observable it is ``None`` (a
        pre-computed ``state.suspended_s`` is honored as a fallback but never
        fabricated).
      * ``funnel`` is a NEW list preserving the accumulated seq order
        (Requirement 2.4).

    PURE and TOTAL: reads only ``state``, never mutates it, holds no ambient state,
    and never raises on a degenerate accumulator (Requirement 8.3, 8.4).
    """
    thread_id = getattr(state, "thread_id", None)
    started_at = getattr(state, "started_at", None)
    ended_at = getattr(state, "ended_at", None)

    # Surrogate identity distinguishing sessions that reuse a thread_id.
    session_id = f"{thread_id}:{started_at}"

    # Time-to-decision: ended_at - started_at when both timestamps are observable,
    # else None while the Session is still open (Requirement 3.1).
    time_to_decision_s: Optional[float] = None
    if _finite_number(started_at) and _finite_number(ended_at):
        time_to_decision_s = float(ended_at) - float(started_at)

    # Suspend intervals: pair each watch-cycle start with the resume that ended it,
    # in observation order, and sum the non-negative, observable intervals (R3.2).
    resume_ts: List[float] = []
    funnel_src = getattr(state, "funnel", None)
    funnel_list = list(funnel_src) if isinstance(funnel_src, (list, tuple)) else []
    for event in funnel_list:
        if getattr(event, "kind", None) == FUNNEL_RESUMED:
            ts = getattr(event, "ts", None)
            if _finite_number(ts):
                resume_ts.append(float(ts))

    watch_starts_src = getattr(state, "watch_starts", None)
    watch_starts: List[float] = []
    if isinstance(watch_starts_src, (list, tuple)):
        for start in watch_starts_src:
            if _finite_number(start):
                watch_starts.append(float(start))

    suspended_s: Optional[float] = None
    intervals: List[float] = []
    for start, resume in zip(watch_starts, resume_ts):
        interval = resume - start
        if interval >= 0.0:  # only observable, non-negative suspend intervals
            intervals.append(interval)
    if intervals:
        suspended_s = float(sum(intervals))
    else:
        # No observable watch->resume interval: honor a pre-computed value if the
        # accumulator supplied a real one, but never fabricate (Requirement 3.2).
        pre = getattr(state, "suspended_s", None)
        if _finite_number(pre) and float(pre) >= 0.0:
            suspended_s = float(pre)

    # Bound suspended_s to 0 <= suspended_s <= time_to_decision_s (Property 8).
    if suspended_s is not None:
        if suspended_s < 0.0:
            suspended_s = 0.0
        if (
            time_to_decision_s is not None
            and time_to_decision_s >= 0.0
            and suspended_s > time_to_decision_s
        ):
            suspended_s = time_to_decision_s

    # Cost proxies: copy the per-tool dict so the record never aliases the state.
    tool_calls_by_name_src = getattr(state, "tool_calls_by_name", None)
    if isinstance(tool_calls_by_name_src, dict):
        tool_calls_by_name = dict(tool_calls_by_name_src)
    else:
        tool_calls_by_name = {}

    # Tokens: carried over only when a real integer count was exposed (R3.4).
    tokens_src = getattr(state, "tokens", None)
    tokens: Optional[int] = tokens_src if (isinstance(tokens_src, int) and not isinstance(tokens_src, bool)) else None

    return SessionRecord(
        session_id=session_id,
        thread_id=thread_id,
        symbol=getattr(state, "symbol", None),
        timeframe=getattr(state, "timeframe", None),
        mode=getattr(state, "mode", None),
        started_at=started_at,
        ended_at=ended_at,
        outcome=getattr(state, "outcome", None),
        hold_reason=getattr(state, "hold_reason", None),
        # Funnel counters (Requirement 2.3)
        watch_cycles=_int_or_zero(getattr(state, "watch_cycles", 0)),
        target_events=_int_or_zero(getattr(state, "target_events", 0)),
        invalidation_events=_int_or_zero(getattr(state, "invalidation_events", 0)),
        resume_count=_int_or_zero(getattr(state, "resume_count", 0)),
        reasoning_turns=_int_or_zero(getattr(state, "reasoning_turns", 0)),
        # Cost proxies (Requirement 3.3, 3.4)
        tool_calls_total=_int_or_zero(getattr(state, "tool_calls_total", 0)),
        tool_calls_by_name=tool_calls_by_name,
        model_turns=_int_or_zero(getattr(state, "model_turns", 0)),
        tokens=tokens,
        # Timing (Requirement 3.1, 3.2)
        time_to_decision_s=time_to_decision_s,
        suspended_s=suspended_s,
        # Ordered funnel path — a new list preserving seq order (Requirement 2.4)
        funnel=funnel_list,
        # ── Adaptive Opportunity Engine measurement (R9.3) ────────────────────
        # Persist the committed tier to the forward-compat ``opportunity_tier``
        # column, and the bounded-hunt termination reason + heartbeat usage to the
        # generic ``extra`` JSON bag. All None/absent for a run without the engine.
        opportunity_tier=getattr(state, "opportunity_tier", None),
        session_budget=None,
        extra=_opportunity_extra(state),
    )


# ── Filtering and grouping helpers (pure, total, non-mutating) ────────────────
# The aggregation core reports metrics over a filtered set of Session_Records and
# breaks them down by symbol / timeframe / mode. These two helpers are the pure
# selection/partition primitives that ``aggregate`` (and the read path) build on:
# ``filter_sessions`` narrows a record set to those matching every supplied
# predicate (Requirement 5.3, Property 14), and ``group_sessions`` partitions a
# record set into disjoint groups keyed by one grouping attribute (Requirement
# 4.5, Property 12). Both are PURE and TOTAL: they read only their arguments,
# never mutate them (Requirement 8.4), hold no ambient state, and NEVER raise on
# any input — including a non-list ``records`` container, malformed records, or an
# unrecognized grouping key (Requirement 8.3).

# The grouping attributes the report breaks metrics down by (Requirement 4.5).
GROUP_KEY_SYMBOL = "symbol"
GROUP_KEY_TIMEFRAME = "timeframe"
GROUP_KEY_MODE = "mode"

GROUP_KEYS = (GROUP_KEY_SYMBOL, GROUP_KEY_TIMEFRAME, GROUP_KEY_MODE)


def _as_record_list(records: Any) -> List[SessionRecord]:
    """Coerce the ``records`` argument to a list without mutating the input.

    Tolerates any iterable container (list / tuple); a non-iterable degrades to an
    empty list so the helpers stay total (Requirement 8.3). The returned list is a
    NEW shallow container — the input is never mutated (Requirement 8.4).
    """
    if isinstance(records, (list, tuple)):
        return list(records)
    return []


def filter_sessions(
    records: List[SessionRecord],
    *,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    mode: Optional[str] = None,
    since: Optional[float] = None,
    until: Optional[float] = None,
) -> List[SessionRecord]:
    """Select the records matching EVERY supplied predicate (Requirement 5.3).

    Returns a NEW list containing exactly those records that satisfy ALL of the
    supplied filters, in their original relative order (Property 14):

      * ``symbol`` / ``timeframe`` / ``mode`` — equality on the record's
        corresponding attribute. A filter of ``None`` imposes NO constraint on that
        attribute (every record matches it), so passing no filters returns every
        record.
      * ``since`` / ``until`` — the record's ``started_at`` must lie within the
        INCLUSIVE ``[since, until]`` range. ``since`` alone imposes only a lower
        bound (``started_at >= since``); ``until`` alone only an upper bound
        (``started_at <= until``); neither imposes no time constraint. A record
        whose ``started_at`` is not an observable finite number cannot be confirmed
        to fall within a supplied bound, so it is EXCLUDED whenever ``since`` or
        ``until`` is supplied (and unconstrained when neither is).

    PURE and TOTAL: reads only its arguments, never mutates them (Requirement 8.4),
    and NEVER raises on any input — a non-list ``records``, malformed records, or a
    non-numeric bound all degrade gracefully rather than raising (Requirement 8.3).
    """
    source = _as_record_list(records)

    # A supplied bound is only usable when it is an observable finite number; a
    # non-numeric bound is treated as "no bound" so the helper stays total.
    low = float(since) if _finite_number(since) else None
    high = float(until) if _finite_number(until) else None
    time_constrained = low is not None or high is not None

    selected: List[SessionRecord] = []
    for record in source:
        # Equality predicates: a None filter imposes no constraint (R5.3).
        if symbol is not None and getattr(record, "symbol", None) != symbol:
            continue
        if timeframe is not None and getattr(record, "timeframe", None) != timeframe:
            continue
        if mode is not None and getattr(record, "mode", None) != mode:
            continue

        # Time-range predicate on started_at (inclusive), when a bound is supplied.
        if time_constrained:
            started_at = getattr(record, "started_at", None)
            if not _finite_number(started_at):
                continue  # cannot confirm it falls within the supplied bound
            started_at = float(started_at)
            if low is not None and started_at < low:
                continue
            if high is not None and started_at > high:
                continue

        selected.append(record)

    return selected


def group_sessions(
    records: List[SessionRecord],
    key: str,
) -> Dict[Any, List[SessionRecord]]:
    """Partition records into disjoint groups keyed by one attribute (R4.5).

    Groups the records by the value of the ``key`` attribute (``symbol`` /
    ``timeframe`` / ``mode``) and returns a mapping from each observed key value to
    the list of records carrying that value, preserving the records' original
    relative order within each group. The result is a true PARTITION (Property 12):

      * every record belongs to EXACTLY one group (the group for its own attribute
        value, ``None`` included — records with a missing attribute collapse into a
        single ``None``-keyed group rather than being dropped),
      * the groups are DISJOINT (no record appears in two groups),
      * the sum of the groups' sizes equals the number of input records, and
      * every member of a group shares that group's key value.

    An unrecognized ``key`` (anything outside ``GROUP_KEYS``) yields an empty
    mapping rather than raising, keeping the helper total (Requirement 8.3).

    PURE and TOTAL: reads only its arguments, never mutates them (the returned
    lists are NEW containers — Requirement 8.4), and NEVER raises on any input,
    including a non-list ``records`` or malformed records (Requirement 8.3).
    """
    groups: Dict[Any, List[SessionRecord]] = {}
    if key not in GROUP_KEYS:
        return groups

    for record in _as_record_list(records):
        group_value = getattr(record, key, None)
        # dict keys must be hashable; a record's grouping attribute is a str/None,
        # but guard against an unhashable value degrading into a stable string key.
        try:
            bucket = groups.get(group_value)
        except TypeError:
            group_value = repr(group_value)
            bucket = groups.get(group_value)
        if bucket is None:
            bucket = []
            groups[group_value] = bucket
        bucket.append(record)

    return groups


# ── Distribution helper (pure, total, non-mutating) ───────────────────────────
# The Telemetry_Report summarizes each numeric sample (watch cycles per Session,
# time-to-decision, and the cost proxies) as a small Distribution — its mean,
# median, maximum, and count (Requirement 4.4). ``_distribution`` is the single
# pure primitive that computes that summary and every Distribution in the report
# flows through it, so its well-formedness guarantees (Property 11) hold uniformly.

# The keys of the Distribution mapping the report emits (Requirement 4.4).
DIST_MEAN = "mean"
DIST_MEDIAN = "median"
DIST_MAX = "max"
DIST_COUNT = "count"


def _distribution(sample: Any) -> Dict[str, Any]:
    """Summarize a numeric ``sample`` as ``{mean, median, max, count}`` (R4.4).

    Filters the input to its observable finite real numbers (reusing
    ``_finite_number`` — booleans and non-finite / non-numeric values are dropped)
    and returns a well-formed Distribution over what remains (Property 11):

      * ``count``  — the number of finite values in the sample.
      * ``mean``   — the arithmetic mean, clamped into ``[min, max]`` so a
                     floating-point rounding error can never push it outside the
                     sample's bounds (``min(sample) <= mean <= max(sample)``).
      * ``median`` — the statistical median (for an even count, the mean of the two
                     central values), which always lies within ``[min, max]``.
      * ``max``    — the maximum value, exactly ``max(sample)``.

    On an EMPTY sample — an empty / non-iterable container, or one with no finite
    values — every summary field (``mean`` / ``median`` / ``max``) is ``None`` and
    ``count`` is ``0`` (Requirement 4.4, 8.3), so the report never fabricates a
    statistic over no data.

    PURE and TOTAL: reads only its argument, never mutates it (Requirement 8.4),
    holds no ambient state, and NEVER raises on any input — a non-iterable sample,
    or one carrying non-numeric / bool / NaN / inf entries, degrades to dropping
    those entries rather than raising (Requirement 8.3).
    """
    # Coerce to an iterable and keep only observable finite real numbers.
    values: List[float] = []
    if isinstance(sample, (list, tuple)):
        for item in sample:
            if _finite_number(item):
                values.append(float(item))

    count = len(values)
    if count == 0:
        return {DIST_MEAN: None, DIST_MEDIAN: None, DIST_MAX: None, DIST_COUNT: 0}

    lo = min(values)
    hi = max(values)

    mean = statistics.fmean(values)
    # Clamp mean into [lo, hi]: mathematically the mean already lies within the
    # sample's bounds, but guard against floating-point drift so the reported mean
    # can never sit outside [min, max] (Property 11).
    if mean < lo:
        mean = lo
    elif mean > hi:
        mean = hi

    median = statistics.median(values)

    return {
        DIST_MEAN: float(mean),
        DIST_MEDIAN: float(median),
        DIST_MAX: float(hi),
        DIST_COUNT: count,
    }


# ── Telemetry_Report aggregation (pure, total, deterministic, non-mutating) ───
# ``aggregate`` is the pure numeric core of the feature: it folds a set of
# in-memory Session_Records into the Telemetry_Report (conversion / hold / error /
# incomplete rates, invalidation rate, watch-cycle / time-to-decision / cost
# distributions, and per-symbol / -timeframe / -mode breakdowns with weak-prior
# flags). It requires no live database (Requirement 8.1), yields identical output
# for identical inputs (Requirement 8.2, Property 16), never raises on degenerate
# input and represents unavailable metrics as ``null`` (Requirement 8.3, Property
# 17), and never mutates its inputs (Requirement 8.4, Property 18).

# ── Telemetry_Report field names ──────────────────────────────────────────────
REPORT_SESSION_COUNT = "session_count"
REPORT_WEAK_PRIOR_MIN_SESSIONS = "weak_prior_min_sessions"
REPORT_WEAK_PRIOR = "weak_prior"
REPORT_FILTERS = "filters"
REPORT_OUTCOMES = "outcomes"
REPORT_INVALIDATION_RATE = "invalidation_rate"
REPORT_WATCH_CYCLES = "watch_cycles"
REPORT_TIME_TO_DECISION = "time_to_decision_s"
REPORT_COST = "cost"
REPORT_BY_SYMBOL = "by_symbol"
REPORT_BY_TIMEFRAME = "by_timeframe"
REPORT_BY_MODE = "by_mode"

# outcomes block field names
OUTCOMES_CONVERSION_RATE = "conversion_rate"
OUTCOMES_HOLD_RATE = "hold_rate"
OUTCOMES_ERROR_RATE = "error_rate"
OUTCOMES_INCOMPLETE_RATE = "incomplete_rate"
OUTCOMES_COUNTS = "counts"

# cost block field names
COST_TOOL_CALLS = "tool_calls"
COST_MODEL_TURNS = "model_turns"
COST_RESUME_COUNT = "resume_count"
COST_TOOL_CALLS_BY_NAME = "tool_calls_by_name"

# GroupReport field names
GROUP_KEY = "key"


def _horizon_of(config: Any) -> float:
    """Read the ``incomplete_horizon_seconds`` from a config, defensively (total).

    Returns the configured horizon as a finite ``float`` when observable, else the
    documented default. Never raises — a malformed / missing config degrades to the
    default so ``aggregate`` stays total (Requirement 8.3).
    """
    horizon = getattr(config, "incomplete_horizon_seconds", DEFAULT_INCOMPLETE_HORIZON_SECONDS)
    if _finite_number(horizon):
        return float(horizon)
    return float(DEFAULT_INCOMPLETE_HORIZON_SECONDS)


def _min_sessions_of(config: Any) -> int:
    """Read the ``weak_prior_min_sessions`` from a config, defensively (total).

    Returns the configured minimum as an ``int`` when it is a real integer, else
    the documented default. Never raises (Requirement 8.3).
    """
    minimum = getattr(config, "weak_prior_min_sessions", DEFAULT_WEAK_PRIOR_MIN_SESSIONS)
    if isinstance(minimum, int) and not isinstance(minimum, bool):
        return minimum
    return DEFAULT_WEAK_PRIOR_MIN_SESSIONS


def _effective_outcome(
    record: Any,
    config: Any,
    now_ref: Optional[float],
) -> Optional[str]:
    """Resolve the effective Session_Outcome used for aggregation (R1.4, R1.5).

    Precedence:

      * A record carrying a recognized terminal / incomplete ``outcome`` (a member
        of ``SESSION_OUTCOMES``) keeps that outcome.
      * A record with no recognized outcome is still OPEN. It is classified
        ``incomplete`` ONLY when an explicit ``now_ref`` is supplied and the
        record's age (``now_ref - started_at``) STRICTLY exceeds the configured
        ``incomplete_horizon_seconds`` (Requirement 1.5, Property 4).
      * An open record within the horizon — or any open record when ``now_ref`` is
        not supplied (no ambient clock, so it cannot age out and stays
        deterministic) — is returned as ``None`` (UNCLASSIFIED): it is counted
        under NO terminal outcome and is excluded from ``session_count`` and every
        rate denominator (Property 4, Property 9).

    Pure and total: reads only its arguments, never mutates them, and never raises
    (Requirement 8.3, 8.4).
    """
    outcome = getattr(record, "outcome", None)
    if outcome in SESSION_OUTCOMES:
        return outcome

    # Still open: only an explicit now_ref past the horizon ages it to incomplete.
    if now_ref is not None and _finite_number(now_ref):
        started_at = getattr(record, "started_at", None)
        if _finite_number(started_at):
            if (float(now_ref) - float(started_at)) > _horizon_of(config):
                return OUTCOME_INCOMPLETE

    return None


def _classified(
    records: List[SessionRecord],
    config: Any,
    now_ref: Optional[float],
) -> List[Tuple[SessionRecord, str]]:
    """Pair each classified record with its effective outcome (helper, total).

    Returns a NEW list of ``(record, effective_outcome)`` for exactly those records
    that classify to one of the five Session_Outcomes; open-within-horizon (and
    otherwise-unclassifiable) records are dropped. Preserves input order. Reads
    only its arguments and never mutates them (Requirement 8.4).
    """
    pairs: List[Tuple[SessionRecord, str]] = []
    for record in _as_record_list(records):
        eff = _effective_outcome(record, config, now_ref)
        if eff is not None:
            pairs.append((record, eff))
    return pairs


def _safe_ratio(numerator: float, denominator: float) -> Optional[float]:
    """Return ``numerator / denominator`` as a float, or ``None`` on a zero denom.

    Any ratio whose denominator is zero (or non-positive) is UNAVAILABLE and is
    represented as ``None`` (Requirement 4.3, 8.3). Never raises.
    """
    if denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _outcomes_block(
    pairs: List[Tuple[SessionRecord, str]],
) -> Dict[str, Any]:
    """Build the outcomes sub-report over classified ``(record, outcome)`` pairs.

    Emits the conversion / hold / error / incomplete rates (each ``count /
    session_count``, ``null`` when there are no classified sessions) and the raw
    counts for all five outcomes (Requirement 4.1, 4.2, Property 9). The four rates
    sum to ``1.0`` over the classified sessions (every classified session lands in
    exactly one of the five counts). Pure and total; never raises.
    """
    counts = {
        OUTCOME_TRADE_BUY: 0,
        OUTCOME_TRADE_SELL: 0,
        OUTCOME_HOLD: 0,
        OUTCOME_ERROR: 0,
        OUTCOME_INCOMPLETE: 0,
    }
    for _record, outcome in pairs:
        if outcome in counts:
            counts[outcome] += 1

    n = len(pairs)
    converted = counts[OUTCOME_TRADE_BUY] + counts[OUTCOME_TRADE_SELL]

    return {
        OUTCOMES_CONVERSION_RATE: _safe_ratio(converted, n),
        OUTCOMES_HOLD_RATE: _safe_ratio(counts[OUTCOME_HOLD], n),
        OUTCOMES_ERROR_RATE: _safe_ratio(counts[OUTCOME_ERROR], n),
        OUTCOMES_INCOMPLETE_RATE: _safe_ratio(counts[OUTCOME_INCOMPLETE], n),
        OUTCOMES_COUNTS: {
            OUTCOME_TRADE_BUY: counts[OUTCOME_TRADE_BUY],
            OUTCOME_TRADE_SELL: counts[OUTCOME_TRADE_SELL],
            OUTCOME_HOLD: counts[OUTCOME_HOLD],
            OUTCOME_ERROR: counts[OUTCOME_ERROR],
            OUTCOME_INCOMPLETE: counts[OUTCOME_INCOMPLETE],
        },
    }


def _invalidation_rate(records: List[SessionRecord]) -> Optional[float]:
    """Compute ``inv / (inv + target)`` over records, ``null`` on a zero total.

    Sums the per-Session ``invalidation_events`` and ``target_events`` and returns
    the invalidation share, which lies in ``[0, 1]``. When the combined total is
    zero the rate is UNAVAILABLE and is ``None`` (Requirement 4.3, Property 10).
    Pure and total; never raises.
    """
    inv = 0
    target = 0
    for record in records:
        inv += _int_or_zero(getattr(record, "invalidation_events", 0))
        target += _int_or_zero(getattr(record, "target_events", 0))
    return _safe_ratio(inv, inv + target)


def _watch_cycles_distribution(records: List[SessionRecord]) -> Dict[str, Any]:
    """Distribution of Watch_Cycles per Session over ``records`` (Requirement 4.4)."""
    return _distribution([_int_or_zero(getattr(r, "watch_cycles", 0)) for r in records])


def _group_report(
    key: Any,
    members: List[SessionRecord],
    config: Any,
    now_ref: Optional[float],
    min_sessions: int,
) -> Dict[str, Any]:
    """Build one GroupReport for a symbol / timeframe / mode group (R4.5, 4.6).

    Reports the group's ``key``, its classified ``session_count``, its
    ``weak_prior`` flag (``session_count < min_sessions``, Property 13), and the
    group-scoped outcomes block, invalidation rate, and watch-cycle distribution.
    Pure and total; never raises.
    """
    pairs = _classified(members, config, now_ref)
    group_count = len(pairs)
    classified_members = [record for record, _outcome in pairs]
    return {
        GROUP_KEY: key,
        REPORT_SESSION_COUNT: group_count,
        REPORT_WEAK_PRIOR: group_count < min_sessions,
        REPORT_OUTCOMES: _outcomes_block(pairs),
        REPORT_INVALIDATION_RATE: _invalidation_rate(classified_members),
        REPORT_WATCH_CYCLES: _watch_cycles_distribution(classified_members),
    }


def _group_reports(
    classified_records: List[SessionRecord],
    key: str,
    config: Any,
    now_ref: Optional[float],
    min_sessions: int,
) -> List[Dict[str, Any]]:
    """Build the list of GroupReports for one grouping attribute (Requirement 4.5).

    Partitions the already-classified records by ``key`` (via ``group_sessions``,
    a disjoint partition — Property 12) and emits one GroupReport per observed key
    value. Groups are ordered deterministically (``None``-keyed group first, then
    by the string form of the key) so identical inputs yield identical output
    (Property 16). Pure and total; never raises.
    """
    groups = group_sessions(classified_records, key)
    ordered_keys = sorted(groups.keys(), key=lambda k: (k is not None, str(k)))
    return [
        _group_report(k, groups[k], config, now_ref, min_sessions)
        for k in ordered_keys
    ]


def _tool_calls_by_name_totals(records: List[SessionRecord]) -> Dict[str, int]:
    """Sum the per-tool call counts across ``records`` (Requirement 3.3, 4.4).

    Returns a NEW mapping ``{tool_name: total_calls}`` accumulated over every
    Session's ``tool_calls_by_name``, with keys in sorted order for deterministic
    output (Property 16). Malformed per-tool entries are skipped. Pure and total;
    never raises and never mutates the input records' dicts.
    """
    totals: Dict[str, int] = {}
    for record in records:
        by_name = getattr(record, "tool_calls_by_name", None)
        if not isinstance(by_name, dict):
            continue
        for name, count in by_name.items():
            if isinstance(name, str) and isinstance(count, int) and not isinstance(count, bool):
                totals[name] = totals.get(name, 0) + count
    return dict(sorted(totals.items()))


def aggregate(
    records: List[SessionRecord],
    config: TelemetryConfig,
    now_ref: Optional[float] = None,
) -> dict:
    """Fold in-memory Session_Records into the Telemetry_Report (Requirement 4, 8).

    Computes, over the supplied records:

      * ``session_count`` — the number of CLASSIFIED sessions in scope. A session
        is classified when it carries a recognized outcome, or is open and has aged
        past ``incomplete_horizon_seconds`` relative to the explicit ``now_ref``
        (=> ``incomplete``). Open-within-horizon sessions (and every open session
        when ``now_ref`` is ``None``) are UNCLASSIFIED and excluded from
        ``session_count`` and every rate denominator (Requirement 1.5, Property 4).
      * ``weak_prior_min_sessions`` / ``weak_prior`` — the configured minimum and
        whether ``session_count`` falls below it (Requirement 4.6, 5.2, Property 13).
      * ``filters`` — the filter skeleton for the report (this pure core applies no
        filtering itself; the CLI pre-filters via ``filter_sessions`` and stamps the
        active filters here, so every key is ``None`` at this layer).
      * ``outcomes`` — conversion / hold / error / incomplete rates (each over
        ``session_count``, ``null`` on an empty scope) plus the raw counts; the four
        rates sum to ``1.0`` over the classified sessions (Requirement 4.1, 4.2,
        Property 9).
      * ``invalidation_rate`` — ``inv / (inv + target)``, ``null`` when that total
        is zero (Requirement 4.3, Property 10).
      * ``watch_cycles`` / ``time_to_decision_s`` distributions and the ``cost``
        block (tool-call / model-turn / resume-count distributions and per-tool
        totals); every distribution is all-``null`` on an empty sample
        (Requirement 4.4, Property 11).
      * ``by_symbol`` / ``by_timeframe`` / ``by_mode`` — disjoint breakdowns, each a
        list of GroupReports partitioning the classified sessions (Requirement 4.5,
        Property 12).

    PURE, DETERMINISTIC, TOTAL (Requirement 8.1-8.4): reads only its arguments,
    never mutates them (Property 18), yields deeply-equal reports for identical
    inputs (Property 16), never raises on degenerate input (zero sessions, zero
    cycles, null timings), and represents every unavailable metric — a ratio with a
    zero denominator, a distribution over an empty sample — as ``null`` (Property
    17). ``now_ref`` is the explicit reference time for the ``incomplete`` horizon,
    kept an argument so the function has no ambient clock dependency.
    """
    source = _as_record_list(records)

    # A usable now_ref must be an observable finite number; anything else means
    # "no reference clock" so open sessions cannot age out (stays deterministic).
    now: Optional[float] = float(now_ref) if _finite_number(now_ref) else None

    min_sessions = _min_sessions_of(config)

    # Classify once; session_count and every metric are over the classified set.
    pairs = _classified(source, config, now)
    classified_records = [record for record, _outcome in pairs]
    session_count = len(pairs)

    return {
        REPORT_SESSION_COUNT: session_count,
        REPORT_WEAK_PRIOR_MIN_SESSIONS: min_sessions,
        REPORT_WEAK_PRIOR: session_count < min_sessions,
        REPORT_FILTERS: {
            GROUP_KEY_SYMBOL: None,
            GROUP_KEY_TIMEFRAME: None,
            GROUP_KEY_MODE: None,
            "since": None,
            "until": None,
        },
        REPORT_OUTCOMES: _outcomes_block(pairs),
        REPORT_INVALIDATION_RATE: _invalidation_rate(classified_records),
        REPORT_WATCH_CYCLES: _watch_cycles_distribution(classified_records),
        REPORT_TIME_TO_DECISION: _distribution(
            [getattr(r, "time_to_decision_s", None) for r in classified_records]
        ),
        REPORT_COST: {
            COST_TOOL_CALLS: _distribution(
                [_int_or_zero(getattr(r, "tool_calls_total", 0)) for r in classified_records]
            ),
            COST_MODEL_TURNS: _distribution(
                [_int_or_zero(getattr(r, "model_turns", 0)) for r in classified_records]
            ),
            COST_RESUME_COUNT: _distribution(
                [_int_or_zero(getattr(r, "resume_count", 0)) for r in classified_records]
            ),
            COST_TOOL_CALLS_BY_NAME: _tool_calls_by_name_totals(classified_records),
        },
        REPORT_BY_SYMBOL: _group_reports(classified_records, GROUP_KEY_SYMBOL, config, now, min_sessions),
        REPORT_BY_TIMEFRAME: _group_reports(classified_records, GROUP_KEY_TIMEFRAME, config, now, min_sessions),
        REPORT_BY_MODE: _group_reports(classified_records, GROUP_KEY_MODE, config, now, min_sessions),
    }


# ── Telemetry_Store (SQLite persistence) ──────────────────────────────────────
# A thin, defensive I/O shell around the pure core, mirroring the
# ``journal._connect`` / ``journal._init_db`` / ``journal._ensure_column``
# convention. The store lives in a DEDICATED database file (``TelemetryConfig.db_path``,
# defaulting to ``telemetry.db`` beside this module) that is SEPARATE from the
# Trade_Journal's ``trade_journal.db`` — telemetry never opens, reads, or writes
# the journal's ``trades`` tables, so it is structurally read-only with respect to
# committed-trade data (Requirement 6.4, 7.1, 7.2). Every DDL/write is guarded so a
# failure logs a ``[Telemetry] WARN: ...`` line (matching ``journal.py``'s
# ``print("[Trade_Journal] WARN: ...")`` convention) and NEVER raises into the
# agent loop or the SSE stream (Requirement 6.2).

# The two tables owned by the Telemetry_Store (Requirement 7.1). No other table is
# ever created or touched — in particular the Trade_Journal's ``trades`` table is
# never referenced.
TABLE_SESSIONS = "sessions"
TABLE_FUNNEL_EVENTS = "funnel_events"

# Forward-compatible columns (Requirement 7.4). These are additive, nullable, and
# unused today; they let the Adaptive Opportunity Engine record an opportunity
# tier and a session budget later without a breaking migration. They are declared
# inline in the ``CREATE TABLE`` for a fresh store AND added idempotently via
# ``_ensure_column`` to an old-shape ``sessions`` table (without touching existing
# rows). ``extra`` is a reserved generic JSON bag.
_SESSIONS_FORWARD_COMPAT_COLUMNS = (
    ("opportunity_tier", "TEXT"),
    ("session_budget", "REAL"),
    ("extra", "TEXT"),
)


def _connect(cfg: TelemetryConfig) -> sqlite3.Connection:
    """Open a connection to the dedicated Telemetry_Store (Requirement 7.1, 7.2).

    Opens ``cfg.db_path`` — a SEPARATE SQLite file from the Trade_Journal (it
    resolves from ``TELEMETRY_DB_PATH`` / the ``telemetry.db`` default, never from
    ``JOURNAL_DB_PATH``) — with the same ``timeout=10.0`` busy timeout as
    ``journal._connect`` so a concurrent writer/reader waits for the lock rather
    than failing fast, and sets ``row_factory = sqlite3.Row`` so loaded rows are
    addressable by column name. Mirrors ``journal._connect`` exactly, differing
    only in taking the resolved config (so the path is not a module global).
    """
    conn = sqlite3.connect(cfg.db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    decl_type: str,
) -> None:
    """Add ``column`` to ``table`` when it is missing (idempotent, guarded).

    Inspects the live schema via ``PRAGMA table_info(<table>)`` and only issues an
    ``ALTER TABLE ... ADD COLUMN`` when the column is absent, so re-running on an
    already-migrated store is a no-op. The ALTER is ADDITIVE only — the new column
    is nullable with no default — so existing rows are preserved untouched
    (Requirement 7.4). This is the mechanism that upgrades an old-shape
    ``sessions`` table (created before the forward-compat columns existed) in
    place, letting the Adaptive Opportunity Engine record an ``opportunity_tier`` /
    ``session_budget`` later without a breaking migration.

    Guarded exactly like ``journal._ensure_column``: any failure logs a
    ``[Telemetry] WARN: ...`` line and is swallowed — it NEVER raises into the
    caller (Requirement 6.2). ``table`` is interpolated from a module-owned
    constant (never user input), so the f-string carries no injection surface.
    """
    try:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl_type}")
    except Exception as e:
        print(f"[Telemetry] WARN: could not ensure column '{column}' on '{table}': {e}")


def _init_db(conn: sqlite3.Connection) -> None:
    """Create the Telemetry_Store schema idempotently (Requirement 7.1, 7.4).

    Creates the two dedicated tables and their indexes with ``IF NOT EXISTS`` (so a
    warm store is untouched) and then runs the idempotent forward-compat
    ``_ensure_column`` migration to bring an OLD-shape ``sessions`` table — one
    created before the ``opportunity_tier`` / ``session_budget`` / ``extra`` columns
    existed — up to the current shape without altering any existing row
    (Requirement 7.4).

      * ``sessions`` — one row per Session_Record: identity (``session_id`` PRIMARY
        KEY, ``thread_id``, symbol / timeframe / mode), timings (``started_at`` /
        ``ended_at`` / ``time_to_decision_s`` / ``suspended_s``), the terminal
        ``outcome`` (NULL while open) and ``hold_reason``, the funnel counters and
        cost proxies (integer, ``NOT NULL DEFAULT 0``), ``tool_calls_by_name`` JSON,
        the nullable ``tokens`` (never fabricated), and the forward-compat
        ``opportunity_tier`` / ``session_budget`` / ``extra`` columns.
      * ``funnel_events`` — the ordered funnel path, one row per FunnelEvent, keyed
        to a session by ``session_id``, ordered by ``seq``.

    The whole body is guarded so any DDL failure logs a ``[Telemetry] WARN: ...``
    line and is swallowed — it NEVER raises into the agent loop or the SSE stream
    (Requirement 6.2), exactly like ``journal._init_db``'s convention. Only these
    two tables are ever created; the Trade_Journal ``trades`` table is never
    referenced (Requirement 6.4, 7.2).
    """
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id     TEXT PRIMARY KEY,
                thread_id      TEXT NOT NULL,
                symbol         TEXT,
                timeframe      TEXT,
                mode           TEXT,
                started_at     REAL NOT NULL,
                ended_at       REAL,
                outcome        TEXT,
                hold_reason    TEXT,
                watch_cycles         INTEGER NOT NULL DEFAULT 0,
                target_events        INTEGER NOT NULL DEFAULT 0,
                invalidation_events  INTEGER NOT NULL DEFAULT 0,
                resume_count         INTEGER NOT NULL DEFAULT 0,
                reasoning_turns      INTEGER NOT NULL DEFAULT 0,
                tool_calls_total     INTEGER NOT NULL DEFAULT 0,
                tool_calls_by_name   TEXT,
                model_turns          INTEGER NOT NULL DEFAULT 0,
                tokens               INTEGER,
                time_to_decision_s   REAL,
                suspended_s          REAL,
                opportunity_tier     TEXT,
                session_budget       REAL,
                extra                TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_thread  ON sessions(thread_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_symbol  ON sessions(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS funnel_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    TEXT NOT NULL,
                seq           INTEGER NOT NULL,
                kind          TEXT NOT NULL,
                ts            REAL,
                trigger_kind  TEXT,
                tool_name     TEXT,
                extra         TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_funnel_session ON funnel_events(session_id)")

        # Additive forward-compat migration for a store whose ``sessions`` table was
        # created before these columns existed (Requirement 7.4). Idempotent and
        # additive-only, so existing rows are preserved untouched.
        for column, decl_type in _SESSIONS_FORWARD_COMPAT_COLUMNS:
            _ensure_column(conn, TABLE_SESSIONS, column, decl_type)

        conn.commit()
    except Exception as e:
        print(f"[Telemetry] WARN: could not initialize telemetry store: {e}")


# ── Persistence + read-only load (save / load_sessions) ───────────────────────
# ``save`` writes one Session_Record and its ordered Funnel_Events into the
# dedicated Telemetry_Store; ``load_sessions`` reads records back, filtered by
# symbol / timeframe / mode / time-range, reconstructing each record with its
# ordered funnel. Both are the thin, defensive I/O shell around the frozen data
# models — every failure logs a ``[Telemetry] WARN: ...`` line (mirroring
# ``journal.py``) and is swallowed so telemetry NEVER raises into the agent loop
# or the SSE stream (Requirement 6.2). Together they satisfy the persist→load
# round-trip (Property 15): identity, counters, timings, outcome, cost proxies,
# and the ordered funnel are all preserved.

# Column order for the ``sessions`` INSERT — kept as a single source of truth so
# the placeholders and the value tuple stay in lock-step. Matches the ``_init_db``
# schema (including the forward-compat ``opportunity_tier`` / ``session_budget`` /
# ``extra`` columns, written from the record when present else NULL).
_SESSION_INSERT_COLUMNS = (
    "session_id",
    "thread_id",
    "symbol",
    "timeframe",
    "mode",
    "started_at",
    "ended_at",
    "outcome",
    "hold_reason",
    "watch_cycles",
    "target_events",
    "invalidation_events",
    "resume_count",
    "reasoning_turns",
    "tool_calls_total",
    "tool_calls_by_name",
    "model_turns",
    "tokens",
    "time_to_decision_s",
    "suspended_s",
    "opportunity_tier",
    "session_budget",
    "extra",
)

# Column order for the ``funnel_events`` INSERT (the autoincrement ``id`` is
# assigned by SQLite and is not persisted from the model).
_FUNNEL_INSERT_COLUMNS = (
    "session_id",
    "seq",
    "kind",
    "ts",
    "trigger_kind",
    "tool_name",
    "extra",
)


def _json_dumps_or_none(value: Any) -> Optional[str]:
    """JSON-encode ``value`` for a TEXT column; ``None`` stays SQL ``NULL``.

    Returns ``None`` (persisted as SQL ``NULL``) when the value is ``None`` or is
    not JSON-serializable, so a malformed ``tool_calls_by_name`` / ``extra`` bag
    degrades to "no JSON stored" rather than raising. Mirrors the defensive
    ``json.dumps`` guard used across ``journal.py`` / ``trade_manager.py``.
    """
    if value is None:
        return None
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None


def _json_loads_or_none(text: Any) -> Any:
    """Decode a JSON TEXT column back to a Python value; ``None`` on absence/error.

    Returns ``None`` when the stored value is SQL ``NULL`` / empty / not a string,
    or cannot be parsed as JSON — so a corrupt cell degrades gracefully rather than
    raising. Pure and total.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _decoded_tool_calls_by_name(text: Any) -> Dict[str, int]:
    """Decode the ``tool_calls_by_name`` cell to a ``{name: count}`` dict (total).

    A stored JSON object round-trips to an equal dict; a ``NULL`` / malformed /
    non-object cell degrades to an empty dict so the reconstructed record always
    carries a dict for ``tool_calls_by_name`` (its declared type), never ``None``.
    """
    decoded = _json_loads_or_none(text)
    if isinstance(decoded, dict):
        return decoded
    return {}


def save(cfg: TelemetryConfig, record: SessionRecord) -> None:
    """Persist one ``SessionRecord`` and its ordered ``FunnelEvent``s (R7.1).

    Opens the dedicated Telemetry_Store (``_connect`` + ``_init_db``) and writes:

      * one ``sessions`` row via ``INSERT OR REPLACE`` keyed on ``session_id`` (so
        re-saving the same Session is an idempotent UPSERT rather than a duplicate),
        JSON-encoding ``tool_calls_by_name`` into its TEXT column and leaving
        ``tokens`` SQL ``NULL`` when the run exposed no token usage (never
        fabricated, Requirement 3.4); the forward-compat ``opportunity_tier`` /
        ``session_budget`` / ``extra`` columns are written from the record when it
        carries them (via ``getattr``) and are ``NULL`` otherwise;
      * the ordered ``funnel_events`` rows: any prior funnel rows for the
        ``session_id`` are cleared first (keeping the UPSERT clean) and the record's
        ``FunnelEvent``s are re-inserted in ``seq`` order, JSON-encoding each
        event's ``extra`` bag.

    BEST-EFFORT and NON-INVASIVE: the whole body is guarded so ANY failure (a
    closed/locked DB, a non-serializable payload, a malformed record) logs a
    ``[Telemetry] WARN: ...`` line and is swallowed — it NEVER raises into the
    agent loop or the SSE stream (Requirement 6.2). Only the dedicated ``sessions``
    / ``funnel_events`` tables are touched; the Trade_Journal is never opened
    (Requirement 6.4, 7.2).
    """
    session_id = getattr(record, "session_id", None)
    try:
        conn = _connect(cfg)
    except Exception as e:  # pragma: no cover - defensive
        print(f"[Telemetry] WARN: could not open store to save session '{session_id}': {e}")
        return

    try:
        _init_db(conn)

        session_values = (
            record.session_id,
            record.thread_id,
            record.symbol,
            record.timeframe,
            record.mode,
            record.started_at,
            record.ended_at,
            record.outcome,
            record.hold_reason,
            _int_or_zero(record.watch_cycles),
            _int_or_zero(record.target_events),
            _int_or_zero(record.invalidation_events),
            _int_or_zero(record.resume_count),
            _int_or_zero(record.reasoning_turns),
            _int_or_zero(record.tool_calls_total),
            _json_dumps_or_none(record.tool_calls_by_name),
            _int_or_zero(record.model_turns),
            record.tokens if _finite_number(record.tokens) else None,
            record.time_to_decision_s,
            record.suspended_s,
            # Forward-compat columns: sourced from the record when present, else NULL.
            getattr(record, "opportunity_tier", None),
            getattr(record, "session_budget", None),
            _json_dumps_or_none(getattr(record, "extra", None)),
        )

        placeholders = ", ".join("?" for _ in _SESSION_INSERT_COLUMNS)
        columns = ", ".join(_SESSION_INSERT_COLUMNS)
        conn.execute(
            f"INSERT OR REPLACE INTO {TABLE_SESSIONS} ({columns}) VALUES ({placeholders})",
            session_values,
        )

        # Clear any prior funnel for this session so the UPSERT does not accumulate
        # duplicate rows, then re-insert the ordered funnel.
        conn.execute(
            f"DELETE FROM {TABLE_FUNNEL_EVENTS} WHERE session_id = ?",
            (record.session_id,),
        )

        funnel = getattr(record, "funnel", None)
        if isinstance(funnel, (list, tuple)):
            funnel_placeholders = ", ".join("?" for _ in _FUNNEL_INSERT_COLUMNS)
            funnel_columns = ", ".join(_FUNNEL_INSERT_COLUMNS)
            insert_funnel_sql = (
                f"INSERT INTO {TABLE_FUNNEL_EVENTS} ({funnel_columns}) "
                f"VALUES ({funnel_placeholders})"
            )
            for event in funnel:
                conn.execute(
                    insert_funnel_sql,
                    (
                        record.session_id,
                        _int_or_zero(getattr(event, "seq", 0)),
                        getattr(event, "kind", None),
                        getattr(event, "ts", None),
                        getattr(event, "trigger_kind", None),
                        getattr(event, "tool_name", None),
                        _json_dumps_or_none(getattr(event, "extra", None)),
                    ),
                )

        conn.commit()
    except Exception as e:
        print(f"[Telemetry] WARN: could not save session '{session_id}': {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_sessions(
    cfg: TelemetryConfig,
    *,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    mode: Optional[str] = None,
    start: Optional[float] = None,
    end: Optional[float] = None,
) -> List[SessionRecord]:
    """Read Session_Records back from the store, filtered (Requirement 7.1, 5.3).

    A READ-ONLY loader that selects ``sessions`` rows matching every supplied
    filter and reconstructs each into an immutable ``SessionRecord`` carrying its
    ordered funnel:

      * ``symbol`` / ``timeframe`` / ``mode`` — equality predicates (a ``None``
        filter imposes no constraint on that column);
      * ``start`` / ``end`` — an INCLUSIVE ``started_at`` range (``start`` alone is
        only a lower bound, ``end`` alone only an upper bound).

    Every filter value is bound through a PARAMETERIZED placeholder (``?``) — no
    value is ever string-interpolated into the SQL — so the query carries no
    injection surface (Requirement 5.3). For each selected session its funnel is
    loaded with ``SELECT ... WHERE session_id = ? ORDER BY seq`` so the ordered
    path (analyze → watch → invalidate → re-watch → …) is reconstructed exactly,
    and ``tool_calls_by_name`` / each funnel event's ``extra`` are JSON-decoded
    back to their Python shapes. Rows are returned ordered by ``started_at`` then
    ``session_id`` for deterministic output.

    BEST-EFFORT: the whole body is guarded so ANY failure (a missing/locked DB, a
    corrupt row) logs a ``[Telemetry] WARN: ...`` line and returns ``[]`` rather
    than raising (Requirement 6.2). This loader only reads the dedicated store; it
    performs no writes and never opens the Trade_Journal (Requirement 6.4, 7.2).
    """
    try:
        conn = _connect(cfg)
    except Exception as e:  # pragma: no cover - defensive
        print(f"[Telemetry] WARN: could not open store to load sessions: {e}")
        return []

    try:
        _init_db(conn)

        clauses: List[str] = []
        params: List[Any] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if timeframe is not None:
            clauses.append("timeframe = ?")
            params.append(timeframe)
        if mode is not None:
            clauses.append("mode = ?")
            params.append(mode)
        if start is not None:
            clauses.append("started_at >= ?")
            params.append(start)
        if end is not None:
            clauses.append("started_at <= ?")
            params.append(end)

        sql = f"SELECT * FROM {TABLE_SESSIONS}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at ASC, session_id ASC"

        session_rows = conn.execute(sql, tuple(params)).fetchall()

        records: List[SessionRecord] = []
        for row in session_rows:
            funnel = _load_funnel(conn, row["session_id"])
            records.append(
                SessionRecord(
                    session_id=row["session_id"],
                    thread_id=row["thread_id"],
                    symbol=row["symbol"],
                    timeframe=row["timeframe"],
                    mode=row["mode"],
                    started_at=row["started_at"],
                    ended_at=row["ended_at"],
                    outcome=row["outcome"],
                    hold_reason=row["hold_reason"],
                    watch_cycles=_int_or_zero(row["watch_cycles"]),
                    target_events=_int_or_zero(row["target_events"]),
                    invalidation_events=_int_or_zero(row["invalidation_events"]),
                    resume_count=_int_or_zero(row["resume_count"]),
                    reasoning_turns=_int_or_zero(row["reasoning_turns"]),
                    tool_calls_total=_int_or_zero(row["tool_calls_total"]),
                    tool_calls_by_name=_decoded_tool_calls_by_name(row["tool_calls_by_name"]),
                    model_turns=_int_or_zero(row["model_turns"]),
                    tokens=row["tokens"] if _finite_number(row["tokens"]) else None,
                    time_to_decision_s=row["time_to_decision_s"],
                    suspended_s=row["suspended_s"],
                    funnel=funnel,
                )
            )

        return records
    except Exception as e:
        print(f"[Telemetry] WARN: could not load sessions: {e}")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _load_funnel(conn: sqlite3.Connection, session_id: str) -> List[FunnelEvent]:
    """Load one Session's ordered funnel from ``funnel_events`` (helper, total).

    Selects the session's funnel rows ordered by ``seq`` (a PARAMETERIZED
    ``session_id`` predicate — no interpolation) and reconstructs each into an
    immutable ``FunnelEvent``, JSON-decoding the ``extra`` bag back to a dict / None.
    Returns an empty list on any failure so ``load_sessions`` still yields the
    record (guarded, never raises).
    """
    try:
        funnel_rows = conn.execute(
            f"SELECT seq, kind, ts, trigger_kind, tool_name, extra "
            f"FROM {TABLE_FUNNEL_EVENTS} WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        ).fetchall()
    except Exception as e:
        print(f"[Telemetry] WARN: could not load funnel for session '{session_id}': {e}")
        return []

    funnel: List[FunnelEvent] = []
    for frow in funnel_rows:
        funnel.append(
            FunnelEvent(
                seq=_int_or_zero(frow["seq"]),
                kind=frow["kind"],
                ts=frow["ts"],
                trigger_kind=frow["trigger_kind"],
                tool_name=frow["tool_name"],
                extra=_json_loads_or_none(frow["extra"]),
            )
        )
    return funnel


# ══════════════════════════════════════════════════════════════════════════════
# Recording layer — background writer (task 9.1)
#
# The recording layer keeps SQLite entirely OFF the hot path (Requirement 6.3).
# The request/stream coroutine (``observe_stream``, task 9.2) only ever performs a
# non-blocking ``put_nowait`` of a lightweight ``Observation`` onto a bounded
# in-memory queue; ALL persistence happens on a single background daemon thread
# (``SessionWriter``) that drains the queue, folds observations into per-``thread_id``
# ``SessionState`` accumulators, and persists finalized ``SessionRecord``s via
# :func:`save`. If the queue is full an observation is DROPPED (telemetry degrades
# to "missed a datapoint" rather than ever back-pressuring the live stream), and
# every drain+write is guarded so a failed write is logged and skipped and the
# thread keeps running (Requirement 6.2).
# ══════════════════════════════════════════════════════════════════════════════

# How long (seconds) to coalesce "queue full — dropped observation(s)" logs so a
# saturated queue cannot spam the log (Requirement 6.3, design "Error Handling").
_DROP_LOG_INTERVAL_S = 30.0

# Sentinel enqueued by ``SessionWriter.stop`` to cleanly unwind the daemon thread
# after it has drained everything ahead of it in the queue.
_WRITER_SENTINEL = object()


@dataclass(frozen=True)
class Observation:
    """One lightweight, immutable observation enqueued by the observation tee.

    This is the message shape that ``observe_stream`` (task 9.2) produces for each
    observed SSE frame and the ``SessionWriter`` consumes off the hot path. It is
    deliberately minimal — it carries the Session identity (``thread_id``), the
    ``/run`` / ``/resume`` entry metadata (``entry``), and the observed event —
    so the producer only does O(1) work and the (potentially expensive) funnel
    reconstruction / persistence happens on the background thread.

    Fields:
      * ``thread_id`` — the key that folds a ``/run`` and all of its ``/resume``
        continuations into one Session (Requirement 1.2).
      * ``entry`` — the :class:`RunEntry` for the stream this frame belongs to
        (``kind`` "run" | "resume", plus ``symbol`` / ``timeframe`` / ``mode`` and,
        for a resume, ``trigger_kind``). All frames of a single ``/run`` (or
        ``/resume``) invocation share the same ``entry``.
      * ``event_name`` — the observed SSE event name (one of the ``EVENT_*``
        literals, e.g. ``REASONING`` / ``TOOL_CALL_START`` / ``DECISION`` /
        ``ERROR`` / ``RUN_FINISHED``), or ``None`` for a pure boundary marker.
      * ``payload`` — the event payload dict (carries the tool name, decision
        record, run status, token usage, etc.), or ``None``.
      * ``ts`` — the wall-clock time (seconds) at which the frame was observed, so
        the writer can stamp ``started_at`` / ``ended_at`` and watch/resume
        suspend intervals even when the payload itself carries no timestamp.
      * ``entry_started`` — ``True`` on the first observation of a ``/run`` or
        ``/resume`` stream (opens a fresh entry buffer; a "run" also opens a fresh
        Session).
      * ``entry_ended`` — ``True`` on the final observation of the stream (the
        source iterator is exhausted); triggers folding the buffered entry into the
        Session and a persist.

    Frozen: an observation is an immutable hand-off from the producer to the writer.
    """

    thread_id: str
    entry: RunEntry
    event_name: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    ts: Optional[float] = None
    entry_started: bool = False
    entry_ended: bool = False

    # ── Convenience constructors for the observation tee (task 9.2) ───────────
    @staticmethod
    def start(thread_id: str, entry: "RunEntry", ts: Optional[float] = None) -> "Observation":
        """A marker opening a ``/run`` or ``/resume`` stream for ``thread_id``."""
        return Observation(thread_id=thread_id, entry=entry, ts=ts, entry_started=True)

    @staticmethod
    def frame(
        thread_id: str,
        entry: "RunEntry",
        event_name: Optional[str],
        payload: Optional[Dict[str, Any]] = None,
        ts: Optional[float] = None,
        *,
        entry_started: bool = False,
        entry_ended: bool = False,
    ) -> "Observation":
        """An observation for one observed SSE frame."""
        return Observation(
            thread_id=thread_id,
            entry=entry,
            event_name=event_name,
            payload=payload,
            ts=ts,
            entry_started=entry_started,
            entry_ended=entry_ended,
        )

    @staticmethod
    def end(thread_id: str, entry: "RunEntry", ts: Optional[float] = None) -> "Observation":
        """A marker closing a ``/run`` or ``/resume`` stream for ``thread_id``."""
        return Observation(thread_id=thread_id, entry=entry, ts=ts, entry_ended=True)


@dataclass
class _EntryBuffer:
    """Per-entry working buffer the writer accumulates before folding (internal).

    Holds the entry metadata and the ordered ``(event_name, payload)`` tuples
    observed for one ``/run`` or ``/resume`` stream, plus the last observed
    timestamp (used as a timing fallback). It is drained into the Session's
    ``SessionState`` via :func:`interpret_events` when the entry ends.
    """

    entry: RunEntry
    events: List[Tuple[str, Any]] = field(default_factory=list)
    last_ts: Optional[float] = None


def _terminal_signals(
    events: List[Tuple[str, Any]],
) -> Tuple[Optional[dict], bool, Optional[str], Optional[float]]:
    """Extract terminal-classification signals from a buffered entry (total).

    Scans the buffered ``(event_name, payload)`` tuples for the inputs
    :func:`classify_outcome` needs: the last ``DECISION`` payload (the committed
    decision record), whether any ``ERROR`` was observed, the last ``RUN_FINISHED``
    status, and the timestamp of the terminal event (for ``ended_at``). Pure and
    total — never raises.
    """
    decision: Optional[dict] = None
    errored = False
    run_status: Optional[str] = None
    terminal_ts: Optional[float] = None
    for item in events:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        name, payload = item[0], item[1]
        if name == EVENT_DECISION and isinstance(payload, dict):
            decision = payload
            ts = _extract_ts(payload)
            if ts is not None:
                terminal_ts = ts
        elif name == EVENT_ERROR:
            errored = True
            ts = _extract_ts(payload)
            if ts is not None:
                terminal_ts = ts
        elif name == EVENT_RUN_FINISHED and isinstance(payload, dict):
            status = payload.get("status")
            if isinstance(status, str):
                run_status = status
    return decision, errored, run_status, terminal_ts


def _extract_tokens(events: List[Tuple[str, Any]]) -> Optional[int]:
    """Extract exposed model token usage from a buffered entry, else ``None``.

    Reads a real integer token count from an event payload (a top-level
    ``tokens`` / ``total_tokens``, or a nested ``usage.total_tokens``) when the run
    exposed one, returning the last such value seen. Returns ``None`` when no token
    usage is exposed — a token count is NEVER fabricated (Requirement 3.4). Pure
    and total — never raises.
    """
    tokens: Optional[int] = None
    for item in events:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        payload = item[1]
        if not isinstance(payload, dict):
            continue
        for key in ("tokens", "total_tokens"):
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                tokens = value
        usage = payload.get("usage")
        if isinstance(usage, dict):
            value = usage.get("total_tokens")
            if isinstance(value, int) and not isinstance(value, bool):
                tokens = value
    return tokens


class SessionWriter:
    """Background daemon thread that drains observations into the Telemetry_Store.

    The single off-hot-path sink for the recording layer (Requirement 6.3). The
    producer (``observe_stream``, task 9.2) calls :meth:`enqueue` — an O(1),
    non-blocking ``put_nowait`` that DROPS the observation when the bounded queue
    is full rather than ever blocking the live SSE stream. A single daemon thread
    drains the queue and, keyed by ``thread_id``:

      * opens a fresh :class:`SessionState` on a ``/run`` entry (a new ``/run`` on a
        reused ``thread_id`` starts a fresh Session), or attaches a ``/resume`` to
        the open Session for that ``thread_id`` (Requirement 1.1, 1.2);
      * buffers each entry's ``(event_name, payload)`` frames and, when the entry
        ends, folds them via :func:`interpret_events` into the Session's ordered
        funnel and updates the funnel counters and cost proxies (Requirement 2, 3);
      * on a terminal entry (a committed ``DECISION`` or an ``ERROR``) classifies
        the outcome via :func:`classify_outcome` and stamps ``ended_at``
        (Requirement 1.3, 1.4);
      * calls :func:`finalize_session` and persists the record via :func:`save`
        (Requirement 7.1). A still-paused Session is persisted as an OPEN record
        (``outcome`` NULL) so the aggregation core can later classify it
        ``incomplete`` past the horizon (Requirement 1.5); its accumulator is kept
        in memory so a subsequent ``/resume`` folds into the same Session, and is
        only dropped once the Session reaches a terminal outcome.

    Best-effort and non-invasive (Requirement 6.2): every drain+write is wrapped in
    a guard so a failed observation is logged (``[Telemetry] WARN: ...``) and
    skipped and the thread continues — a telemetry failure can never kill the
    writer thread, raise into the agent loop, or stall the SSE stream.

    Testing hooks: :meth:`start` launches the daemon thread (idempotent),
    :meth:`flush` blocks until the queue is fully drained, and :meth:`stop` drains
    what is enqueued and joins the thread via a sentinel. ``save_fn`` is injectable
    so tests can capture persisted records without a real store.
    """

    def __init__(
        self,
        cfg: Optional[TelemetryConfig] = None,
        *,
        maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        save_fn: Any = save,
    ) -> None:
        try:
            bound = int(maxsize)
        except (TypeError, ValueError):
            bound = DEFAULT_QUEUE_MAXSIZE
        if bound <= 0:
            bound = DEFAULT_QUEUE_MAXSIZE
        self._cfg = cfg
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=bound)
        self._save_fn = save_fn if callable(save_fn) else save
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._started = False
        # Per-thread_id accumulators and in-flight entry buffers.
        self._states: Dict[str, SessionState] = {}
        self._buffers: Dict[str, _EntryBuffer] = {}
        # Drop bookkeeping (coalesced logging so a full queue cannot spam).
        self._dropped = 0
        self._last_drop_log = 0.0

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self) -> "SessionWriter":
        """Start the background daemon thread (idempotent). Never raises."""
        with self._lock:
            if self._started and self._thread is not None and self._thread.is_alive():
                return self
            if self._cfg is None:
                try:
                    self._cfg = resolve_telemetry_config()
                except Exception:  # pragma: no cover - defensive
                    self._cfg = None
            self._thread = threading.Thread(
                target=self._run,
                name="telemetry-session-writer",
                daemon=True,
            )
            self._started = True
            self._thread.start()
            return self

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        """Drain what is enqueued, then unwind and join the thread. Never raises."""
        with self._lock:
            thread = self._thread
        if thread is None:
            return
        try:
            self._queue.put(_WRITER_SENTINEL)
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            thread.join(timeout)
        except Exception:  # pragma: no cover - defensive
            pass
        with self._lock:
            self._started = False

    def flush(self, timeout: Optional[float] = 5.0) -> bool:
        """Block until every enqueued observation has been processed (for tests).

        Returns ``True`` when the queue drained within ``timeout`` (or ``timeout``
        is ``None``), ``False`` if it timed out. Never raises.
        """
        deadline = None if timeout is None else (time.time() + max(0.0, float(timeout)))
        while True:
            try:
                pending = self._queue.unfinished_tasks  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                pending = self._queue.qsize()
            if pending <= 0:
                return True
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(0.005)

    # ── Producer side (hot path) — O(1), non-blocking, drop-on-full ───────────
    def enqueue(self, observation: Any) -> bool:
        """Enqueue one observation without ever blocking (Requirement 6.3).

        Uses a non-blocking ``put_nowait``; on a full queue the observation is
        DROPPED (logged at most periodically) rather than blocking the live SSE
        stream. Returns ``True`` if the observation was accepted, ``False`` if it
        was dropped. NEVER raises into the caller (Requirement 6.2).
        """
        try:
            self._queue.put_nowait(observation)
            return True
        except queue.Full:
            self._note_drop()
            return False
        except Exception:  # pragma: no cover - defensive
            return False

    # ``put`` alias mirrors the queue vocabulary the tee may reach for.
    put = enqueue

    def _note_drop(self) -> None:
        """Record a dropped observation, logging at most once per interval."""
        self._dropped += 1
        now = time.time()
        if now - self._last_drop_log >= _DROP_LOG_INTERVAL_S:
            print(
                f"[Telemetry] WARN: observation queue full; "
                f"dropped {self._dropped} observation(s) so far"
            )
            self._last_drop_log = now

    # ── Consumer side (background thread) ─────────────────────────────────────
    def _run(self) -> None:
        """Drain the queue forever; each drain+write is individually guarded."""
        if self._cfg is None:
            try:
                self._cfg = resolve_telemetry_config()
            except Exception:  # pragma: no cover - defensive
                self._cfg = None
        while True:
            item = self._queue.get()
            try:
                if item is _WRITER_SENTINEL:
                    return
                # Guard EACH observation so a bad one is logged and skipped and the
                # thread keeps running (Requirement 6.2 — a failed write must never
                # kill the writer thread).
                self._process(item)
            except Exception as e:  # pragma: no cover - defensive
                print(f"[Telemetry] WARN: session writer failed to process observation: {e}")
            finally:
                try:
                    self._queue.task_done()
                except Exception:  # pragma: no cover - defensive
                    pass

    def _process(self, obs: Observation) -> None:
        """Fold one observation into its Session and persist on entry completion."""
        thread_id = getattr(obs, "thread_id", None)
        if thread_id is None:
            return

        # Open a fresh entry buffer on an explicit start marker, or lazily when the
        # first frame of an unseen stream arrives (defensive: tolerate a missing
        # start marker).
        if getattr(obs, "entry_started", False) or thread_id not in self._buffers:
            self._start_entry(obs)

        buf = self._buffers.get(thread_id)
        if buf is None:  # pragma: no cover - defensive
            return

        if getattr(obs, "event_name", None) is not None:
            self._record_event(obs, buf)

        if getattr(obs, "entry_ended", False):
            self._end_entry(obs)

    def _new_state(self, thread_id: str, entry: RunEntry, ts: Optional[float]) -> SessionState:
        """Create a fresh accumulator, stamping ``started_at`` from ``ts`` or now."""
        started = float(ts) if _finite_number(ts) else time.time()
        return SessionState(
            thread_id=thread_id,
            symbol=getattr(entry, "symbol", None),
            timeframe=getattr(entry, "timeframe", None),
            mode=getattr(entry, "mode", None),
            started_at=started,
        )

    def _start_entry(self, obs: Observation) -> None:
        """Begin buffering a ``/run`` or ``/resume`` stream for its ``thread_id``."""
        thread_id = obs.thread_id
        entry = getattr(obs, "entry", None)
        buf = _EntryBuffer(entry=entry, last_ts=obs.ts)
        self._buffers[thread_id] = buf

        kind = getattr(entry, "kind", None)
        if kind == ENTRY_KIND_RESUME:
            # Attach to the open Session for this thread_id; if none is open
            # (e.g. the writer started mid-hunt) open one from the resume.
            if thread_id not in self._states:
                self._states[thread_id] = self._new_state(thread_id, entry, obs.ts)
        else:
            # A ``/run`` (or an unknown kind) opens a FRESH Session, replacing any
            # still-open accumulator for a reused thread_id (Requirement 1.2).
            self._states[thread_id] = self._new_state(thread_id, entry, obs.ts)

    def _record_event(self, obs: Observation, buf: _EntryBuffer) -> None:
        """Append one observed frame to the entry buffer (stamping ts when needed)."""
        payload = obs.payload
        if isinstance(payload, dict):
            # Ensure interpret_events / finalize can see a timestamp even when the
            # payload itself carries none (the tee stamps ``obs.ts``). Copy so the
            # producer's payload is never mutated.
            if obs.ts is not None and _extract_ts(payload) is None:
                payload = {**payload, "ts": obs.ts}
        else:
            payload = {} if obs.ts is None else {"ts": obs.ts}
        buf.events.append((obs.event_name, payload))
        if obs.ts is not None:
            buf.last_ts = obs.ts

    def _fold_fragment(self, state: SessionState, fragment: List[FunnelEvent]) -> None:
        """Fold one entry's derived funnel fragment into the Session accumulator.

        Appends each fragment event to the Session's ordered funnel with a
        continuing, contiguous ``seq`` and updates the funnel counters and cost
        proxies. A ``watch_registered`` counts as both a Watch_Cycle and a tool
        call (a ``watch_price_condition`` registration is a tool call too), and its
        start timestamp is recorded for the suspend-interval computation.
        """
        for event in fragment:
            new_seq = len(state.funnel)
            state.funnel.append(
                FunnelEvent(
                    seq=new_seq,
                    kind=getattr(event, "kind", None),
                    ts=getattr(event, "ts", None),
                    trigger_kind=getattr(event, "trigger_kind", None),
                    tool_name=getattr(event, "tool_name", None),
                    extra=getattr(event, "extra", None),
                )
            )
            kind = getattr(event, "kind", None)
            if kind == FUNNEL_WATCH_REGISTERED:
                state.watch_cycles += 1
                state.tool_calls_total += 1
                name = getattr(event, "tool_name", None) or WATCH_TOOL_NAME
                state.tool_calls_by_name[name] = state.tool_calls_by_name.get(name, 0) + 1
                ts = getattr(event, "ts", None)
                if _finite_number(ts):
                    state.watch_starts.append(float(ts))
            elif kind == FUNNEL_TOOL_CALL:
                state.tool_calls_total += 1
                name = getattr(event, "tool_name", None) or "unknown"
                state.tool_calls_by_name[name] = state.tool_calls_by_name.get(name, 0) + 1
            elif kind == FUNNEL_REASONING_TURN:
                state.reasoning_turns += 1
                state.model_turns += 1
            elif kind == FUNNEL_RESUMED:
                state.resume_count += 1
                if getattr(event, "trigger_kind", None) == TRIGGER_INVALIDATION:
                    state.invalidation_events += 1
                else:
                    state.target_events += 1

    def _end_entry(self, obs: Observation) -> None:
        """Fold a completed entry into its Session, finalize, and persist."""
        thread_id = obs.thread_id
        buf = self._buffers.pop(thread_id, None)
        if buf is None:  # pragma: no cover - defensive
            return

        state = self._states.get(thread_id)
        if state is None:
            state = self._new_state(thread_id, buf.entry, buf.last_ts)
            self._states[thread_id] = state

        # Derive the ordered funnel for this entry (pure) and fold it into the
        # Session, updating counters and cost proxies.
        fragment = interpret_events(buf.entry, buf.events)
        self._fold_fragment(state, fragment)

        # Cost proxy: record real token usage only when the run exposed it (R3.4).
        tokens = _extract_tokens(buf.events)
        if tokens is not None:
            state.tokens = tokens

        # Terminal classification (Requirement 1.3, 1.4).
        decision, errored, run_status, terminal_ts = _terminal_signals(buf.events)
        terminal = decision is not None or errored
        if terminal:
            outcome, hold_reason = classify_outcome(decision, run_status, errored)
            state.outcome = outcome
            state.hold_reason = hold_reason
            if _finite_number(terminal_ts):
                state.ended_at = float(terminal_ts)
            elif _finite_number(buf.last_ts):
                state.ended_at = float(buf.last_ts)
            else:
                state.ended_at = time.time()

            # ── Adaptive Opportunity Engine measurement (R9.3) ────────────────
            # Best-effort capture of the committed tier + the bounded-hunt
            # termination reason from the DECISION payload. Wrapped so telemetry
            # can NEVER break the stream (R10.3); a missing field stays None.
            try:
                if isinstance(decision, dict):
                    tier = decision.get("opportunity_tier")
                    if isinstance(tier, str) and tier:
                        state.opportunity_tier = tier
                    reason = decision.get("reason")
                    if reason in ("watch-cap-reached", "session-budget-exhausted"):
                        state.opportunity_termination_reason = reason
            except Exception as _opp_err:  # noqa: BLE001
                print(f"[Telemetry] WARN: opportunity capture failed: {_opp_err}")

        # Fold into an immutable record (pure) and persist (guarded). A still-open
        # Session is persisted as an OPEN record so aggregation can later classify
        # it ``incomplete`` past the horizon (Requirement 1.5).
        record = finalize_session(state)
        try:
            self._save_fn(self._cfg, record)
        except Exception as e:  # pragma: no cover - defensive
            print(
                f"[Telemetry] WARN: session writer failed to persist session "
                f"'{getattr(record, 'session_id', None)}': {e}"
            )

        # Drop the accumulator only once the Session has reached a terminal
        # outcome; a paused Session is kept so a subsequent ``/resume`` folds in.
        if terminal:
            self._states.pop(thread_id, None)


# ── Shared writer singleton (used by the observation tee, task 9.2) ───────────
_WRITER_SINGLETON: Optional[SessionWriter] = None
_WRITER_SINGLETON_LOCK = threading.Lock()


def get_session_writer() -> Optional[SessionWriter]:
    """Return the process-wide, lazily-started :class:`SessionWriter` (best-effort).

    The observation tee reaches for this shared writer so a single background
    thread services every stream. Creation/start is guarded so a failure degrades
    to ``None`` (no telemetry) rather than raising into the caller (Requirement
    6.2).
    """
    global _WRITER_SINGLETON
    try:
        with _WRITER_SINGLETON_LOCK:
            if _WRITER_SINGLETON is None:
                _WRITER_SINGLETON = SessionWriter().start()
            return _WRITER_SINGLETON
    except Exception:  # pragma: no cover - defensive
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Recording layer — observation tee (task 9.2)
#
# ``observe_stream`` is the single wiring seam ``main.py`` wraps around the existing
# SSE ``event_generator``. It is a PASSTHROUGH TEE: it re-yields every source frame
# UNCHANGED and in the same order (so the live stream is byte-for-byte identical —
# Requirement 6.1, Property 20) while enqueuing a lightweight ``Observation`` for
# each frame onto the background ``SessionWriter``'s bounded queue OFF THE HOT PATH
# (a non-blocking ``put_nowait`` — Requirement 6.3). Every observation step is
# individually guarded so a telemetry bug can never drop, delay, reorder, or raise
# into the live SSE stream (Requirement 6.2, Property 21). It never swallows the
# SOURCE's own exceptions — those belong to the stream and propagate normally —
# but it always enqueues the closing marker in a ``finally`` so the writer learns
# the entry boundary even when the source raises.
# ══════════════════════════════════════════════════════════════════════════════


def _parse_sse_frame(frame: Any) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Best-effort parse of one SSE frame into ``(event_name, payload)`` (total).

    The frames produced by ``stream_events.format_sse`` have the shape
    ``"event: {name}\\ndata: {json}\\n\\n"`` — an ``event:`` line naming the event
    and one (or more) ``data:`` line(s) carrying a JSON object. This helper reads
    the event name from the ``event:`` line and JSON-decodes the concatenated
    ``data:`` payload into a dict, so the writer can reconstruct the funnel from
    the observation.

    It is BEST-EFFORT and TOTAL: a non-string frame, a frame with no ``event:``
    line, or a ``data:`` payload that is not valid JSON (or decodes to a non-dict)
    degrades to ``(name_or_None, None)`` rather than raising — the caller still
    enqueues an observation (or skips it) and, crucially, ALWAYS re-yields the
    frame. Never raises (Requirement 6.2).
    """
    if not isinstance(frame, str):
        return None, None

    event_name: Optional[str] = None
    data_parts: List[str] = []
    for line in frame.split("\n"):
        if line.startswith("event:"):
            name = line[len("event:"):].strip()
            event_name = name or None
        elif line.startswith("data:"):
            # SSE strips a single leading space after the colon.
            data_parts.append(line[len("data:"):].lstrip(" "))

    payload: Optional[Dict[str, Any]] = None
    if data_parts:
        raw = "\n".join(data_parts).strip()
        if raw:
            try:
                decoded = json.loads(raw)
            except (ValueError, TypeError):
                decoded = None
            if isinstance(decoded, dict):
                payload = decoded

    return event_name, payload


def _safe_enqueue(writer: Any, observation: Any) -> None:
    """Enqueue one observation onto the writer, swallowing ALL failures (total).

    Wraps the writer's non-blocking ``enqueue`` in a guard so neither a missing
    writer, a full queue, nor any unexpected error can propagate into the live SSE
    stream (Requirement 6.2, Property 21). Does nothing when the writer is
    unavailable. Never raises.
    """
    if writer is None:
        return
    try:
        writer.enqueue(observation)
    except Exception:  # pragma: no cover - defensive; enqueue is already guarded
        pass


async def observe_stream(
    thread_id: str,
    entry: RunEntry,
    source: AsyncIterator[str],
) -> AsyncIterator[str]:
    """Passthrough tee: re-yield every source SSE frame while observing it (R6.1).

    The single seam ``main.py`` wraps around the existing ``event_generator``. For
    EACH frame the source yields, this generator FIRST re-yields the frame
    UNCHANGED (byte-for-byte, same order — so the live stream the client sees is
    identical, Requirement 6.1, Property 20), and only THEN enqueues a lightweight
    :class:`Observation` for it onto the shared background :class:`SessionWriter`
    via a non-blocking ``put_nowait`` (OFF the hot path — Requirement 6.3). The
    frame is parsed best-effort (:func:`_parse_sse_frame`) to recover its
    ``event_name`` / ``payload`` for the observation; a parse failure still enqueues
    a minimal observation and, above all, never prevents the frame from being
    re-yielded.

    Markers frame the entry boundaries for the writer: an :meth:`Observation.start`
    marker is enqueued BEFORE iterating (so the writer opens a fresh buffer — and,
    for a ``/run``, a fresh Session — for this ``thread_id``), and an
    :meth:`Observation.end` marker is enqueued when the source is exhausted (so the
    writer folds the buffered entry into the Session and persists it). The end
    marker is emitted in a ``finally`` so it is enqueued EVEN IF the source raises.
    Every observation carries a wall-clock ``ts`` (``time.time()``) so the writer
    can stamp ``started_at`` / ``ended_at`` and the watch→resume suspend intervals.

    Non-invasive and best-effort (Requirement 2.5, 6.1, 6.2, Property 20, 21):

      * It NEVER reorders, drops, adds to, or alters a source frame — the source
        frame is always yielded, and yielded before any observation work.
      * ALL observation logic is wrapped in guards, so a telemetry bug (a failed
        parse, a full or broken queue, a missing writer) can never drop, delay,
        reorder, or raise into the live stream — telemetry simply "misses a
        datapoint".
      * It does NOT swallow the SOURCE's own exceptions. A frame the source raises
        while producing belongs to the stream and propagates to the caller exactly
        as it would without the tee; the tee only swallows telemetry's own
        failures. The closing marker is still enqueued in the ``finally``.

    This function observes the events the graph already produces without changing
    any control flow (Requirement 2.5, 10.3).
    """
    # Reach for the shared background writer (guarded — a failure degrades to no
    # telemetry rather than raising into the endpoint). Requirement 6.2.
    try:
        writer = get_session_writer()
    except Exception:  # pragma: no cover - defensive; get_session_writer is guarded
        writer = None

    # Open the entry: a start marker so the writer opens a fresh buffer (and, for a
    # ``/run``, a fresh Session) for this thread_id. Guarded — never blocks/raises.
    try:
        _safe_enqueue(writer, Observation.start(thread_id, entry, ts=time.time()))
    except Exception:  # pragma: no cover - defensive
        pass

    try:
        async for frame in source:
            # 1. ALWAYS re-yield the source frame FIRST, unchanged and in order, so
            #    the live stream is byte-for-byte identical (R6.1, Property 20).
            yield frame

            # 2. THEN enqueue a lightweight observation OFF the hot path. Wrapped so
            #    a telemetry bug can never drop/delay/raise into the stream
            #    (R6.2, R6.3, Property 21).
            try:
                event_name, payload = _parse_sse_frame(frame)
                _safe_enqueue(
                    writer,
                    Observation.frame(
                        thread_id,
                        entry,
                        event_name,
                        payload,
                        ts=time.time(),
                    ),
                )
            except Exception:  # pragma: no cover - defensive; steps already guarded
                # Swallow telemetry's own failure; the frame was already yielded.
                pass
    finally:
        # Close the entry: enqueue the end marker so the writer folds + persists the
        # Session. In a ``finally`` so it fires even if the source raised (its
        # exception still propagates — we do not swallow the source's own error).
        try:
            _safe_enqueue(writer, Observation.end(thread_id, entry, ts=time.time()))
        except Exception:  # pragma: no cover - defensive
            pass


# ── CLI ───────────────────────────────────────────────────────────────────────
# A thin, READ-ONLY command-line front door over the store + pure aggregation
# core, mirroring ``attribution.py`` / ``backtest.py``'s ``main()`` conventions:
# an ``argparse`` parser, a ``print(json.dumps(report, indent=2))`` dump of the
# structured Telemetry_Report, and the ``if __name__ == "__main__"`` guard. The
# CLI emits NO trade decision and NEVER writes to any store (Requirement 5.1,
# 6.4); it always exits ``0`` — even on an empty store — because the report itself
# carries the weak-prior signal the reader judges strength by (Requirement 5.2).


def _parse_time_bound(raw: Optional[str]) -> Optional[float]:
    """Parse a ``--since`` / ``--until`` bound into epoch-seconds, else ``None``.

    Accepts either epoch seconds (``"1704067200"`` / ``"1704067200.0"``) or an ISO
    date/datetime (``"2024-01-01"`` or ``"2024-01-01T09:15:00"``), returning a
    ``float`` epoch-seconds value suitable for :func:`load_sessions` /
    :func:`filter_sessions`. DEFENSIVE and TOTAL: a ``None`` / empty / whitespace /
    unparseable bound resolves to ``None`` ("no bound") rather than raising, so a
    malformed ``--since`` / ``--until`` degrades to an unconstrained range instead
    of taking the CLI down (Requirement 5.3).

    An ISO value with no timezone is interpreted as UTC so the epoch conversion is
    deterministic regardless of the host's local zone.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    # 1. Epoch seconds (int or float) — the store's native started_at units.
    try:
        value = float(text)
        if math.isfinite(value):
            return value
    except (TypeError, ValueError):
        pass

    # 2. ISO date / datetime — convert via datetime, assuming UTC when naive.
    iso = text
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):  # pragma: no cover - defensive
        return None


def main(argv: Optional[List[str]] = None) -> int:
    """Print the Telemetry_Report as JSON over the recorded Sessions (R5.1-5.3).

    Mirrors ``attribution.py`` / ``backtest.py``: an ``argparse`` parser
    (``--symbol`` / ``--timeframe`` / ``--mode`` / ``--since`` / ``--until`` /
    ``--json``), a read-only load of the store, and a
    ``print(json.dumps(report, indent=2))`` dump of the structured report.

    ``--since`` / ``--until`` are time-range filters on a Session's ``started_at``
    (inclusive ``[since, until]``). Each may be given as epoch seconds or an ISO
    date; they are parsed to epoch-seconds floats via :func:`_parse_time_bound`,
    and an unparseable bound degrades to "no bound" rather than failing.

    The report is computed by :func:`aggregate` with ``now_ref=time.time()`` so
    still-open Sessions aged past the ``incomplete`` horizon are classified
    ``incomplete``. The active CLI filters are stamped into the report's
    ``filters`` block (the pure core emits an all-``None`` skeleton; the CLI
    pre-filters and records what it filtered by).

    BEST-EFFORT: the whole body is guarded so ANY failure still prints a report
    and returns ``0`` — even over an empty store, which yields a valid weak-prior
    report the reader can judge (Requirement 5.2). ALWAYS returns ``0``.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Session Telemetry — read-only Telemetry_Report over recorded "
            "analysis Sessions (conversion, invalidation, watch cycles, "
            "time-to-decision, cost), filterable by symbol / timeframe / mode / "
            "time range."
        )
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Restrict to a single symbol, e.g. RELIANCE. Default: every symbol.",
    )
    parser.add_argument(
        "--timeframe",
        default=None,
        help="Restrict to a single timeframe, e.g. 15m. Default: every timeframe.",
    )
    parser.add_argument(
        "--mode",
        default=None,
        help="Restrict to a single mode, e.g. FIND. Default: every mode.",
    )
    parser.add_argument(
        "--since",
        default=None,
        help=(
            "Lower bound (inclusive) on a Session's start time. ISO date "
            "(2024-01-01) or epoch seconds. Default: no lower bound."
        ),
    )
    parser.add_argument(
        "--until",
        default=None,
        help=(
            "Upper bound (inclusive) on a Session's start time. ISO date "
            "(2024-02-01) or epoch seconds. Default: no upper bound."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit only the JSON report (suppress any human-readable notes).",
    )

    try:
        args = parser.parse_args(argv)

        since = _parse_time_bound(args.since)
        until = _parse_time_bound(args.until)

        cfg = resolve_telemetry_config()

        # Read the store READ-ONLY, applying the equality + time-range filters at
        # the load layer (parameterized, no interpolation).
        records = load_sessions(
            cfg,
            symbol=args.symbol,
            timeframe=args.timeframe,
            mode=args.mode,
            start=since,
            end=until,
        )

        # Aggregate with an explicit now_ref so still-open sessions past the
        # horizon are classified `incomplete` for this report.
        report = aggregate(records, cfg, now_ref=time.time())

        # Stamp the ACTIVE CLI filters into the report's filters block (the pure
        # core emits an all-None skeleton; the CLI pre-filtered and records it).
        if isinstance(report, dict):
            report["filters"] = {
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "mode": args.mode,
                "since": since,
                "until": until,
            }

        if not args.json and isinstance(report, dict) and report.get("weak_prior"):
            print(
                "[Telemetry] Weak prior: session count below "
                "weak_prior_min_sessions — interpret the report with caution."
            )

        print(json.dumps(report, indent=2))
    except SystemExit:
        # argparse calls sys.exit on --help / bad args; let that pass through.
        raise
    except Exception as e:  # pragma: no cover - defensive; report never crashes
        print(f"[Telemetry] WARN: could not produce report: {e}")

    # Always exit 0 — even on an empty store; the report carries its own signal.
    return 0


if __name__ == "__main__":
    sys.exit(main())
