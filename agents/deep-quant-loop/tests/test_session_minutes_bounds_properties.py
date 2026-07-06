"""Property-based test for session minutes bounds (session.py, task 2.7).

Feature: session-expiry-awareness

This module implements design **Property 5: Minutes-since-open and
minutes-until-close are correct and bounded**:

    For any valid timestamp and configuration, ``minutes_since_open`` and
    ``minutes_until_close`` are each either ``null`` (when the local time falls
    outside ``[open, close]``) or a finite non-negative number equal to the
    whole-minute delta from the open / to the close respectively.

Validates: Requirements 1.4, 3.3.

The strategy generates arbitrary tz-aware local datetimes (spanning before-open,
in-session, and after-close windows on every weekday) together with arbitrary,
internally consistent ``SessionConfig`` values (``open_time < close_time``). For
each pair it asserts:

  * Out of session (local time before the configured open or after the close):
    both ``compute_minutes_since_open`` and ``compute_minutes_until_close``
    return ``None`` (Requirements 1.4, 3.3).
  * In session: both return a finite, non-negative number equal to the exact
    whole-minute (floor) delta from the open / to the close respectively, and
    each is bounded by ``[0, session_length]``.
  * In-session consistency: ``minutes_since_open + minutes_until_close`` equals
    the session length within the floor rounding tolerance (i.e. it is either
    the session length or one minute less).

Timezones are restricted to DST-free zones (IST and friends) so the wall-clock
session boundaries line up with real elapsed time, keeping the consistency check
exact while still exercising the configured-timezone conversion path. The
sys.path / import pattern mirrors the sibling ``test_session_*`` modules.
"""

import math
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
    SessionConfig,
    compute_minutes_since_open,
    compute_minutes_until_close,
)

_SECONDS_PER_MINUTE = 60

# DST-free timezones: wall-clock time advances in lock-step with real elapsed
# time, so the session-length consistency check is exact (no DST hour to skew the
# open->close span). The configured-timezone conversion path is still exercised.
_DST_FREE_TZS = ["Asia/Kolkata", "UTC", "Asia/Tokyo", "Asia/Dubai", "Asia/Karachi"]


@st.composite
def _time_of_day(draw):
    """An arbitrary valid 24h time-of-day."""
    return dtime(
        draw(st.integers(min_value=0, max_value=23)),
        draw(st.integers(min_value=0, max_value=59)),
    )


@st.composite
def _config(draw):
    """An arbitrary, internally consistent ``SessionConfig`` (open < close)."""
    open_minutes = draw(st.integers(min_value=0, max_value=23 * 60))
    close_minutes = draw(st.integers(min_value=open_minutes + 1, max_value=24 * 60 - 1))
    return SessionConfig(
        timezone=draw(st.sampled_from(_DST_FREE_TZS)),
        open_time=dtime(open_minutes // 60, open_minutes % 60),
        close_time=dtime(close_minutes // 60, close_minutes % 60),
        opening_minutes=draw(st.integers(min_value=0, max_value=120)),
        closing_minutes=draw(st.integers(min_value=0, max_value=120)),
        midday_start=draw(_time_of_day()),
        midday_end=draw(_time_of_day()),
        expiry_weekday=draw(st.integers(min_value=0, max_value=6)),
    )


@st.composite
def _local_datetime(draw, config):
    """A tz-aware local datetime in ``config.timezone``.

    The date spans several years (every weekday) and the time-of-day spans the
    whole day (including seconds), so the generated instants land before open,
    inside the session, and after close.
    """
    date = draw(
        st.dates(
            min_value=datetime(2018, 1, 1).date(),
            max_value=datetime(2035, 12, 31).date(),
        )
    )
    hour = draw(st.integers(min_value=0, max_value=23))
    minute = draw(st.integers(min_value=0, max_value=59))
    second = draw(st.integers(min_value=0, max_value=59))
    naive = datetime(date.year, date.month, date.day, hour, minute, second)
    return naive.replace(tzinfo=ZoneInfo(config.timezone))


# ─────────────────────────────────────────────────────────────────────────────
# Property 5: Minutes-since-open and minutes-until-close are correct and bounded
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 5: Minutes-since-open and minutes-until-close are correct and bounded
@settings(max_examples=300, deadline=None)
@given(data=st.data(), config=_config())
def test_property_5_minutes_are_correct_and_bounded(data, config):
    """Validates: Requirements 1.4, 3.3

    For any tz-aware local datetime and valid config, ``minutes_since_open`` and
    ``minutes_until_close`` are each either ``None`` (out of session) or a finite
    non-negative whole-minute delta from the open / to the close, bounded by
    ``[0, session_length]`` and mutually consistent within the floor tolerance.
    """
    local_dt = data.draw(_local_datetime(config))

    mso = compute_minutes_since_open(local_dt, config)
    muc = compute_minutes_until_close(local_dt, config)

    # Reconstruct the open/close instants exactly as the implementation does so
    # the expected values and the in-session predicate are computed identically.
    open_dt = local_dt.replace(
        hour=config.open_time.hour,
        minute=config.open_time.minute,
        second=0,
        microsecond=0,
    )
    close_dt = local_dt.replace(
        hour=config.close_time.hour,
        minute=config.close_time.minute,
        second=0,
        microsecond=0,
    )
    in_session = open_dt <= local_dt <= close_dt

    if not in_session:
        # Out of session (before open or after close) -> both null (R1.4, R3.3).
        assert mso is None, f"expected None out of session, got {mso!r}"
        assert muc is None, f"expected None out of session, got {muc!r}"
        return

    # ── In session: both must be finite, non-negative numbers (R3.3). ─────────
    for name, value in (("minutes_since_open", mso), ("minutes_until_close", muc)):
        assert value is not None, f"{name} must not be None in session"
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
        assert math.isfinite(value), f"{name} must be finite, got {value!r}"
        assert value >= 0.0, f"{name} must be non-negative, got {value!r}"

    # ── Correctness: each equals the exact whole-minute (floor) delta (R1.4). ─
    expected_mso = float(
        math.floor((local_dt - open_dt).total_seconds() / _SECONDS_PER_MINUTE)
    )
    expected_muc = float(
        math.floor((close_dt - local_dt).total_seconds() / _SECONDS_PER_MINUTE)
    )
    assert mso == expected_mso, f"minutes_since_open: {mso} != expected {expected_mso}"
    assert muc == expected_muc, f"minutes_until_close: {muc} != expected {expected_muc}"

    # ── Bounds: each within [0, session_length] (R1.4, R3.3). ─────────────────
    session_length = (close_dt - open_dt).total_seconds() / _SECONDS_PER_MINUTE
    assert 0.0 <= mso <= session_length, f"mso {mso} out of [0, {session_length}]"
    assert 0.0 <= muc <= session_length, f"muc {muc} out of [0, {session_length}]"

    # ── Consistency: the two minute deltas sum to the session length within the
    # floor rounding tolerance (exactly the length, or one minute less). ───────
    total = mso + muc
    assert session_length - 1.0 - 1e-9 <= total <= session_length + 1e-9, (
        f"minutes_since_open + minutes_until_close = {total} not within floor "
        f"tolerance of session length {session_length}"
    )
