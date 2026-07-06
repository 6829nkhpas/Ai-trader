# Feature: earnings-event-risk-gate, Property 3: Days-until-event is correct, bounded, and computed in the configured timezone
"""Property-based test for days-until-event correctness and bounds (events.py, task 2.5).

Feature: earnings-event-risk-gate

This module implements design **Property 3: Days-until-event is correct,
bounded, and computed in the configured timezone**:

    ``compute_days_until_event(reference_ms, event_ms, config)`` returns the
    whole number of calendar days between the reference's LOCAL date and the
    event's LOCAL date — both interpreted in ``config.timezone`` (R2.2, R3.5) —
    and that result is constrained to either ``None`` or a finite non-negative
    number (R3.3). For a valid (reference, event) pair the value matches an
    independently computed calendar-day difference in the configured timezone;
    a missing / non-numeric / non-finite timestamp yields ``None`` (never a
    fabricated number); and an event whose local date falls strictly before the
    reference's local date (a past event) yields ``None`` — never a negative
    count.

Validates: Requirements 2.2, 3.3.

Four guarantees are exercised across the (reference, event, timezone) space:

  * Correctness in the configured tz (R2.2): for any valid pair, the returned
    day count equals the whole-day difference between the two LOCAL dates,
    computed independently with ``zoneinfo`` / ``datetime`` in
    ``config.timezone`` (``None`` when that independent difference is negative).

  * Bounds (R3.3): the result is always either ``None`` or a finite,
    non-negative ``int`` — never negative, never a float NaN/inf.

  * Invalid-input safety (R3.1 surface of R3.3): a ``None`` / NaN / ±inf /
    non-numeric / boolean timestamp on either side yields ``None`` without
    raising and without fabricating a day count.

  * Past-event exclusion (R3.3 non-negativity): an event strictly before the
    reference date yields ``None`` rather than a negative number.

The sys.path / import bootstrap mirrors ``tests/test_event_config_default_fallback_properties.py``
and the date-math style mirrors ``tests/test_session_timezone_independence_properties.py``.
"""

import math
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import events  # noqa: E402
from events import EventConfig, compute_days_until_event  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Strategies / helpers
# ─────────────────────────────────────────────────────────────────────────────

# A spread of IANA timezones with materially different UTC offsets (and DST
# behaviour) so the "computed in the configured tz" guarantee bites: the same
# instant can land on different local calendar dates under different zones.
_TIMEZONES = [
    "Asia/Kolkata",         # +05:30, no DST
    "UTC",                  # +00:00
    "America/New_York",     # -05:00 / -04:00 (DST)
    "Europe/London",        # +00:00 / +01:00 (DST)
    "Asia/Tokyo",           # +09:00, no DST
    "Australia/Sydney",     # +10:00 / +11:00 (DST)
    "America/Los_Angeles",  # -08:00 / -07:00 (DST)
    "Pacific/Kiritimati",   # +14:00, extreme positive offset
]

_MS_PER_DAY = 86_400_000

# 2021-01-01 .. ~2031 in epoch milliseconds: comfortably representable as a
# datetime under every configured timezone above, spread across dates and
# times-of-day so date-boundary crossings are reachable.
_VALID_MS = st.integers(min_value=1_609_459_200_000, max_value=1_924_991_999_000)


def _config_for_tz(timezone: str) -> EventConfig:
    """A resolved-shaped ``EventConfig`` carrying the given (loadable) timezone.

    Only ``timezone`` matters for ``compute_days_until_event``; the remaining
    fields are set to their documented defaults so the config is well-formed.
    """
    return EventConfig(
        enabled=events.DEFAULT_EVENT_GATE_ENABLED,
        timezone=timezone,
        default_holding_horizon=events.DEFAULT_EVENT_DEFAULT_HOLDING_HORIZON,
        imminent_window_days=events.DEFAULT_EVENT_IMMINENT_WINDOW_DAYS,
        through_event_window_days=events.DEFAULT_EVENT_THROUGH_EVENT_WINDOW_DAYS,
        source_timeout_s=events.DEFAULT_EVENT_SOURCE_TIMEOUT_S,
        calendar_api_url=None,
        calendar_file_path=None,
    )


def _expected_days(reference_ms, event_ms, timezone):
    """Independently compute the expected whole calendar-day difference.

    Uses ``zoneinfo`` / ``datetime`` directly to render both instants to their
    LOCAL dates in ``timezone`` and take the whole-day difference. Returns
    ``None`` when the event's local date is strictly before the reference's
    local date (a past event is not a valid non-negative days-until value).
    """
    tz = ZoneInfo(timezone)
    ref_date = datetime.fromtimestamp(reference_ms / 1000.0, tz=tz).date()
    evt_date = datetime.fromtimestamp(event_ms / 1000.0, tz=tz).date()
    days = (evt_date - ref_date).days
    if days < 0:
        return None
    return days


# Invalid timestamp values: each must force a ``None`` result on either side.
# ``bool`` is included because the module's finite-number check excludes it
# (matching the repo-wide ``_is_num`` convention).
_invalid_ms = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.booleans(),
    st.text(alphabet="0123456789abcXYZ.-", min_size=0, max_size=6),
    st.just([]),
    st.just({}),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 3: Days-until-event is correct, bounded, and computed in the
# configured timezone.
# ─────────────────────────────────────────────────────────────────────────────


# Feature: earnings-event-risk-gate, Property 3: Days-until-event is correct, bounded, and computed in the configured timezone
@settings(max_examples=300, deadline=None)
@given(
    reference_ms=_VALID_MS,
    event_ms=_VALID_MS,
    timezone=st.sampled_from(_TIMEZONES),
)
def test_property_3_days_until_matches_independent_local_date_diff(
    reference_ms, event_ms, timezone
):
    """Feature: earnings-event-risk-gate, Property 3: for any valid (reference,
    event) pair, ``compute_days_until_event`` returns the whole calendar-day
    difference between the two LOCAL dates in ``config.timezone`` (or ``None``
    for a past event), and the result is always null-or-finite-non-negative.

    Validates: Requirements 2.2, 3.3
    """
    config = _config_for_tz(timezone)
    result = compute_days_until_event(reference_ms, event_ms, config)

    expected = _expected_days(reference_ms, event_ms, timezone)

    # Correctness in the configured timezone (R2.2): the module's value equals
    # the independently computed local-date difference (both None for past).
    assert result == expected, (
        f"days-until mismatch for tz={timezone!r}: got {result!r}, "
        f"expected {expected!r}"
    )

    # Bounds (R3.3): null, or a finite non-negative integer — never negative,
    # never a NaN/inf float.
    if result is None:
        assert expected is None
    else:
        assert isinstance(result, int) and not isinstance(result, bool), (
            f"expected an int day count, got {type(result).__name__}: {result!r}"
        )
        assert math.isfinite(result), "day count must be finite"
        assert result >= 0, f"day count must be non-negative, got {result!r}"


# Feature: earnings-event-risk-gate, Property 3: Days-until-event is correct, bounded, and computed in the configured timezone
@settings(max_examples=300, deadline=None)
@given(
    reference_ms=_VALID_MS,
    day_offset=st.integers(min_value=0, max_value=400),
    timezone=st.sampled_from(_TIMEZONES),
)
def test_property_3_future_offset_is_bounded_and_correct(
    reference_ms, day_offset, timezone
):
    """Feature: earnings-event-risk-gate, Property 3: an event a whole number of
    days after the reference yields a finite non-negative day count that matches
    the independently computed local-date difference in the configured tz.

    Constructing the event as ``reference + N*day`` exercises controlled,
    non-negative day gaps (including ``0``) so the correctness/bounds guarantee
    is checked on the intended upcoming-event input space.

    Validates: Requirements 2.2, 3.3
    """
    config = _config_for_tz(timezone)
    event_ms = reference_ms + day_offset * _MS_PER_DAY

    result = compute_days_until_event(reference_ms, event_ms, config)
    expected = _expected_days(reference_ms, event_ms, timezone)

    assert result == expected, (
        f"days-until mismatch for tz={timezone!r}, offset={day_offset}: "
        f"got {result!r}, expected {expected!r}"
    )
    # A non-negative ms offset can only produce a non-negative (or None) count.
    assert result is None or (
        isinstance(result, int)
        and not isinstance(result, bool)
        and math.isfinite(result)
        and result >= 0
    )
    # Adding whole days shifts the local date by at most that many days (a DST
    # transition can shift the count by ±1 relative to the raw offset, so the
    # count stays within the offset's immediate neighbourhood).
    if result is not None:
        assert abs(result - day_offset) <= 1, (
            f"day count {result} unexpectedly far from offset {day_offset}"
        )


# Feature: earnings-event-risk-gate, Property 3: Days-until-event is correct, bounded, and computed in the configured timezone
@settings(max_examples=200, deadline=None)
@given(
    reference_ms=_VALID_MS,
    event_ms=_VALID_MS,
    bad=_invalid_ms,
    which=st.sampled_from(["reference", "event", "both"]),
    timezone=st.sampled_from(_TIMEZONES),
)
def test_property_3_invalid_timestamp_yields_none(
    reference_ms, event_ms, bad, which, timezone
):
    """Feature: earnings-event-risk-gate, Property 3: a missing / non-numeric /
    non-finite timestamp on either (or both) side(s) yields ``None`` without
    raising and without fabricating a day count.

    Validates: Requirements 2.2, 3.3
    """
    config = _config_for_tz(timezone)
    ref = bad if which in ("reference", "both") else reference_ms
    evt = bad if which in ("event", "both") else event_ms

    result = compute_days_until_event(ref, evt, config)
    assert result is None, (
        f"invalid timestamp ({which}={bad!r}) must yield None, got {result!r}"
    )


# Feature: earnings-event-risk-gate, Property 3: Days-until-event is correct, bounded, and computed in the configured timezone
@settings(max_examples=200, deadline=None)
@given(
    reference_ms=_VALID_MS,
    days_before=st.integers(min_value=1, max_value=400),
    timezone=st.sampled_from(_TIMEZONES),
)
def test_property_3_past_event_yields_none_not_negative(
    reference_ms, days_before, timezone
):
    """Feature: earnings-event-risk-gate, Property 3: an event whose local date
    falls strictly before the reference's local date yields ``None`` — never a
    negative day count (R3.3 non-negativity).

    Subtracting at least two whole days guarantees the event's local date is
    strictly earlier than the reference's regardless of intraday time or a DST
    shift, so the past-event branch is deterministically exercised.

    Validates: Requirements 2.2, 3.3
    """
    config = _config_for_tz(timezone)
    # Subtract (days_before + 1) whole days so even a +/-1-day DST wobble cannot
    # push the event's local date to be on-or-after the reference date.
    event_ms = reference_ms - (days_before + 1) * _MS_PER_DAY

    result = compute_days_until_event(reference_ms, event_ms, config)
    assert result is None, (
        f"a strictly past event must yield None (never negative), got {result!r}"
    )
