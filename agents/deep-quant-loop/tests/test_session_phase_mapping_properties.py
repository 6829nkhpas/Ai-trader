"""Property-based test for the Session_Phase boundary mapping (session.py, task 2.6).

Feature: session-expiry-awareness

This module implements design **Property 4: Session_Phase is well-formed and
matches the boundary mapping**:

    For any valid timestamp and configuration, the Session_Phase is exactly one
    of ``pre_open``, ``opening``, ``morning``, ``midday``, ``afternoon``,
    ``closing``, ``post_close``, and equals the phase dictated by comparing the
    timestamp's local time-of-day against the configured boundaries per the
    specified mapping table — in particular a local time before the configured
    open yields ``pre_open`` and a local time after the configured close yields
    ``post_close``, never an out-of-session error.

Validates: Requirements 1.3, 3.2.

The strategies generate arbitrary valid ``SessionConfig`` values (loadable
timezone, ``open_time < close_time``, non-negative window lengths, arbitrary
midday window, expiry weekday in ``[0, 6]``) together with two complementary
input families:

  * Arbitrary tz-aware local datetimes built directly from a generated
    time-of-day in the config's market timezone, exercising
    ``classify_session_phase`` across every time-of-day (including the exact
    boundary seconds), and
  * Arbitrary finite epoch-millisecond timestamps fed through the top-level
    ``classify_session`` so the seven-value membership guarantee is exercised on
    the public entry point too.

The expected phase is computed by an INDEPENDENT reference implementation of the
design's ordered boundary table (``_expected_phase``) — re-deriving the mapping
here, rather than calling the implementation's helper, is what makes this a real
check that the implementation matches the specified precedence.

The sys.path / import pattern mirrors the sibling ``test_session_*`` modules.
"""

import os
import sys
from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (session.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from session import (  # noqa: E402
    PHASE_AFTERNOON,
    PHASE_CLOSING,
    PHASE_MIDDAY,
    PHASE_MORNING,
    PHASE_POST_CLOSE,
    PHASE_PRE_OPEN,
    PHASE_OPENING,
    SessionConfig,
    classify_session,
    classify_session_phase,
    to_local_datetime,
)

# The complete, fixed seven-value Session_Phase enumeration (Requirement 1.3).
SEVEN_PHASES = {
    PHASE_PRE_OPEN,
    PHASE_OPENING,
    PHASE_MORNING,
    PHASE_MIDDAY,
    PHASE_AFTERNOON,
    PHASE_CLOSING,
    PHASE_POST_CLOSE,
}

_SECONDS_PER_MINUTE = 60

# A handful of real IANA timezones spanning offsets so the property is exercised
# independent of any single market timezone.
_TIMEZONES = [
    "Asia/Kolkata",
    "UTC",
    "America/New_York",
    "Europe/London",
    "Asia/Tokyo",
    "Australia/Sydney",
]


# ─────────────────────────────────────────────────────────────────────────────
# Independent reference implementation of the design's ordered boundary table.
# ─────────────────────────────────────────────────────────────────────────────


def _seconds_of_day(t: dtime) -> int:
    """Whole seconds since local midnight (hour/minute/second; microseconds are
    not part of the boundary comparison, matching the design's table)."""
    return t.hour * 3600 + t.minute * 60 + t.second


def _expected_phase(t_seconds: int, config: SessionConfig) -> str:
    """Re-derive the Session_Phase from the design's ordered mapping table.

    Rows are evaluated top-to-bottom; the FIRST matching row wins, so ``opening``
    and ``closing`` take precedence over ``morning`` / ``afternoon`` / ``midday``
    when the configured windows overlap (design, "Session_Phase classification"):

        t < open                                       -> pre_open
        t > close                                      -> post_close
        open <= t < open + opening_minutes             -> opening
        t >= close - closing_minutes (and t <= close)  -> closing
        midday_start <= t < midday_end                 -> midday
        open + opening_minutes <= t < midday_start     -> morning
        otherwise (in-session remainder)               -> afternoon
    """
    open_s = _seconds_of_day(config.open_time)
    close_s = _seconds_of_day(config.close_time)
    opening_end = open_s + config.opening_minutes * _SECONDS_PER_MINUTE
    closing_start = close_s - config.closing_minutes * _SECONDS_PER_MINUTE
    midday_start_s = _seconds_of_day(config.midday_start)
    midday_end_s = _seconds_of_day(config.midday_end)

    if t_seconds < open_s:
        return PHASE_PRE_OPEN
    if t_seconds > close_s:
        return PHASE_POST_CLOSE
    if t_seconds < opening_end:
        return PHASE_OPENING
    if t_seconds >= closing_start:
        return PHASE_CLOSING
    if midday_start_s <= t_seconds < midday_end_s:
        return PHASE_MIDDAY
    if opening_end <= t_seconds < midday_start_s:
        return PHASE_MORNING
    return PHASE_AFTERNOON


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────


@st.composite
def _time_of_day(draw):
    """An arbitrary valid 24h time-of-day (whole-second resolution)."""
    return dtime(
        draw(st.integers(min_value=0, max_value=23)),
        draw(st.integers(min_value=0, max_value=59)),
        draw(st.integers(min_value=0, max_value=59)),
    )


@st.composite
def _config(draw):
    """An arbitrary, internally consistent ``SessionConfig``.

    Mirrors what ``resolve_session_config`` would produce: a loadable timezone,
    ``open_time < close_time``, non-negative window lengths, and an expiry
    weekday in ``[0, 6]``. The midday window is left free (the classifier — and
    the reference mapping — are total over any time-of-day and any window
    configuration, including degenerate / overlapping windows).
    """
    tz = draw(st.sampled_from(_TIMEZONES))
    # open strictly before close, both inside the day (minute resolution).
    open_minutes = draw(st.integers(min_value=0, max_value=23 * 60))
    close_minutes = draw(st.integers(min_value=open_minutes + 1, max_value=24 * 60 - 1))
    open_time = dtime(open_minutes // 60, open_minutes % 60)
    close_time = dtime(close_minutes // 60, close_minutes % 60)
    return SessionConfig(
        timezone=tz,
        open_time=open_time,
        close_time=close_time,
        opening_minutes=draw(st.integers(min_value=0, max_value=180)),
        closing_minutes=draw(st.integers(min_value=0, max_value=180)),
        midday_start=draw(_time_of_day()),
        midday_end=draw(_time_of_day()),
        expiry_weekday=draw(st.integers(min_value=0, max_value=6)),
    )


# A "boundary-aware" time-of-day strategy: arbitrary times plus times sampled at
# and around the configured boundaries, so the exact precedence edges are hit.
@st.composite
def _boundary_aware_time(draw, config: SessionConfig):
    open_s = _seconds_of_day(config.open_time)
    close_s = _seconds_of_day(config.close_time)
    opening_end = open_s + config.opening_minutes * _SECONDS_PER_MINUTE
    closing_start = close_s - config.closing_minutes * _SECONDS_PER_MINUTE
    midday_start_s = _seconds_of_day(config.midday_start)
    midday_end_s = _seconds_of_day(config.midday_end)

    edges = []
    for base in (
        open_s,
        close_s,
        opening_end,
        closing_start,
        midday_start_s,
        midday_end_s,
    ):
        for delta in (-1, 0, 1):
            v = base + delta
            if 0 <= v <= 86399:
                edges.append(v)

    secs = draw(
        st.one_of(
            st.integers(min_value=0, max_value=86399),
            st.sampled_from(edges) if edges else st.integers(min_value=0, max_value=86399),
        )
    )
    return dtime(secs // 3600, (secs % 3600) // 60, secs % 60)


# Finite epoch-millisecond timestamps spanning ~1970 .. ~2065 (every weekday and
# time-of-day) for exercising the top-level public entry point.
_VALID_TS_MS = st.one_of(
    st.integers(min_value=0, max_value=3_000_000_000_000),
    st.floats(min_value=0.0, max_value=3.0e12, allow_nan=False, allow_infinity=False),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 4: Session_Phase is well-formed and matches the boundary mapping
# ─────────────────────────────────────────────────────────────────────────────


# Feature: session-expiry-awareness, Property 4: Session_Phase is well-formed and matches the boundary mapping
@settings(max_examples=200, deadline=None)
@given(data=st.data(), config=_config())
def test_property_4_phase_matches_boundary_mapping(data, config):
    """Validates: Requirements 1.3, 3.2

    For any local time-of-day (including the exact boundary seconds) in the
    configured market timezone, ``classify_session_phase`` returns exactly one of
    the seven phases and equals the phase dictated by the design's ordered
    boundary table — in particular ``pre_open`` strictly before the open and
    ``post_close`` strictly after the close, never an out-of-session error.
    """
    tod = data.draw(_boundary_aware_time(config))
    tz = ZoneInfo(config.timezone)
    # An arbitrary date is fine: phase depends only on the local time-of-day.
    local_dt = datetime(2024, 6, 12, tod.hour, tod.minute, tod.second, tzinfo=tz)

    phase = classify_session_phase(local_dt, config)

    # Well-formed: exactly one of the seven phases (Requirement 1.3).
    assert phase in SEVEN_PHASES, f"phase {phase!r} not in the seven-value enum"

    # Matches the ordered boundary mapping (Requirements 1.3, 3.2).
    t_seconds = _seconds_of_day(local_dt.time())
    expected = _expected_phase(t_seconds, config)
    assert phase == expected, (
        f"phase mismatch for t={tod} open={config.open_time} close={config.close_time} "
        f"opening_min={config.opening_minutes} closing_min={config.closing_minutes} "
        f"midday=[{config.midday_start},{config.midday_end}): "
        f"got {phase!r}, expected {expected!r}"
    )

    # Boundary specifics (Requirement 3.2): before open -> pre_open;
    # after close -> post_close; never an out-of-session error.
    open_s = _seconds_of_day(config.open_time)
    close_s = _seconds_of_day(config.close_time)
    if t_seconds < open_s:
        assert phase == PHASE_PRE_OPEN
    elif t_seconds > close_s:
        assert phase == PHASE_POST_CLOSE


# Feature: session-expiry-awareness, Property 4: Session_Phase is well-formed and matches the boundary mapping
@settings(max_examples=200, deadline=None)
@given(timestamp_ms=_VALID_TS_MS, config=_config())
def test_property_4_classify_session_phase_membership_via_entry_point(
    timestamp_ms, config
):
    """Validates: Requirements 1.3, 3.2

    For any valid (finite, representable) epoch-millisecond timestamp, the
    top-level ``classify_session`` produces a Session_Label whose ``session_phase``
    is one of the seven phases and matches the boundary mapping applied to that
    timestamp's local time-of-day in the configured timezone — confirming the
    public entry point honours the same total mapping.
    """
    local_dt = to_local_datetime(timestamp_ms, config)
    # Every value generated here is finite and in-range, so it must be classifiable.
    assert local_dt is not None

    result = classify_session(timestamp_ms, config, symbol="RELIANCE", timeframe="15m")

    assert "unavailable" not in result, "a valid timestamp must yield a Session_Label"
    phase = result["session_phase"]
    assert phase in SEVEN_PHASES, f"phase {phase!r} not in the seven-value enum"

    expected = _expected_phase(_seconds_of_day(local_dt.time()), config)
    assert phase == expected, (
        f"entry-point phase mismatch: got {phase!r}, expected {expected!r} "
        f"(local={local_dt.isoformat()})"
    )
