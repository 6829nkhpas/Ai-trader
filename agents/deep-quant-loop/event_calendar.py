"""
Event_Source adapter: NSE's corporate event calendar -> the shape `get_event_risk` reads.

WHY THIS MODULE EXISTS
----------------------
`get_event_risk` classifies earnings/results proximity from an operator-configured
Event_Source (`EVENT_CALENDAR_FILE` and/or `EVENT_CALENDAR_API_URL`). Neither was
configured, so the tool returned an honest "no event source configured" marker and
event risk was never assessed. Kite serves no earnings calendar, so the data has to
come from somewhere else.

NSE publishes it for free and authoritatively: companies file board-meeting
intimations under SEBI LODR, and NSE exposes them as JSON. That endpoint is a near
perfect match for what `tools.py::_read_event_api` already parses — a list of
records keyed by `symbol` with a `date` field — but it cannot be pointed at
directly for three measured reasons:

  1. It refuses a bare request. The agent's reader sends no User-Agent and no
     cookies; that call times out. NSE needs a browser UA and a cookie primed from
     the homepage first.
  2. Its dates are `01-Sep-2026` (DD-Mon-YYYY). `_parse_event_date_to_ms` accepts
     only `YYYY-MM-DD` or an ISO datetime, so every date would be dropped.
  3. Most rows are not earnings. In a sample of 35 rows exactly ONE was
     "Financial Results"; the rest were Fund Raising, Dividend, AGM, Buyback.

Fixing those inside `tools.py` would put one vendor's quirks inside the agent's
vendor-agnostic reader, which its design explicitly avoids ("never scrapes or
hardcodes a specific paid vendor"). So the quirks live HERE, behind an endpoint the
operator configures, and the agent keeps reading a generic shape. Swapping NSE for
a paid feed later means editing this file only.

THE SYMBOL FILTER IS LOAD-BEARING, NOT AN OPTIMISATION
------------------------------------------------------
`tools.py::_read_event_api` unions two collectors:

    _collect_symbol_dates(body, symbol) + _collect_api_dates(body, symbol)

`_collect_symbol_dates` filters records by symbol. `_collect_api_dates` does NOT —
for a list body it harvests the `date` of EVERY record, ignoring its `symbol`
argument, because it exists to handle already-symbol-scoped responses. So if this
adapter returned the whole calendar, every other company's board-meeting date
would become a candidate event for the queried symbol, and the gate would size
down a RELIANCE trade because some unrelated microcap meets tomorrow. This adapter
therefore returns ONLY the requested symbol's rows. `test_returns_no_other_symbols_rows`
pins it.

(The two collectors both pick up `date`, so each row's date is contributed twice.
That is harmless: `events.select_next_event` takes the nearest future candidate, and
duplicates cannot change a minimum.)

FAILURE MUST NOT LOOK LIKE "NO EVENT"
-------------------------------------
The agent distinguishes three unavailable reasons, and two of them are reached
through this endpoint's status code:

  * non-2xx / unreachable -> "event source retrieval failed: ..."  (R1.4)
  * 200 with []           -> "no upcoming scheduled event known for symbol" (R1.3)

Those mean different things to a trader: the first is "we are blind", the second is
"we looked and the calendar is clear". So an upstream failure with no usable cache
returns 502 and NEVER an empty list. Serving `[]` on failure would report a clear
calendar for a company that might report tomorrow.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

# ── Defaults ─────────────────────────────────────────────────────────────────

# NSE's corporate-filings event calendar. Operator-overridable so this adapter can
# be repointed at a paid feed (or a mock, in an integration test) without a code
# change; anything returning NSE's record shape works as-is.
DEFAULT_SOURCE_URL = "https://www.nseindia.com/api/event-calendar"

# The page whose response primes the cookies the API call needs.
DEFAULT_PRIME_URL = "https://www.nseindia.com"

# How long a fetched calendar stays fresh. The calendar changes at most daily, and
# NSE throttles aggressively, so 30 minutes keeps us to ~48 upstream fetches a day
# while still picking up an intimation filed mid-session.
DEFAULT_TTL_SECONDS = 1800.0

# Upstream request timeout, per request (prime and fetch each get this).
DEFAULT_TIMEOUT_SECONDS = 15.0

# How long a STALE calendar may still be served after the upstream starts failing.
# Beyond this the endpoint fails loudly rather than answering from a stale cache:
# an event date that has since been revised is worse than an honest "blind".
DEFAULT_STALE_GRACE_SECONDS = 21600.0  # 6h

# Substrings (case-insensitive) a row's `purpose` must contain to count as a
# Scheduled_Event. Default targets results/earnings, which is what the gate is
# documented to assess ("earnings/results proximity risk"). Set
# EVENT_CALENDAR_PURPOSES to a comma-separated list to widen (e.g.
# "result,dividend,buyback"), or to a single blank entry to accept every row.
DEFAULT_PURPOSES = ("result",)

# A browser UA is required; NSE times out a request without one.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ── Environment variable names ───────────────────────────────────────────────

ENV_SOURCE_URL = "EVENT_CALENDAR_SOURCE_URL"
ENV_PRIME_URL = "EVENT_CALENDAR_PRIME_URL"
ENV_TTL_SECONDS = "EVENT_CALENDAR_TTL_SECS"
ENV_TIMEOUT_SECONDS = "EVENT_CALENDAR_TIMEOUT_S"
ENV_STALE_GRACE_SECONDS = "EVENT_CALENDAR_STALE_GRACE_SECS"
ENV_PURPOSES = "EVENT_CALENDAR_PURPOSES"

# ── Month names -> number ────────────────────────────────────────────────────
#
# An explicit table, NOT `datetime.strptime(value, "%d-%b-%Y")`.
#
# `%b` resolves month abbreviations through the process LOCALE. This code is
# developed on a Windows host and runs in a Linux container, and a container whose
# locale is not English would fail to parse "Sep" — every date silently dropped,
# the endpoint returning [] , and the agent concluding the calendar is clear. NSE
# emits English abbreviations regardless of who is asking, so the mapping is fixed
# data, not a locale question.
_MONTHS: Dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


class EventCalendarUnavailable(RuntimeError):
    """The upstream calendar could not be read and no usable cache was available.

    Raised so the route can answer non-2xx, which the agent records as a
    retrieval failure rather than an empty calendar. Carries the cause for the log
    line and the response body.
    """


# ── Config ───────────────────────────────────────────────────────────────────


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw.strip() if isinstance(raw, str) and raw.strip() else default


def _env_float(name: str, default: float, minimum: float) -> float:
    """Resolve a positive float, falling back to ``default`` on anything unusable.

    Total by design: a typo in an env var must not take the endpoint down, it must
    leave the documented default in place.
    """
    raw = os.getenv(name)
    if not isinstance(raw, str) or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return default
    if not (value > minimum) or value != value:  # NaN-safe
        return default
    return value


def resolve_purposes() -> Tuple[str, ...]:
    """The purpose substrings that qualify a row, from the env or the default.

    An env value of a single blank entry (``EVENT_CALENDAR_PURPOSES=""`` is
    indistinguishable from unset, so use ``EVENT_CALENDAR_PURPOSES=*``) disables
    filtering and accepts every board meeting.
    """
    raw = os.getenv(ENV_PURPOSES)
    if not isinstance(raw, str) or not raw.strip():
        return DEFAULT_PURPOSES
    if raw.strip() == "*":
        return ()
    parts = tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    return parts or DEFAULT_PURPOSES


# ── Pure helpers (no I/O — these carry the tests) ─────────────────────────────


def to_iso_date(value: Any) -> Optional[str]:
    """Convert NSE's ``DD-Mon-YYYY`` to ISO ``YYYY-MM-DD``.

    Returns None for anything unparseable rather than guessing, so a format change
    upstream drops the row instead of inventing a date. Already-ISO input is passed
    through, which keeps this a no-op if the source is later repointed at a feed
    that already emits ISO. Never raises.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    # Already ISO (YYYY-MM-DD, possibly with a time component we discard).
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        head = text[:10]
        try:
            year, month, day = int(head[0:4]), int(head[5:7]), int(head[8:10])
        except ValueError:
            return None
        return _iso_or_none(year, month, day)

    parts = text.split("-")
    if len(parts) != 3:
        return None
    day_s, mon_s, year_s = parts
    month = _MONTHS.get(mon_s.strip().lower()[:3])
    if month is None:
        return None
    try:
        day, year = int(day_s.strip()), int(year_s.strip())
    except ValueError:
        return None
    return _iso_or_none(year, month, day)


def _iso_or_none(year: int, month: int, day: int) -> Optional[str]:
    """Format a validated Y/M/D as ISO, rejecting impossible calendar dates."""
    if not (1 <= month <= 12) or not (1 <= day <= 31) or not (1970 <= year <= 2999):
        return None
    # Reject 31-Feb and friends: the agent anchors the date at midnight and a
    # bogus one would be dropped there anyway, but silently — better to drop it
    # here where the reason is visible.
    try:
        import datetime as _dt

        _dt.date(year, month, day)
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def purpose_matches(purpose: Any, purposes: Tuple[str, ...]) -> bool:
    """Whether a row's ``purpose`` qualifies as a Scheduled_Event.

    An empty ``purposes`` tuple accepts everything (operator opted out of
    filtering). A non-string purpose never matches a non-empty filter — a row we
    cannot classify must not be assumed to be earnings.
    """
    if not purposes:
        return True
    if not isinstance(purpose, str):
        return False
    text = purpose.lower()
    return any(p in text for p in purposes)


def normalise_rows(raw: Any, purposes: Tuple[str, ...]) -> List[Dict[str, str]]:
    """Project NSE's payload into ``[{symbol, date, purpose, company}]``.

    Drops any row without a usable symbol or a convertible date, and any row whose
    purpose does not qualify. Pure and total: a shape change upstream yields fewer
    rows, never an exception and never a fabricated date.
    """
    out: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        if not isinstance(row, dict):
            continue
        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            continue
        iso = to_iso_date(row.get("date"))
        if iso is None:
            continue
        purpose = row.get("purpose")
        if not purpose_matches(purpose, purposes):
            continue
        out.append(
            {
                "symbol": symbol.strip().upper(),
                "date": iso,
                "purpose": purpose.strip() if isinstance(purpose, str) else "",
                "company": row.get("company", "") if isinstance(row.get("company"), str) else "",
            }
        )
    return out


def rows_for_symbol(rows: List[Dict[str, str]], symbol: Any) -> List[Dict[str, str]]:
    """The subset of ``rows`` belonging to ``symbol`` (case-insensitive, exact).

    Exact match on the NSE tradingsymbol, never a substring: "INFY" must not pick
    up "INFYTECH". An empty/non-string symbol yields no rows rather than the whole
    calendar — see the module docstring on why leaking other symbols' rows is a
    correctness bug, not a performance one.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        return []
    target = symbol.strip().upper()
    return [r for r in rows if r.get("symbol", "").upper() == target]


# ── Cached upstream read ─────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cached_rows: Optional[List[Dict[str, str]]] = None
_cached_at: float = 0.0
_cached_purposes: Tuple[str, ...] = ()


def _fetch_upstream(source_url: str, prime_url: str, timeout: float) -> Any:
    """Fetch the raw calendar payload, priming cookies first.

    Both requests share one client so the cookies set by the prime response are
    sent with the API call. Raises on any transport error or non-2xx so the caller
    can decide between serving stale and failing.
    """
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": prime_url,
    }
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        # Priming is best-effort: its job is only to populate the cookie jar. A
        # failure here is not fatal on its own — the API call below is what must
        # succeed, and it reports its own status.
        try:
            client.get(prime_url)
        except Exception:
            pass
        response = client.get(source_url)
        if response.status_code < 200 or response.status_code >= 300:
            raise EventCalendarUnavailable(
                f"upstream returned HTTP {response.status_code}"
            )
        try:
            return response.json()
        except Exception as exc:
            raise EventCalendarUnavailable("upstream returned an unparseable body") from exc


def get_calendar(force_refresh: bool = False) -> Tuple[List[Dict[str, str]], bool]:
    """Return ``(rows, stale)`` for the whole calendar, refreshing when due.

    Double-checked under a lock so a burst of concurrent requests triggers ONE
    upstream fetch rather than one each — this endpoint is called on every event
    risk assessment, and NSE throttles.

    On an upstream failure a cached calendar within the stale grace window is
    returned with ``stale=True``; past that window, or with nothing cached at all,
    :class:`EventCalendarUnavailable` propagates so the route can answer non-2xx.
    """
    global _cached_rows, _cached_at, _cached_purposes

    ttl = _env_float(ENV_TTL_SECONDS, DEFAULT_TTL_SECONDS, 0.0)
    timeout = _env_float(ENV_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS, 0.0)
    grace = _env_float(ENV_STALE_GRACE_SECONDS, DEFAULT_STALE_GRACE_SECONDS, 0.0)
    source_url = _env_str(ENV_SOURCE_URL, DEFAULT_SOURCE_URL)
    prime_url = _env_str(ENV_PRIME_URL, DEFAULT_PRIME_URL)
    purposes = resolve_purposes()

    now = time.monotonic()

    def _fresh_enough() -> bool:
        # A purposes change invalidates the cache: the cached rows were filtered
        # under the old setting, so serving them would silently ignore the new one.
        return (
            _cached_rows is not None
            and _cached_purposes == purposes
            and (now - _cached_at) < ttl
            and not force_refresh
        )

    if _fresh_enough():
        return list(_cached_rows or []), False

    with _cache_lock:
        # Re-check inside the lock: another thread may have refreshed while we
        # waited, in which case its result is fresh and we must not refetch.
        now = time.monotonic()
        if _fresh_enough():
            return list(_cached_rows or []), False

        try:
            raw = _fetch_upstream(source_url, prime_url, timeout)
        except Exception as exc:
            age = time.monotonic() - _cached_at
            if _cached_rows is not None and _cached_purposes == purposes and age <= grace:
                print(
                    f"[EventCalendar] WARN upstream failed ({exc}); serving cache "
                    f"aged {age:.0f}s"
                )
                return list(_cached_rows), True
            raise EventCalendarUnavailable(str(exc)) from exc

        rows = normalise_rows(raw, purposes)
        _cached_rows = rows
        _cached_at = time.monotonic()
        _cached_purposes = purposes
        print(
            f"[EventCalendar] refreshed: {len(rows)} qualifying row(s) "
            f"from {len(raw) if isinstance(raw, list) else 0} upstream "
            f"(purposes={list(purposes) or 'ALL'})"
        )
        return list(rows), False


def reset_cache() -> None:
    """Drop the cached calendar. For tests; never called in the request path."""
    global _cached_rows, _cached_at, _cached_purposes
    with _cache_lock:
        _cached_rows = None
        _cached_at = 0.0
        _cached_purposes = ()


def events_for_symbol(symbol: str) -> Tuple[List[Dict[str, str]], bool]:
    """``(rows, stale)`` for one symbol — the endpoint's whole job.

    Propagates :class:`EventCalendarUnavailable` so a blind endpoint is reported as
    blind. An empty list is a POSITIVE statement that the calendar is clear for
    this symbol, so it is only ever returned from a successful read.
    """
    rows, stale = get_calendar()
    return rows_for_symbol(rows, symbol), stale
