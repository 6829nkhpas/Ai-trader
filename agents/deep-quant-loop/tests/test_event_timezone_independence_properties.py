# Feature: earnings-event-risk-gate, Property 4: Classification is timezone-independent of the host
"""Property-based test for host-timezone independence (events.py, task 2.6).

Feature: earnings-event-risk-gate

This module implements design **Property 4: Classification is timezone-
independent of the host**:

    The Event_Classifier always interprets the reference and event epoch-
    millisecond timestamps in the *configured* market timezone
    (``config.timezone`` via ``zoneinfo``) before deriving the days-until-event
    and the classification (AD-1). Therefore the Event_Assessment (and the raw
    day count) of a given (reference, event, horizon, config) tuple is a
    function of ``config.timezone`` and the instants — never of the host
    machine's local timezone. The classifier never reads the host wall clock.

Validates: Requirements 3.5.

Three guarantees are exercised across the timestamp / timezone input space:

  * Host independence (R3.5): mutating the host's local timezone (via
    ``os.environ['TZ']`` + ``time.tzset()`` where available) does not change the
    day count ``compute_days_until_event`` returns nor the Event_Assessment
    ``assess_event_risk`` returns, for a fixed configuration. On platforms
    without ``time.tzset`` (e.g. Windows) the env var is still imposed so the
    classifier's indifference to it is exercised even though the C library's
    notion of local time does not move — and because interpretation goes through
    an explicit ``ZoneInfo(config.timezone)`` it is inherently host-invariant.

  * Configured-tz interpretation (R3.5): ``compute_days_until_event`` equals the
    whole-day difference between the reference and event *local dates* computed
    independently in ``config.timezone`` (or ``None`` for a past-dated event) —
    proving the configured timezone, not the host, drives the calendar-date
    boundary.

  * Timezone dependence: the same instants classified under two configured
    timezones with different UTC offsets each yield that zone's locally-correct
    day count, so the result tracks ``config.timezone`` rather than any single
    global clock.

The sys.path / import pattern and the ``os.environ`` TZ isolation mirror the
sibling ``tests/test_session_timezone_independence_properties.py`` module.
"""

import os
import sys
import time as _time_mod
from datetime import datetime
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from events import (  # noqa: E402
    EventConfig,
    HOLDING_HORIZONS,
    assess_event_risk,
    compute_days_until_event,
)

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# A spread of IANA timezones with materially different UTC offsets (and DST
# behaviour) so the "configured tz drives the calendar-date boundary"
# assertions bite.
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

# Host timezones to impose on the process while reclassifying fixed inputs. On
# platforms with ``time.tzset`` these actually change the C library's notion of
# local time; the classifier must ignore them entirely.
_HOST_TZS = ["UTC", "America/New_York", "Asia/Kolkata", "Pacific/Honolulu", "Etc/GMT-12"]

# 2021-01-01 .. ~2031 in epoch milliseconds: comfortably representable, and
# spread across weekdays / times-of-day so the local-date boundary (and hence a
# same-day vs next-day day count) is reachable under any configured tz.
_VALID_MS = st.integers(min_value=1_609_459_200_000, max_value=1_924_991_999_000)

# Holding_Horizon inputs: recognized values plus absent / unrecognized ones
# (which the classifier normalizes to the configured default). Host-independence
# must hold for every variant.
_HORIZON = st.one_of(
    st.none(),
    st.sampled_from(sorted(HOLDING_HORIZONS)),
    st.sampled_from(["swing", "positional", "", "MULTI_SESSION", "scalp"]),
)


@st.composite
def _config_for_tz(draw, timezone):
    """A valid ``EventConfig`` carrying the given (loadable) ``timezone``.

    Mirrors what ``resolve_event_config`` produces: non-negative window lengths
    with ``through_event_window_days <= imminent_window_days`` (the ordering
    invariant), a positive source timeout, and a recognized default horizon. The
    classification math depends only on ``timezone`` and the two window lengths;
    the remaining fields are filled with resolver-realistic values.
    """
    imminent = draw(st.integers(min_value=0, max_value=30))
    through = draw(st.integers(min_value=0, max_value=imminent))
    return EventConfig(
        enabled=draw(st.booleans()),
        timezone=timezone,
        default_holding_horizon=draw(st.sampled_from(sorted(HOLDING_HORIZONS))),
        imminent_window_days=imminent,
        through_event_window_days=through,
        source_timeout_s=draw(st.floats(min_value=0.1, max_value=60.0)),
        calendar_api_url=draw(st.one_of(st.none(), st.just("https://example.test/cal"))),
        calendar_file_path=draw(st.one_of(st.none(), st.just("/tmp/cal.json"))),
    )


def _standalone_days(reference_ms, event_ms, tz_name):
    """Independently compute the whole-day gap between the two instants' local
    calendar dates in ``tz_name`` (``None`` for a past-dated event).

    This is a from-scratch reimplementation of the classifier's date math using
    an explicit ``ZoneInfo`` — it never consults the host clock — so agreement
    with ``compute_days_until_event`` proves interpretation is purely configured-
    tz driven.
    """
    tz = ZoneInfo(tz_name)
    ref_date = datetime.fromtimestamp(reference_ms / 1000.0, tz=tz).date()
    evt_date = datetime.fromtimestamp(event_ms / 1000.0, tz=tz).date()
    days = (evt_date - ref_date).days
    return None if days < 0 else days


def _set_host_tz(tz_name):
    """Best-effort: impose ``tz_name`` as the host local timezone.

    Uses ``time.tzset()`` where available (POSIX). On platforms without
    ``tzset`` (e.g. Windows) the env var is still set so the classifier's
    indifference to it is exercised, even though the C library local time does
    not change. Returns the previous ``TZ`` value (or ``None``) for restoration.
    """
    prev = os.environ.get("TZ")
    os.environ["TZ"] = tz_name
    if hasattr(_time_mod, "tzset"):
        _time_mod.tzset()
    return prev


def _restore_host_tz(prev):
    """Restore the host ``TZ`` env var captured by ``_set_host_tz``."""
    if prev is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = prev
    if hasattr(_time_mod, "tzset"):
        _time_mod.tzset()


# ─────────────────────────────────────────────────────────────────────────────
# Property 4: Classification is timezone-independent of the host.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 4: Classification is timezone-independent of the host
@settings(max_examples=200, deadline=None)
@given(
    reference_ms=_VALID_MS,
    event_ms=_VALID_MS,
    holding_horizon=_HORIZON,
    data=st.data(),
)
def test_property_4_classification_is_host_timezone_independent(
    reference_ms, event_ms, holding_horizon, data
):
    """Validates: Requirements 3.5

    For fixed inputs and a fixed configuration, both the raw day count and the
    full Event_Assessment are invariant to the host machine's local timezone
    (R3.5), because interpretation always goes through ``config.timezone``. Two
    distinct configured timezones for the same instants each yield that zone's
    locally-correct day count, proving the configured timezone — not the host —
    drives the calendar-date boundary.
    """
    # Two distinct configured timezones for this property.
    tz_a = data.draw(st.sampled_from(_TIMEZONES), label="tz_a")
    tz_b = data.draw(st.sampled_from([t for t in _TIMEZONES if t != tz_a]), label="tz_b")
    config_a = data.draw(_config_for_tz(tz_a), label="config_a")
    config_b = data.draw(_config_for_tz(tz_b), label="config_b")

    # ── (1) Configured-tz interpretation (R3.5) ──────────────────────────────
    # The classifier's day count equals an independent computation of the local-
    # date gap in the configured timezone, for both configs.
    for cfg in (config_a, config_b):
        got = compute_days_until_event(reference_ms, event_ms, cfg)
        expected = _standalone_days(reference_ms, event_ms, cfg.timezone)
        assert got == expected, (
            f"days-until-event {got!r} != independent local-date gap {expected!r} "
            f"in configured tz {cfg.timezone!r} — interpretation is not purely "
            f"configured-tz driven"
        )

    # ── (2) Host independence (R3.5) ─────────────────────────────────────────
    # Reclassify under config_a while imposing several different host timezones;
    # both the day count and the full assessment must be identical every time.
    baseline_days = compute_days_until_event(reference_ms, event_ms, config_a)
    baseline_assessment = assess_event_risk(
        reference_ms,
        event_ms,
        holding_horizon,
        config_a,
        symbol="RELIANCE",
        event_date="2025-01-15",
    )
    prev_tz = os.environ.get("TZ")
    try:
        for host_tz in _HOST_TZS:
            _set_host_tz(host_tz)
            under_days = compute_days_until_event(reference_ms, event_ms, config_a)
            assert under_days == baseline_days, (
                f"days-until-event changed with host TZ={host_tz!r}: "
                f"{under_days!r} != {baseline_days!r}"
            )
            under_assessment = assess_event_risk(
                reference_ms,
                event_ms,
                holding_horizon,
                config_a,
                symbol="RELIANCE",
                event_date="2025-01-15",
            )
            assert under_assessment == baseline_assessment, (
                f"assessment changed with host TZ={host_tz!r}: "
                f"{under_assessment!r} != {baseline_assessment!r}"
            )
    finally:
        _restore_host_tz(prev_tz)

    # ── (3) Timezone dependence — locally correct under each zone (R3.5) ──────
    # The same instants under tz_a vs tz_b each render their day count against
    # that zone's local calendar dates. Each must equal its own zone's
    # independently-computed gap (already pinned in (1)); this asserts the two
    # results are governed by their respective configured zones, not a shared
    # host clock.
    days_a = compute_days_until_event(reference_ms, event_ms, config_a)
    days_b = compute_days_until_event(reference_ms, event_ms, config_b)
    assert days_a == _standalone_days(reference_ms, event_ms, tz_a)
    assert days_b == _standalone_days(reference_ms, event_ms, tz_b)
