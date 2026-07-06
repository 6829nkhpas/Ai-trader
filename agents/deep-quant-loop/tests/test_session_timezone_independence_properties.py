"""Property-based test for timezone interpretation independent of host (session.py, task 2.5).

Feature: session-expiry-awareness

This module implements design **Property 3: Timestamp is interpreted in the
configured timezone, independent of the host**:

    The ``Session_Classifier`` always converts the epoch-millisecond timestamp to
    a timezone-aware datetime in the *configured* market timezone before deriving
    any field (AD-2). Therefore (a) the classification of a given timestamp is a
    function of ``config.timezone`` and the instant, never of the host machine's
    local timezone, and (b) the same instant classified under two different
    configured timezones yields the locally-correct phase for each zone.

Validates: Requirements 1.2, 3.5.

Three guarantees are exercised across the timestamp / timezone input space:

  * Configured-tz interpretation (R1.2): ``to_local_datetime`` returns a
    tz-aware datetime whose ``tzinfo`` is the configured timezone and whose
    wall-clock equals ``datetime.fromtimestamp(ts/1000, ZoneInfo(tz))``; the
    phase ``classify_session`` reports equals ``classify_session_phase`` on that
    local datetime.

  * Host independence (R3.5): mutating the host's local timezone (via
    ``os.environ['TZ']`` + ``time.tzset()`` where available) does not change the
    classification of a fixed timestamp under a fixed configured timezone.

  * Timezone dependence (R1.2/R3.5): the same instant classified under two
    timezones with different UTC offsets produces local datetimes that are each
    the locally-correct rendering of that instant — so the configured timezone,
    not the host, drives interpretation.

The sys.path / import pattern mirrors the sibling ``test_session_*`` modules:
the service directory (one level up) is prepended to ``sys.path`` so ``session``
is importable when pytest is run from anywhere.
"""

import os
import sys
import time as _time_mod
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
    classify_session,
    classify_session_phase,
    to_local_datetime,
)

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# A spread of IANA timezones with materially different UTC offsets (and DST
# behaviour) so the "configured tz drives interpretation" assertions bite.
_TIMEZONES = [
    "Asia/Kolkata",      # +05:30, no DST
    "UTC",               # +00:00
    "America/New_York",  # -05:00 / -04:00 (DST)
    "Europe/London",     # +00:00 / +01:00 (DST)
    "Asia/Tokyo",        # +09:00, no DST
    "Australia/Sydney",  # +10:00 / +11:00 (DST)
    "America/Los_Angeles",  # -08:00 / -07:00 (DST)
    "Pacific/Kiritimati",   # +14:00, extreme positive offset
]

# Host timezones to impose on the process while reclassifying a fixed timestamp.
# On platforms with ``time.tzset`` these actually change the C library's notion
# of local time; the classifier must ignore them entirely.
_HOST_TZS = ["UTC", "America/New_York", "Asia/Kolkata", "Pacific/Honolulu", "Etc/GMT-12"]

# 2021-01-01 .. ~2031 in epoch milliseconds: comfortably representable, and
# spread across weekdays / times-of-day so every session phase and expiry flag
# is reachable.
_VALID_MS = st.integers(min_value=1_609_459_200_000, max_value=1_924_991_999_000)


@st.composite
def _time_of_day(draw):
    """An arbitrary valid 24h time-of-day."""
    return dtime(
        draw(st.integers(min_value=0, max_value=23)),
        draw(st.integers(min_value=0, max_value=59)),
    )


@st.composite
def _config_for_tz(draw, timezone):
    """A valid ``SessionConfig`` carrying the given (loadable) ``timezone``.

    Mirrors what ``resolve_session_config`` produces: ``open_time < close_time``,
    non-negative window lengths, expiry weekday in ``[0, 6]``. The midday window
    is left free since the classifier is total over any time-of-day.
    """
    open_minutes = draw(st.integers(min_value=0, max_value=23 * 60))
    close_minutes = draw(st.integers(min_value=open_minutes + 1, max_value=24 * 60 - 1))
    return SessionConfig(
        timezone=timezone,
        open_time=dtime(open_minutes // 60, open_minutes % 60),
        close_time=dtime(close_minutes // 60, close_minutes % 60),
        opening_minutes=draw(st.integers(min_value=0, max_value=120)),
        closing_minutes=draw(st.integers(min_value=0, max_value=120)),
        midday_start=draw(_time_of_day()),
        midday_end=draw(_time_of_day()),
        expiry_weekday=draw(st.integers(min_value=0, max_value=6)),
    )


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
# Property 3: Timestamp is interpreted in the configured timezone, independent
# of the host.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 3: Timestamp is interpreted in the configured timezone, independent of the host
@settings(max_examples=200, deadline=None)
@given(
    timestamp_ms=_VALID_MS,
    data=st.data(),
)
def test_property_3_timestamp_interpreted_in_configured_timezone(timestamp_ms, data):
    """Validates: Requirements 1.2, 3.5

    For a fixed timestamp, the classifier interprets it in the *configured*
    timezone (R1.2) and is invariant to the host machine's local timezone
    (R3.5). Two distinct configured timezones for the same instant yield the
    locally-correct phase for each, proving the configured timezone — not the
    host — drives interpretation.
    """
    # Two distinct configured timezones for this property.
    tz_a = data.draw(st.sampled_from(_TIMEZONES), label="tz_a")
    tz_b = data.draw(st.sampled_from([t for t in _TIMEZONES if t != tz_a]), label="tz_b")
    config_a = data.draw(_config_for_tz(tz_a), label="config_a")
    config_b = data.draw(_config_for_tz(tz_b), label="config_b")

    # ── (1) Configured-tz interpretation (R1.2) ──────────────────────────────
    # The returned datetime is tz-aware in the configured zone and matches the
    # standalone fromtimestamp rendering in that zone, for both configs.
    for cfg in (config_a, config_b):
        local_dt = to_local_datetime(timestamp_ms, cfg)
        assert local_dt is not None, "valid timestamp must convert to a datetime"
        assert local_dt.tzinfo is not None, "returned datetime must be tz-aware"
        # ZoneInfo exposes the IANA key; it must equal the configured timezone.
        assert getattr(local_dt.tzinfo, "key", None) == cfg.timezone, (
            f"returned tzinfo {local_dt.tzinfo!r} does not match config tz "
            f"{cfg.timezone!r}"
        )
        expected = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=ZoneInfo(cfg.timezone))
        assert local_dt == expected, (
            f"local datetime {local_dt!r} != fromtimestamp in {cfg.timezone!r} "
            f"({expected!r}) — timestamp not interpreted in the configured tz"
        )
        # The classifier's reported phase is exactly the phase of that local_dt.
        result = classify_session(timestamp_ms, cfg)
        assert result.get("session_phase") == classify_session_phase(local_dt, cfg), (
            "classify_session phase must derive from the configured-tz local datetime"
        )

    # ── (2) Host independence (R3.5) ─────────────────────────────────────────
    # Reclassify under config_a while imposing several different host timezones;
    # the result must be byte-for-byte identical every time.
    baseline = classify_session(timestamp_ms, config_a, symbol="RELIANCE", timeframe="15m")
    baseline_local = to_local_datetime(timestamp_ms, config_a)
    prev_tz = os.environ.get("TZ")
    try:
        for host_tz in _HOST_TZS:
            _set_host_tz(host_tz)
            under_host = classify_session(
                timestamp_ms, config_a, symbol="RELIANCE", timeframe="15m"
            )
            assert under_host == baseline, (
                f"classification changed with host TZ={host_tz!r}: "
                f"{under_host!r} != {baseline!r}"
            )
            # The local datetime (instant + configured offset) is also host-invariant.
            assert to_local_datetime(timestamp_ms, config_a) == baseline_local, (
                f"to_local_datetime changed with host TZ={host_tz!r}"
            )
    finally:
        _restore_host_tz(prev_tz)

    # ── (3) Timezone dependence — locally correct under each zone (R1.2) ──────
    # The same instant under tz_a vs tz_b renders at the two zones' respective
    # wall-clocks. When their UTC offsets differ for this instant, the local
    # datetimes differ accordingly; when they momentarily coincide (e.g. UTC vs
    # London in winter) they agree — either way each equals its own zone's
    # locally-correct rendering, which the assertions in (1) already pin down.
    local_a = to_local_datetime(timestamp_ms, config_a)
    local_b = to_local_datetime(timestamp_ms, config_b)
    offset_a = local_a.utcoffset()
    offset_b = local_b.utcoffset()
    # The naive wall-clock difference equals the difference in UTC offsets.
    naive_diff = local_a.replace(tzinfo=None) - local_b.replace(tzinfo=None)
    assert naive_diff == (offset_a - offset_b), (
        f"wall-clock difference between {tz_a!r} and {tz_b!r} does not match "
        f"their UTC-offset difference — interpretation is not purely tz-driven"
    )
