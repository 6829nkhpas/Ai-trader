"""Event_Classifier — pure date-math earnings/event-date risk awareness.

The Deep Quant agent reads price structure, regime, relative strength, order
flow, a volatility-aware forecast, options positioning, session/expiry timing,
and news sentiment — but it is blind to the *calendar of scheduled binary
events*. It will happily commit a two-day swing long into a stock that reports
quarterly results tomorrow morning. A scheduled earnings/results date is a
*binary event*: the stock can gap 8-12% overnight on the print, and no amount of
clean price structure protects a position held through it. A veteran trader
knows the earnings calendar cold — they flatten or size down before a scheduled
event, or take the trade only if it closes intraday *before* the event. This
module implements that discipline as a cheap, deterministic classifier.

From a reference "now" timestamp, the nearest upcoming Scheduled_Event datetime,
and an intended Holding_Horizon, it computes the days-until-event, classifies
the Event_Risk (``clear`` / ``imminent`` / ``through_event``), and derives a
tightening-only Event_Recommendation (``proceed`` / ``size_down`` /
``shorten_horizon`` / ``stand_aside``).

Scope discipline (Requirement 12): everything here is a *risk filter / context
aid*, never a trade generator. The classifier maps a (reference, event, horizon,
config) tuple to a structured Event_Assessment (or an honest Unavailable_Marker);
it never emits BUY/SELL/HOLD, only ever tightens (never loosens), never blocks a
trade, and never fabricates an event date.

Purity (Requirements 2.1, 2.9, 3): this module is pure Python. It performs zero
network calls, reads no external data source, and never touches the host wall
clock. All I/O — reading the process clock for the reference "now" and reading
the configured Event_Source — lives in the ``get_event_risk`` tool, never here.
Parameter *resolution* (``resolve_event_config``) is the only place the process
environment is read, and it does so once, deterministically, with documented
defaults (Requirement 11).

This file (task 1.1) provides the configuration-resolution foundation: the
documented default constants, the ``HOLDING_HORIZONS`` set, the frozen
``EventConfig`` dataclass, and ``resolve_event_config()``. The pure selection /
date-math / classification helpers and the top-level ``assess_event_risk`` entry
point are added in subsequent tasks.
"""

from __future__ import annotations

import math
import os
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ── Documented default parameters ─────────────────────────────────────────────
# Applied whenever a parameter env var is unset / empty / unparseable / out of
# range (Requirement 11.2-11.4). These are the single source of truth for the
# defaults on BOTH the live tool path and the backtest path (Requirement 11.6).

DEFAULT_EVENT_GATE_ENABLED = True                        # master enable flag; disabling restores pre-feature behavior
DEFAULT_EVENT_MARKET_TIMEZONE = "Asia/Kolkata"           # IST; the NSE session tz
DEFAULT_EVENT_DEFAULT_HOLDING_HORIZON = "multi_session"  # conservative default (swing/positional)
DEFAULT_EVENT_IMMINENT_WINDOW_DAYS = 5                   # <= this many days out -> at least imminent
DEFAULT_EVENT_THROUGH_EVENT_WINDOW_DAYS = 2              # multi_session within this many days -> through_event
DEFAULT_EVENT_SOURCE_TIMEOUT_S = 10.0                    # calendar-API retrieval timeout (seconds)

# The recognized Holding_Horizon values. Anything else (missing / unrecognized)
# normalizes to the documented default Holding_Horizon (Requirements 3.2, 4.4).
HOLDING_HORIZONS = {"intraday", "multi_session"}

# ── Environment variable names ────────────────────────────────────────────────
ENV_EVENT_GATE_ENABLED = "EVENT_GATE_ENABLED"
ENV_EVENT_MARKET_TIMEZONE = "EVENT_MARKET_TIMEZONE"
ENV_EVENT_DEFAULT_HOLDING_HORIZON = "EVENT_DEFAULT_HOLDING_HORIZON"
ENV_EVENT_IMMINENT_WINDOW_DAYS = "EVENT_IMMINENT_WINDOW_DAYS"
ENV_EVENT_THROUGH_EVENT_WINDOW_DAYS = "EVENT_THROUGH_EVENT_WINDOW_DAYS"
ENV_EVENT_SOURCE_TIMEOUT_S = "EVENT_SOURCE_TIMEOUT_S"
ENV_EVENT_CALENDAR_API_URL = "EVENT_CALENDAR_API_URL"
ENV_EVENT_CALENDAR_FILE = "EVENT_CALENDAR_FILE"

# ── Valid ranges ──────────────────────────────────────────────────────────────
# Window lengths are integers >= 0 (no upper bound); the source retrieval timeout
# is a float strictly > 0 (Requirement 11.1).
_WINDOW_MIN = 0
_TIMEOUT_MIN_EXCLUSIVE = 0.0

# Recognized boolean spellings for the master enable flag (case-insensitive).
# A value outside these spellings falls back to the documented default.
_BOOL_TRUE = {"1", "true", "yes"}
_BOOL_FALSE = {"0", "false", "no"}


@dataclass(frozen=True)
class EventConfig:
    """The resolved, validated parameter set used to classify event risk.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the classifier's purity guarantee). For identical
    environment-variable values the resolved configuration is identical on both
    the tool path and the backtest path (Requirement 11.6).

    ``calendar_api_url`` / ``calendar_file_path`` are ``None`` when their env
    vars are unset/empty; the source layer detects "no source configured" by
    both being ``None`` (Requirement 1.2).
    """

    enabled: bool
    timezone: str
    default_holding_horizon: str
    imminent_window_days: int
    through_event_window_days: int
    source_timeout_s: float
    calendar_api_url: Optional[str]
    calendar_file_path: Optional[str]


def _resolve_bool(env_name: str, default: bool) -> bool:
    """Resolve one boolean parameter from its own env var (R11.2-11.4).

    Recognizes ``1/0/true/false/yes/no`` case-insensitively. Falls back to
    ``default`` when the var is unset/empty or holds an unrecognized spelling.
    Never raises.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    text = raw.strip().lower()
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    return default


def _resolve_timezone(env_name: str, default: str) -> str:
    """Resolve the market timezone from its own env var (R11.2-11.4).

    Falls back to ``default`` when the var is unset/empty or names a timezone
    that ``zoneinfo`` cannot load. Returns an IANA timezone string that is known
    to be loadable (the default itself is assumed loadable on a standard CPython
    install with tzdata available). Never raises.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    candidate = raw.strip()
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # Unloadable / malformed timezone -> documented default (R11.3, R11.4).
        return default
    return candidate


def _resolve_horizon(env_name: str, default: str) -> str:
    """Resolve the default Holding_Horizon from its own env var (R11.2-11.4).

    Falls back to ``default`` when the var is unset/empty or holds a value that
    is not one of ``HOLDING_HORIZONS``. Never raises.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    candidate = raw.strip()
    if candidate not in HOLDING_HORIZONS:
        return default
    return candidate


def _resolve_int(env_name: str, default: int, low: int, high: int | None = None) -> int:
    """Resolve one integer parameter from its own env var (R11.2-11.4).

    Falls back to ``default`` when the var is unset/empty, cannot be parsed as an
    int, or parses but falls outside ``[low, high]`` (``high`` ``None`` means no
    upper bound). Never raises.
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
    if high is not None and value > high:
        return default
    return value


def _resolve_float_exclusive(env_name: str, default: float, low_exclusive: float) -> float:
    """Resolve one float parameter that must be strictly greater than a lower
    bound, from its own env var (R11.2-11.4).

    Falls back to ``default`` when the var is unset/empty, cannot be parsed as a
    float, is non-finite (NaN/inf), or parses but is not strictly greater than
    ``low_exclusive``. Never raises.
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
    if value <= low_exclusive:
        return default
    return value


def _resolve_optional_str(env_name: str) -> Optional[str]:
    """Resolve one optional string parameter from its own env var.

    Returns the stripped value when the var holds a non-empty string, else
    ``None`` (unset / empty). This ``None`` is how the source layer detects "no
    source configured" (Requirement 1.2). Never raises.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def resolve_event_config() -> EventConfig:
    """Resolve every parameter from its own env var with documented defaults.

    Per-parameter rules (Requirement 11):
      * unset / empty            -> documented default (R11.2)
      * unparseable as its type  -> documented default (never raises) (R11.3)
      * parses but out of range  -> documented default (never raises) (R11.4)
      * through_event_window_days > imminent_window_days -> BOTH windows revert
        to their documented defaults together (ordering invariant)
      * unloadable timezone      -> documented default timezone (R11.3, R11.4)
      * unrecognized default horizon -> documented default horizon (R11.3)

    ``EVENT_CALENDAR_API_URL`` / ``EVENT_CALENDAR_FILE`` resolve to ``None`` when
    unset/empty, which is how the source layer detects "no source configured"
    (Requirement 1.2).

    The same function is called on every tool invocation so the resolved values
    are identical for identical environment (Requirement 11.6). This function
    NEVER raises.
    """
    enabled = _resolve_bool(ENV_EVENT_GATE_ENABLED, DEFAULT_EVENT_GATE_ENABLED)

    timezone = _resolve_timezone(
        ENV_EVENT_MARKET_TIMEZONE, DEFAULT_EVENT_MARKET_TIMEZONE
    )

    default_holding_horizon = _resolve_horizon(
        ENV_EVENT_DEFAULT_HOLDING_HORIZON, DEFAULT_EVENT_DEFAULT_HOLDING_HORIZON
    )

    imminent_window_days = _resolve_int(
        ENV_EVENT_IMMINENT_WINDOW_DAYS, DEFAULT_EVENT_IMMINENT_WINDOW_DAYS, _WINDOW_MIN
    )
    through_event_window_days = _resolve_int(
        ENV_EVENT_THROUGH_EVENT_WINDOW_DAYS,
        DEFAULT_EVENT_THROUGH_EVENT_WINDOW_DAYS,
        _WINDOW_MIN,
    )

    # Enforce the through_event_window_days <= imminent_window_days ordering. If
    # it does not hold (after the per-parameter resolution above), BOTH windows
    # revert to their documented defaults together (ordering invariant, AD-8).
    if through_event_window_days > imminent_window_days:
        imminent_window_days = DEFAULT_EVENT_IMMINENT_WINDOW_DAYS
        through_event_window_days = DEFAULT_EVENT_THROUGH_EVENT_WINDOW_DAYS

    source_timeout_s = _resolve_float_exclusive(
        ENV_EVENT_SOURCE_TIMEOUT_S, DEFAULT_EVENT_SOURCE_TIMEOUT_S, _TIMEOUT_MIN_EXCLUSIVE
    )

    calendar_api_url = _resolve_optional_str(ENV_EVENT_CALENDAR_API_URL)
    calendar_file_path = _resolve_optional_str(ENV_EVENT_CALENDAR_FILE)

    return EventConfig(
        enabled=enabled,
        timezone=timezone,
        default_holding_horizon=default_holding_horizon,
        imminent_window_days=imminent_window_days,
        through_event_window_days=through_event_window_days,
        source_timeout_s=source_timeout_s,
        calendar_api_url=calendar_api_url,
        calendar_file_path=calendar_file_path,
    )


# ── Event_Risk / Event_Recommendation vocabularies ───────────────────────────
# The categorical outputs of the classifier. Named constants (mirroring the
# PHASE_* convention in session.py) keep the mapping tables below readable and
# give tools.py a single source of truth to validate against.

EVENT_RISK_CLEAR = "clear"                # no straddle of a scheduled event
EVENT_RISK_IMMINENT = "imminent"          # event near but not held through
EVENT_RISK_THROUGH_EVENT = "through_event"  # position would be live across the event

EVENT_REC_PROCEED = "proceed"             # no tightening required
EVENT_REC_SIZE_DOWN = "size_down"         # reduce size / conviction
EVENT_REC_SHORTEN_HORIZON = "shorten_horizon"  # close intraday before the event
EVENT_REC_STAND_ASIDE = "stand_aside"     # cannot tighten further -> prefer HOLD


# ── Pure selection / date-math / classification helpers ──────────────────────
# Everything below is pure Python: it derives its result solely from the
# provided arguments and the resolved configuration, performs zero network
# calls, reads no external data source, never touches the host wall clock, and
# never raises (Requirements 2.1, 2.9, 3.1, 3.5). All I/O — reading the process
# clock for the reference "now" and reading the configured Event_Source — lives
# in the get_event_risk tool, never here (AD-1).


def _is_finite_number(v) -> bool:
    """True for a finite real number; ``bool`` is excluded (matches the repo's
    ``_is_num`` convention in ``session.py`` / ``regime.py`` / ``rs.py``)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def normalize_holding_horizon(value, config: EventConfig) -> str:
    """Return ``value`` when it is one of ``HOLDING_HORIZONS``, else the
    configured default Holding_Horizon (Requirements 3.2, 4.4).

    Absent / empty / unrecognized Holding_Horizons (including non-string values)
    collapse to ``config.default_holding_horizon``. Total; never raises.
    """
    if isinstance(value, str) and value in HOLDING_HORIZONS:
        return value
    return config.default_holding_horizon


def select_next_event(candidate_ms, reference_ms, config: EventConfig) -> Optional[float]:
    """Select the nearest *future* Scheduled_Event from a candidate list (AD-4).

    From an iterable of candidate event timestamps (epoch ms), discard every
    candidate at or before ``reference_ms`` (past / not upcoming, Requirement
    1.6) and return the earliest of the remaining future candidates, or ``None``
    when none remain (Requirement 1.5). Non-finite / non-numeric candidates (and
    a non-finite / non-numeric ``reference_ms``, or a non-iterable
    ``candidate_ms``) are ignored rather than raising. Pure; never raises.

    ``config`` is accepted for signature symmetry with the other helpers and
    future extension; selection itself needs only the reference timestamp.
    """
    if not _is_finite_number(reference_ms):
        return None
    try:
        iterator = iter(candidate_ms)
    except TypeError:
        # Not an iterable -> no candidates to select from.
        return None

    best: Optional[float] = None
    for candidate in iterator:
        if not _is_finite_number(candidate):
            continue
        if candidate <= reference_ms:
            # At or before the reference -> past / not upcoming (R1.6).
            continue
        if best is None or candidate < best:
            best = float(candidate)
    return best


def compute_days_until_event(reference_ms, event_ms, config: EventConfig) -> Optional[int]:
    """Whole calendar days from the reference date to the event date (R2.2, R3.5).

    Both timestamps are interpreted in the configured market timezone
    (``config.timezone`` via ``zoneinfo``) and reduced to their local calendar
    dates; the result is the whole-day difference between those dates. Because
    classification depends only on the calendar-date gap (not the intraday
    time), a same-day event yields ``0`` and a next-day event yields ``1``.

    Returns ``None`` when either timestamp is missing (``None``), non-numeric,
    non-finite (``NaN`` / ``±inf``), or out of the representable datetime range
    (Requirement 3.1), and also when the event's local date falls strictly
    before the reference's local date (a past event is not a valid
    days-until-event). This keeps the result constrained to null-or-finite-
    non-negative (Requirement 3.3). Pure; never reads the host clock; never
    raises.
    """
    if not _is_finite_number(reference_ms) or not _is_finite_number(event_ms):
        return None
    try:
        tz = ZoneInfo(config.timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # A resolved config should always carry a loadable timezone, but guard
        # defensively so this helper never raises (Requirement 3.1).
        return None
    try:
        reference_date = datetime.fromtimestamp(reference_ms / 1000.0, tz=tz).date()
        event_date = datetime.fromtimestamp(event_ms / 1000.0, tz=tz).date()
    except (OverflowError, OSError, ValueError):
        # Out-of-range epoch values cannot become a datetime -> treat as invalid.
        return None
    days = (event_date - reference_date).days
    if days < 0:
        # Past-dated event -> not a valid upcoming days-until (R3.3 non-negative).
        return None
    return days


def classify_event_risk(days_until_event, holding_horizon: str, config: EventConfig) -> str:
    """Classify the Event_Risk as exactly one of ``clear`` / ``imminent`` /
    ``through_event`` from the day count and the Holding_Horizon (R2.3-R2.6).

    Mapping (total over every ``days >= 0`` and every recognized horizon):

      * ``intraday``      : ``d == 0`` -> ``through_event`` (event lands while the
                            same-session position is live); ``d >= 1`` -> ``clear``
                            (a future-dated event is not straddled, R2.4).
      * ``multi_session`` : ``d <= through_event_window_days`` -> ``through_event``
                            (R2.5); ``through_event_window_days < d <=
                            imminent_window_days`` -> ``imminent`` (R2.6); ``d >
                            imminent_window_days`` -> ``clear``.

    An unrecognized ``holding_horizon`` is treated as the conservative
    ``multi_session`` branch, and a non-numeric / negative ``days_until_event``
    falls back to ``clear`` (no straddle asserted), so the function is total and
    never raises.
    """
    if not _is_finite_number(days_until_event) or days_until_event < 0:
        return EVENT_RISK_CLEAR
    days = days_until_event

    if holding_horizon == "intraday":
        return EVENT_RISK_THROUGH_EVENT if days == 0 else EVENT_RISK_CLEAR

    # multi_session (and any non-intraday horizon, treated conservatively).
    if days <= config.through_event_window_days:
        return EVENT_RISK_THROUGH_EVENT
    if days <= config.imminent_window_days:
        return EVENT_RISK_IMMINENT
    return EVENT_RISK_CLEAR


def derive_event_recommendation(event_risk: str, holding_horizon: str) -> str:
    """Derive the tightening-only Event_Recommendation from the Event_Risk and
    Holding_Horizon (Requirements 2.7, 12.2).

    Mapping (total; range limited to the four tightening-only values):

      * ``clear``        -> ``proceed``        (any horizon)
      * ``imminent``     -> ``size_down``      (any horizon)
      * ``through_event`` + ``intraday``      -> ``stand_aside`` (already
                            same-session and still through today's event ->
                            cannot shorten further)
      * ``through_event`` + any other horizon -> ``shorten_horizon`` (close
                            intraday before the event -> reclassifies to clear)

    Any unrecognized Event_Risk collapses to ``proceed`` (no tightening
    asserted), keeping the recommendation tightening-only and the function total.
    Never raises.
    """
    if event_risk == EVENT_RISK_IMMINENT:
        return EVENT_REC_SIZE_DOWN
    if event_risk == EVENT_RISK_THROUGH_EVENT:
        if holding_horizon == "intraday":
            return EVENT_REC_STAND_ASIDE
        return EVENT_REC_SHORTEN_HORIZON
    # clear (and any unrecognized risk) -> no tightening required.
    return EVENT_REC_PROCEED


# ── Unavailable_Marker / Event_Assessment helpers ────────────────────────────


def _event_unavailable(
    reason: str,
    symbol: Optional[str],
    holding_horizon: Optional[str],
    event_date: Optional[str],
) -> dict:
    """Build an honest Unavailable_Marker (Requirements 3.1, 5.1, 12.1).

    ``event_risk`` / ``event_recommendation`` are *omitted* (never defaulted or
    fabricated, AD-5): a marker asserts an *absence* of a usable assessment, so
    it carries only the invalid-input reason plus whatever context the caller
    could supply. ``symbol`` / ``holding_horizon`` / ``event_date`` are included
    only when provided (the classifier itself has no knowledge of them). Mirrors
    the marker style of ``session._unavailable`` / ``regime._unavailable``.
    """
    marker: dict = {}
    if symbol is not None:
        marker["symbol"] = symbol
    if holding_horizon is not None:
        marker["holding_horizon"] = holding_horizon
    if event_date is not None:
        marker["event_date"] = event_date
    marker["unavailable"] = True
    marker["reason"] = reason
    return marker


def assess_event_risk(
    reference_ms,
    event_ms,
    holding_horizon,
    config: EventConfig,
    symbol: Optional[str] = None,
    event_date: Optional[str] = None,
) -> dict:
    """Top-level entry point: map a (reference, event, horizon, config) tuple to
    an Event_Assessment or an Unavailable_Marker.

    Returns either an Event_Assessment dict (``days_until_event`` / ``event_risk``
    / ``event_recommendation`` / ``holding_horizon`` / ``event_date`` /
    ``symbol``) or an Unavailable_Marker dict (``unavailable`` / ``reason`` plus
    available context, with ``event_risk`` / ``event_recommendation`` omitted).

    Behaviour (Requirements 2.1, 2.8, 2.9, 3.1, 3.4, 5.1, 12.1):
      1. ``horizon = normalize_holding_horizon(holding_horizon, config)`` — an
         absent / unrecognized horizon collapses to the configured default
         (R3.2, R4.4) so the assessment always reports a recognized horizon.
      2. ``days = compute_days_until_event(reference_ms, event_ms, config)``; if
         ``None`` the (reference, event) pair is invalid (missing / non-numeric /
         non-finite / out-of-range / past-dated), so the result is an
         Unavailable_Marker citing the invalid-input condition (R3.1, R5.1) —
         never a fabricated assessment.
      3. ``event_risk = classify_event_risk(days, horizon, config)``.
      4. ``event_recommendation = derive_event_recommendation(event_risk, horizon)``.
      5. Return the Event_Assessment carrying all six fields.

    Pure and deterministic (R2.8, R3.4): identical inputs always yield an
    identical result. Never mutates its inputs (R2.9). Never reads the host clock
    and never raises (R2.1, R3.1). Emits ONLY an assessment or a marker — never a
    BUY/SELL/HOLD action, conviction, or any other trade-decision field (R12.1).
    """
    try:
        horizon = normalize_holding_horizon(holding_horizon, config)

        days_until_event = compute_days_until_event(reference_ms, event_ms, config)
        if days_until_event is None:
            return _event_unavailable(
                "invalid event timing: expected finite epoch-millisecond "
                f"reference/event timestamps, got reference={reference_ms!r}, "
                f"event={event_ms!r}",
                symbol,
                horizon,
                event_date,
            )

        event_risk = classify_event_risk(days_until_event, horizon, config)
        event_recommendation = derive_event_recommendation(event_risk, horizon)

        return {
            "days_until_event": days_until_event,
            "event_risk": event_risk,
            "event_recommendation": event_recommendation,
            "holding_horizon": horizon,
            "event_date": event_date,
            "symbol": symbol,
        }
    except Exception as exc:  # pragma: no cover - defensive; classifier is pure
        # The classifier must never raise into its callers (R3.1). Any unexpected
        # failure degrades to an honest Unavailable_Marker rather than an
        # exception or a fabricated assessment.
        return _event_unavailable(
            f"event classification error: {exc.__class__.__name__}",
            symbol,
            normalize_holding_horizon(holding_horizon, config),
            event_date,
        )
