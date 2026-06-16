"""Property-based test for Expiry_Context correctness (session.py, task 2.8).

Feature: session-expiry-awareness

This module implements design **Property 6: Expiry_Context is correct**:

    For any valid timestamp and configuration, the Expiry_Context reports
    ``is_expiry_day`` true exactly when the timestamp's local-date weekday equals
    the configured expiry weekday, and ``days_until_expiry`` equal to the number
    of calendar days until the next occurrence of that weekday — a value in
    ``[0, 6]`` that is zero precisely on the expiry day.

Validates: Requirements 2.1, 2.2.

The strategy generates arbitrary timezone-aware local datetimes (spanning a wide
date range and a spread of IANA market timezones) together with valid
``SessionConfig`` values whose ``expiry_weekday`` is drawn from ``[0, 6]`` (Mon=0
.. Sun=6). ``compute_expiry_context`` is asserted to satisfy the three clauses of
the property independently of the host timezone.

The sys.path / import pattern mirrors the existing ``test_session_*`` modules:
the service directory (one level up) is prepended to ``sys.path`` so ``session``
is importable when pytest is run from anywhere.
"""

import os
import sys
from datetime import datetime
from datetime import time as dtime
from datetime import timedelta
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (session.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from session import SessionConfig, compute_expiry_context  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

_TIMEZONES = [
    "Asia/Kolkata",
    "UTC",
    "America/New_York",
    "Europe/London",
    "Asia/Tokyo",
    "Australia/Sydney",
]


@st.composite
def _time_of_day(draw):
    """An arbitrary valid 24h time-of-day."""
    return dtime(
        draw(st.integers(min_value=0, max_value=23)),
        draw(st.integers(min_value=0, max_value=59)),
    )


@st.composite
def _local_datetime(draw):
    """An arbitrary timezone-aware local datetime in a configured market tz.

    The naive date/time is built from a wide range (≈1990..2065 across every
    month/day/hour/minute) and then attached to a generated IANA timezone, so the
    weekday spans all seven values and the property is exercised independent of
    the host's local timezone.
    """
    tz = ZoneInfo(draw(st.sampled_from(_TIMEZONES)))
    naive = draw(
        st.datetimes(
            min_value=datetime(1990, 1, 1, 0, 0),
            max_value=datetime(2065, 12, 31, 23, 59),
        )
    )
    return naive.replace(tzinfo=tz)


@st.composite
def _config(draw):
    """A valid ``SessionConfig`` with ``expiry_weekday`` drawn from ``[0, 6]``.

    The other fields are kept internally consistent (``open_time < close_time``,
    non-negative windows) but are irrelevant to ``compute_expiry_context``, which
    depends only on ``expiry_weekday``.
    """
    open_minutes = draw(st.integers(min_value=0, max_value=23 * 60))
    close_minutes = draw(st.integers(min_value=open_minutes + 1, max_value=24 * 60 - 1))
    return SessionConfig(
        timezone=draw(st.sampled_from(_TIMEZONES)),
        open_time=dtime(open_minutes // 60, open_minutes % 60),
        close_time=dtime(close_minutes // 60, close_minutes % 60),
        opening_minutes=draw(st.integers(min_value=0, max_value=120)),
        closing_minutes=draw(st.integers(min_value=0, max_value=120)),
        midday_start=draw(_time_of_day()),
        midday_end=draw(_time_of_day()),
        expiry_weekday=draw(st.integers(min_value=0, max_value=6)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 6: Expiry_Context is correct
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 6: Expiry_Context is correct
@settings(max_examples=200, deadline=None)
@given(local_dt=_local_datetime(), config=_config())
def test_property_6_expiry_context_is_correct(local_dt, config):
    """Validates: Requirements 2.1, 2.2

    For any tz-aware local datetime and valid config:
      * ``is_expiry_day`` is True iff the local date's weekday equals
        ``config.expiry_weekday`` (R2.1);
      * ``days_until_expiry`` equals ``(expiry_weekday - weekday) mod 7``, lies in
        ``[0, 6]``, and is ``0`` exactly on the expiry day (R2.2).
    """
    result = compute_expiry_context(local_dt, config)

    weekday = local_dt.weekday()
    expected_is_expiry = weekday == config.expiry_weekday
    expected_days = (config.expiry_weekday - weekday) % 7

    # is_expiry_day is a boolean, true exactly on the configured weekday (R2.1).
    assert isinstance(result["is_expiry_day"], bool)
    assert result["is_expiry_day"] is expected_is_expiry

    # days_until_expiry equals the calendar-day count to the next occurrence,
    # is in [0, 6], and is zero precisely on the expiry day (R2.2).
    days = result["days_until_expiry"]
    assert days == expected_days
    assert 0 <= days <= 6
    assert (days == 0) is expected_is_expiry

    # Cross-check: advancing the local date by days_until_expiry lands on the
    # configured expiry weekday (the "next occurrence" semantics of R2.2).
    landed = local_dt + timedelta(days=days)
    assert landed.weekday() == config.expiry_weekday
