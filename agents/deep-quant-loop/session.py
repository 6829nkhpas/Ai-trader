"""Session_Classifier — pure date-math session & expiry awareness for Deep Quant.

An NSE session is not uniform: the opening drive (first minutes after 09:15 IST)
is violent and mean-reverting, the midday lull is thin and chop-prone, the
closing hour carries squaring-off flow, and weekly expiry (Thursday afternoon)
distorts price action across the market. A veteran trader sizes down or stands
aside in those windows. This module implements that discipline as a cheap,
deterministic, pure-date-math classifier.

From a single candle timestamp interpreted in the configured market timezone
(default Asia/Kolkata / IST) it labels the intraday Session_Phase, minutes since
open / until close, the weekly Expiry_Context, and a derived Time_Favorability.

Scope discipline (Requirement 13): everything here is a *filter / context aid*,
never a trade generator. The classifier maps a candle timestamp plus a resolved
configuration to a structured Session_Label (or an honest Unavailable_Marker);
it never emits BUY/SELL/HOLD, never blocks a trade, and never fabricates data.

Purity (Requirement 1): this module is pure Python. It performs zero network
calls, reads no external data source, and never touches the host wall clock.
Parameter *resolution* (``resolve_session_config``) is the only place the
process environment is read, and it does so once, deterministically, with
documented defaults.

This file (task 1.1) provides the configuration-resolution foundation: the
documented default constants, the frozen ``SessionConfig`` dataclass, and
``resolve_session_config()``. The pure date-math helpers and the classification
functions are added in subsequent tasks.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ── Documented default parameters ─────────────────────────────────────────────
# Applied whenever a parameter env var is unset / empty / unparseable / out of
# range (Requirement 12.2-12.4). These are the single source of truth for the
# defaults on BOTH the live tool path and the backtest path (Requirement 12.6).

DEFAULT_SESSION_TIMEZONE = "Asia/Kolkata"   # IST; the NSE equity cash session tz
DEFAULT_SESSION_OPEN = dtime(9, 15)         # 09:15 IST
DEFAULT_SESSION_CLOSE = dtime(15, 30)       # 15:30 IST
DEFAULT_OPENING_MINUTES = 15                # opening-drive window length (minutes)
DEFAULT_CLOSING_MINUTES = 30                # closing window length (minutes)
DEFAULT_MIDDAY_START = dtime(11, 30)        # midday lull start
DEFAULT_MIDDAY_END = dtime(13, 30)          # midday lull end
DEFAULT_EXPIRY_WEEKDAY = 3                   # Thursday (Mon=0 .. Sun=6), NSE weekly

# ── Environment variable names ────────────────────────────────────────────────
ENV_SESSION_TIMEZONE = "SESSION_TIMEZONE"
ENV_SESSION_OPEN = "SESSION_OPEN"
ENV_SESSION_CLOSE = "SESSION_CLOSE"
ENV_OPENING_MINUTES = "SESSION_OPENING_MINUTES"
ENV_CLOSING_MINUTES = "SESSION_CLOSING_MINUTES"
ENV_MIDDAY_START = "SESSION_MIDDAY_START"
ENV_MIDDAY_END = "SESSION_MIDDAY_END"
ENV_EXPIRY_WEEKDAY = "SESSION_EXPIRY_WEEKDAY"

# ── Valid ranges ──────────────────────────────────────────────────────────────
# Window lengths are integers >= 0 (no upper bound); the expiry weekday is an
# integer in [0, 6] (Mon-Sun); times are any valid 24h time-of-day
# (HH:MM, 00:00-23:59) (Requirement 12.1).
_MINUTES_MIN = 0
_WEEKDAY_MIN = 0
_WEEKDAY_MAX = 6


@dataclass(frozen=True)
class SessionConfig:
    """The resolved, validated parameter set used to classify a session.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the classifier's purity guarantee). For identical
    environment-variable values the resolved configuration is identical on both
    the tool path and the backtest path (Requirement 12.6).
    """

    timezone: str
    open_time: dtime
    close_time: dtime
    opening_minutes: int
    closing_minutes: int
    midday_start: dtime
    midday_end: dtime
    expiry_weekday: int


def _resolve_timezone(env_name: str, default: str) -> str:
    """Resolve the market timezone from its own env var (R12.2-12.4).

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
        # Unloadable / malformed timezone -> documented default (R12.3, R12.4).
        return default
    return candidate


def _resolve_time(env_name: str, default: dtime) -> dtime:
    """Resolve one ``HH:MM`` time-of-day parameter from its env var (R12.2-12.4).

    Falls back to ``default`` when the var is unset/empty, is not formatted as
    ``HH:MM``, or does not parse as a valid 24h time-of-day. Seconds/fractional
    components are not accepted (the documented type is ``HH:MM``). Never raises.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    text = raw.strip()
    parts = text.split(":")
    if len(parts) != 2:
        return default
    hh, mm = parts[0].strip(), parts[1].strip()
    if not (hh.isdigit() and mm.isdigit()):
        return default
    try:
        hour = int(hh)
        minute = int(mm)
    except (ValueError, TypeError):
        return default
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return default
    return dtime(hour, minute)


def _resolve_int(env_name: str, default: int, low: int, high: int | None = None) -> int:
    """Resolve one integer parameter from its own env var (R12.2-12.4).

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


def resolve_session_config() -> SessionConfig:
    """Resolve every parameter from its own env var with documented defaults.

    Per-parameter rules (Requirement 12):
      * unset / empty            -> documented default (R12.2)
      * unparseable as its type  -> documented default (never raises) (R12.3)
      * parses but out of range  -> documented default (never raises) (R12.4)
      * open_time not strictly before close_time -> BOTH revert to the
        documented default open/close together (R12.5)
      * unloadable timezone      -> documented default timezone (R12.3, R12.4)

    The same function is called on the tool path and the backtest path so the
    resolved values are identical for identical environment (Requirement 12.6).
    This function NEVER raises.
    """
    timezone = _resolve_timezone(ENV_SESSION_TIMEZONE, DEFAULT_SESSION_TIMEZONE)

    open_time = _resolve_time(ENV_SESSION_OPEN, DEFAULT_SESSION_OPEN)
    close_time = _resolve_time(ENV_SESSION_CLOSE, DEFAULT_SESSION_CLOSE)

    # Enforce the strict open < close ordering. If it does not hold (after the
    # per-parameter resolution above), BOTH the open and close times revert to
    # their documented defaults together (Requirement 12.5).
    if not (open_time < close_time):
        open_time = DEFAULT_SESSION_OPEN
        close_time = DEFAULT_SESSION_CLOSE

    opening_minutes = _resolve_int(
        ENV_OPENING_MINUTES, DEFAULT_OPENING_MINUTES, _MINUTES_MIN
    )
    closing_minutes = _resolve_int(
        ENV_CLOSING_MINUTES, DEFAULT_CLOSING_MINUTES, _MINUTES_MIN
    )

    midday_start = _resolve_time(ENV_MIDDAY_START, DEFAULT_MIDDAY_START)
    midday_end = _resolve_time(ENV_MIDDAY_END, DEFAULT_MIDDAY_END)

    expiry_weekday = _resolve_int(
        ENV_EXPIRY_WEEKDAY, DEFAULT_EXPIRY_WEEKDAY, _WEEKDAY_MIN, _WEEKDAY_MAX
    )

    return SessionConfig(
        timezone=timezone,
        open_time=open_time,
        close_time=close_time,
        opening_minutes=opening_minutes,
        closing_minutes=closing_minutes,
        midday_start=midday_start,
        midday_end=midday_end,
        expiry_weekday=expiry_weekday,
    )


# ── Session_Phase / Time_Favorability enumerations ────────────────────────────
# Kept here as the single source of truth for the seven phases and the three
# favorability values the classifier can produce (Requirements 1.3, 1.5).
PHASE_PRE_OPEN = "pre_open"
PHASE_OPENING = "opening"
PHASE_MORNING = "morning"
PHASE_MIDDAY = "midday"
PHASE_AFTERNOON = "afternoon"
PHASE_CLOSING = "closing"
PHASE_POST_CLOSE = "post_close"

FAVORABLE = "favorable"
UNFAVORABLE = "unfavorable"
NEUTRAL = "neutral"

# Base favorability per Session_Phase (Requirement 1.5). The expiry override
# (Requirement 2.3) is applied on top of this base mapping in
# ``derive_time_favorability``. The opening drive reads ``unfavorable``; the
# productive morning and (non-expiry) afternoon trend windows read ``favorable``;
# everything else is ``neutral``.
_BASE_FAVORABILITY = {
    PHASE_PRE_OPEN: NEUTRAL,
    PHASE_OPENING: UNFAVORABLE,
    PHASE_MORNING: FAVORABLE,
    PHASE_MIDDAY: NEUTRAL,
    PHASE_AFTERNOON: FAVORABLE,
    PHASE_CLOSING: NEUTRAL,
    PHASE_POST_CLOSE: NEUTRAL,
}

# The expiry override down-weights these phases to ``unfavorable`` on an
# expiry-day candle (the expiry-afternoon chop window) (Requirement 2.3).
_EXPIRY_OVERRIDE_PHASES = {PHASE_AFTERNOON, PHASE_CLOSING}

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_DAY = 24 * 60 * 60


# ── Pure date-math helpers ────────────────────────────────────────────────────
# Every function below is pure: it derives its result exclusively from the
# provided timestamp / datetime and the resolved configuration, performs zero
# network calls, reads no external data source, never touches the host wall
# clock, and never raises (Requirements 1.1, 1.7, 3.5, 13.2).


def _is_finite_number(v) -> bool:
    """True for a finite real number; ``bool`` is excluded (matches the repo's
    ``_is_num`` convention in ``journal.py`` / ``regime.py`` / ``rs.py``)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _seconds_of_day(t: dtime) -> int:
    """Whole seconds elapsed since local midnight for a time-of-day. Pure."""
    return t.hour * 3600 + t.minute * 60 + t.second


def to_local_datetime(timestamp_ms, config: SessionConfig) -> Optional[datetime]:
    """Convert epoch milliseconds to a timezone-aware datetime in the configured
    market timezone (Requirement 1.2, AD-2).

    Returns ``None`` when ``timestamp_ms`` is missing (``None``), non-numeric,
    non-finite (``NaN`` / ``+-inf``), or so large/small that it cannot be
    represented as a datetime (Requirement 3.1). The timestamp is always
    interpreted in ``config.timezone`` so classification is independent of the
    host machine's local timezone (Requirement 3.5). Pure; never reads the host
    clock; never raises.
    """
    if not _is_finite_number(timestamp_ms):
        return None
    try:
        tz = ZoneInfo(config.timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # A resolved config should always carry a loadable timezone, but guard
        # defensively so this helper never raises (Requirement 3.1).
        return None
    try:
        return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=tz)
    except (OverflowError, OSError, ValueError):
        # Out-of-range epoch values cannot become a datetime -> treat as invalid.
        return None


def classify_session_phase(local_dt: datetime, config: SessionConfig) -> str:
    """Classify the Session_Phase as exactly one of the seven phases by comparing
    the local time-of-day against the configured boundaries (Requirements 1.3,
    3.2). Total over every time-of-day; never raises.

    The boundary rows are evaluated in order (top to bottom), so ``opening`` and
    ``closing`` take precedence over ``morning`` / ``afternoon`` / ``midday`` when
    the configured windows overlap (see the design's Session_Phase mapping):

        t < open                                  -> pre_open
        t > close                                 -> post_close
        open <= t < open + opening_minutes        -> opening
        t >= close - closing_minutes (and <= close) -> closing
        midday_start <= t < midday_end            -> midday
        open + opening_minutes <= t < midday_start -> morning
        otherwise (in-session remainder)          -> afternoon
    """
    t = _seconds_of_day(local_dt.time())
    open_s = _seconds_of_day(config.open_time)
    close_s = _seconds_of_day(config.close_time)
    opening_end = open_s + config.opening_minutes * _SECONDS_PER_MINUTE
    closing_start = close_s - config.closing_minutes * _SECONDS_PER_MINUTE
    midday_start_s = _seconds_of_day(config.midday_start)
    midday_end_s = _seconds_of_day(config.midday_end)

    if t < open_s:
        return PHASE_PRE_OPEN
    if t > close_s:
        return PHASE_POST_CLOSE
    if t < opening_end:
        return PHASE_OPENING
    if t >= closing_start:
        return PHASE_CLOSING
    if midday_start_s <= t < midday_end_s:
        return PHASE_MIDDAY
    if opening_end <= t < midday_start_s:
        return PHASE_MORNING
    # In-session remainder (midday_end <= t < closing_start, plus any residual
    # left by a degenerate overlapping config) -> afternoon. Keeps the function
    # total and deterministic for every time-of-day.
    return PHASE_AFTERNOON


def _session_open_close_dts(local_dt: datetime, config: SessionConfig):
    """Build tz-aware open/close datetimes on ``local_dt``'s local date. Pure."""
    open_dt = local_dt.replace(
        hour=config.open_time.hour,
        minute=config.open_time.minute,
        second=config.open_time.second,
        microsecond=0,
    )
    close_dt = local_dt.replace(
        hour=config.close_time.hour,
        minute=config.close_time.minute,
        second=config.close_time.second,
        microsecond=0,
    )
    return open_dt, close_dt


def compute_minutes_since_open(
    local_dt: datetime, config: SessionConfig
) -> Optional[float]:
    """Whole minutes from the configured open to the local time, or ``None`` when
    the local time is before open or after close (out of session) (Requirements
    1.4, 3.3). Constrained to null-or-finite-non-negative. Pure; never raises.
    """
    open_dt, close_dt = _session_open_close_dts(local_dt, config)
    if local_dt < open_dt or local_dt > close_dt:
        return None
    elapsed = (local_dt - open_dt).total_seconds()
    minutes = math.floor(elapsed / _SECONDS_PER_MINUTE)
    # Defensive clamp: the in-session guard already makes this non-negative.
    if minutes < 0:
        return None
    return float(minutes)


def compute_minutes_until_close(
    local_dt: datetime, config: SessionConfig
) -> Optional[float]:
    """Whole minutes from the local time to the configured close, or ``None`` when
    the local time is before open or after close (out of session) (Requirements
    1.4, 3.3). Constrained to null-or-finite-non-negative. Pure; never raises.
    """
    open_dt, close_dt = _session_open_close_dts(local_dt, config)
    if local_dt < open_dt or local_dt > close_dt:
        return None
    remaining = (close_dt - local_dt).total_seconds()
    minutes = math.floor(remaining / _SECONDS_PER_MINUTE)
    if minutes < 0:
        return None
    return float(minutes)


def compute_expiry_context(local_dt: datetime, config: SessionConfig) -> dict:
    """Return ``{'is_expiry_day': bool, 'days_until_expiry': int}`` (Requirements
    2.1, 2.2, 2.4).

    ``is_expiry_day`` is ``True`` iff the local date's weekday equals the
    configured expiry weekday (Mon=0 .. Sun=6). ``days_until_expiry`` is the
    count of calendar days until the next occurrence of that weekday, computed as
    ``(expiry_weekday - weekday) mod 7`` so it is ``0`` on the expiry day itself
    and lies in ``[0, 6]``. Using the configurable ``expiry_weekday`` makes a
    schedule change a configuration change, not a code change (Requirement 2.4).
    Pure; never raises.
    """
    weekday = local_dt.weekday()
    days_until_expiry = (config.expiry_weekday - weekday) % 7
    return {
        "is_expiry_day": weekday == config.expiry_weekday,
        "days_until_expiry": days_until_expiry,
    }


def derive_time_favorability(
    session_phase: str, expiry_context: dict, config: SessionConfig
) -> str:
    """Derive the Time_Favorability as exactly one of ``favorable`` /
    ``unfavorable`` / ``neutral`` from the Session_Phase and the Expiry_Context
    (Requirements 1.5, 2.3).

    The derivation is a total function over every (phase, expiry-day)
    combination: a base favorability per phase, then the expiry override that
    down-weights an expiry-day ``afternoon`` / ``closing`` candle to
    ``unfavorable`` (the expiry-afternoon chop window). An unrecognized phase
    falls back to ``neutral`` so the function is total. Pure; never raises.
    """
    base = _BASE_FAVORABILITY.get(session_phase, NEUTRAL)
    is_expiry_day = bool(expiry_context.get("is_expiry_day")) if isinstance(
        expiry_context, dict
    ) else False
    if is_expiry_day and session_phase in _EXPIRY_OVERRIDE_PHASES:
        return UNFAVORABLE
    return base


# ── Unavailable_Marker / Session_Label helpers ────────────────────────────────


def _unavailable(
    reason: str,
    symbol: Optional[str],
    timeframe: Optional[str],
) -> dict:
    """Build an honest Unavailable_Marker (Requirements 3.1, 5.2, 13.1).

    ``session_phase`` / ``time_favorability`` are *omitted* (never defaulted or
    fabricated, AD-5). ``symbol`` / ``timeframe`` are included only when provided
    by the caller (the classifier itself has no knowledge of them). Mirrors the
    marker style of ``regime._unavailable`` / ``rs`` / ``order_flow``.
    """
    marker: dict = {}
    if symbol is not None:
        marker["symbol"] = symbol
    if timeframe is not None:
        marker["timeframe"] = timeframe
    marker["unavailable"] = True
    marker["reason"] = reason
    return marker


def classify_session(
    timestamp_ms,
    config: SessionConfig,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> dict:
    """Top-level entry point: map a candle timestamp + config to a Session_Label
    or an Unavailable_Marker.

    Returns either a Session_Label dict (``session_phase`` / ``minutes_since_open``
    / ``minutes_until_close`` / ``expiry_context`` / ``time_favorability`` — plus
    ``symbol`` / ``timeframe`` when the caller supplies them) or an
    Unavailable_Marker dict.

    Behaviour (Requirements 1, 3, 13):
      1. ``local_dt = to_local_datetime(timestamp_ms, config)``; if ``None`` the
         timestamp is missing / non-numeric / non-finite / out-of-range, so the
         result is an Unavailable_Marker citing the invalid-timestamp condition
         (R3.1) — never a fabricated label (R5.2, R13.1).
      2. ``session_phase = classify_session_phase(local_dt, config)``.
      3. ``minutes_since_open`` / ``minutes_until_close`` (null outside session).
      4. ``expiry_context = compute_expiry_context(local_dt, config)``.
      5. ``time_favorability = derive_time_favorability(phase, expiry_context, config)``.
      6. Return the Session_Label carrying all five fields.

    The classifier emits an Unavailable_Marker ONLY when the timestamp itself is
    invalid (R3.1); for every valid timestamp it always produces a full label (a
    timestamp before open or after close is a legitimate ``pre_open`` /
    ``post_close`` label, not an error, R3.2).

    Pure and deterministic (R1.6, R3.4): identical inputs always yield an
    identical result. Never mutates its inputs (R1.7). Never raises (R3.1). Emits
    ONLY a label or a marker — never a BUY/SELL/HOLD action, conviction, or any
    other trade-decision field (R13.1).
    """
    try:
        local_dt = to_local_datetime(timestamp_ms, config)
        if local_dt is None:
            return _unavailable(
                "invalid timestamp: expected a finite epoch-millisecond number, "
                f"got {timestamp_ms!r}",
                symbol,
                timeframe,
            )

        session_phase = classify_session_phase(local_dt, config)
        minutes_since_open = compute_minutes_since_open(local_dt, config)
        minutes_until_close = compute_minutes_until_close(local_dt, config)
        expiry_context = compute_expiry_context(local_dt, config)
        time_favorability = derive_time_favorability(
            session_phase, expiry_context, config
        )

        label: dict = {
            "session_phase": session_phase,
            "minutes_since_open": minutes_since_open,
            "minutes_until_close": minutes_until_close,
            "expiry_context": expiry_context,
            "time_favorability": time_favorability,
        }
        if symbol is not None:
            label["symbol"] = symbol
        if timeframe is not None:
            label["timeframe"] = timeframe
        return label
    except Exception as exc:  # pragma: no cover - defensive; classifier is pure
        # The classifier must never raise into its callers (R3.1). Any unexpected
        # failure degrades to an honest Unavailable_Marker rather than an
        # exception or a fabricated label.
        return _unavailable(
            f"session classification error: {exc.__class__.__name__}",
            symbol,
            timeframe,
        )
