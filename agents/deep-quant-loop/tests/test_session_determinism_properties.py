"""Property-based test for session classification determinism (session.py, task 2.3).

Feature: session-expiry-awareness

This module implements design **Property 1: Classification is deterministic**:

    For any timestamp (valid or invalid) and configuration, invoking
    ``classify_session`` two or more times returns results (Session_Label or
    Unavailable_Marker, including the Session_Phase, minutes-since-open,
    minutes-until-close, the full Expiry_Context, and the Time_Favorability) that
    are element-wise identical across all invocations.

Validates: Requirements 1.6, 3.4.

The strategies below generate arbitrary epoch-millisecond timestamps — finite
values spanning many years (which reach the Session_Label path across every
phase / expiry combination) mixed with invalid timestamps (``None`` / ``NaN`` /
``+-inf`` / non-numeric / out-of-range) that drive the Unavailable_Marker path —
together with arbitrary ``SessionConfig`` values (and optional symbol/timeframe).
Determinism is asserted by classifying the *same* inputs several times and
requiring deep equality of the results, both with and without symbol/timeframe.

The sys.path / import pattern mirrors the existing session/regime test modules:
the service directory (one level up) is prepended to ``sys.path`` so ``session``
is importable when pytest is run from anywhere.
"""

import copy
import math
import os
import sys
from datetime import time as dtime

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (session.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from session import SessionConfig, classify_session  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Finite epoch-millisecond timestamps spanning roughly 1970-01-01 .. ~2065 so
# generated values land in every Session_Phase and on / off the expiry weekday,
# reaching the full Session_Label path.
_valid_ts_ms = st.floats(
    min_value=0.0,
    max_value=3.0e12,
    allow_nan=False,
    allow_infinity=False,
)

# Integer epoch-ms timestamps (the natural candle representation) over the same
# range, mixed in so the property covers both int and float inputs.
_valid_ts_ms_int = st.integers(min_value=0, max_value=3_000_000_000_000)

# Invalid timestamps that drive the Unavailable_Marker path (Requirement 3.1):
# missing, non-finite, non-numeric, or out-of-range epoch values.
_invalid_ts = st.sampled_from(
    [
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        "x",
        "1700000000000",
        True,
        [],
        {},
        1.0e30,   # out-of-range -> not representable as a datetime
        -1.0e30,
    ]
)

# Arbitrary timestamps: mostly valid (label path), occasionally invalid (marker
# path), so the determinism property covers both result families.
_timestamp = st.one_of(_valid_ts_ms, _valid_ts_ms_int, _invalid_ts)


@st.composite
def _time_of_day(draw):
    """An arbitrary valid 24h time-of-day."""
    return dtime(
        draw(st.integers(min_value=0, max_value=23)),
        draw(st.integers(min_value=0, max_value=59)),
    )


@st.composite
def _config(draw):
    """An arbitrary, internally consistent ``SessionConfig``.

    Mirrors what ``resolve_session_config`` would produce: a loadable timezone,
    ``open_time < close_time``, non-negative window lengths, and an expiry
    weekday in ``[0, 6]``. The property only needs a valid config object, so the
    midday window is left free (the classifier is total over any time-of-day).
    """
    tz = draw(
        st.sampled_from(
            [
                "Asia/Kolkata",
                "UTC",
                "America/New_York",
                "Europe/London",
                "Asia/Tokyo",
                "Australia/Sydney",
            ]
        )
    )
    # open strictly before close, both inside the day.
    open_minutes = draw(st.integers(min_value=0, max_value=23 * 60))
    close_minutes = draw(st.integers(min_value=open_minutes + 1, max_value=24 * 60 - 1))
    open_time = dtime(open_minutes // 60, open_minutes % 60)
    close_time = dtime(close_minutes // 60, close_minutes % 60)
    return SessionConfig(
        timezone=tz,
        open_time=open_time,
        close_time=close_time,
        opening_minutes=draw(st.integers(min_value=0, max_value=120)),
        closing_minutes=draw(st.integers(min_value=0, max_value=120)),
        midday_start=draw(_time_of_day()),
        midday_end=draw(_time_of_day()),
        expiry_weekday=draw(st.integers(min_value=0, max_value=6)),
    )


def _deep_equal(a, b):
    """Structural equality that treats two NaNs as equal.

    Session minutes are always a finite number or ``None`` by construction, so a
    plain ``==`` suffices; this helper additionally treats two NaNs as equal
    purely as a defensive guard so a (non-)deterministic NaN would still be
    caught as a *difference* rather than masked by ``nan != nan``.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return a == b
    return a == b


# ─────────────────────────────────────────────────────────────────────────────
# Property 1: Classification is deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 1: Classification is deterministic
@settings(max_examples=200, deadline=None)
@given(timestamp_ms=_timestamp, config=_config())
def test_property_1_classification_is_deterministic(timestamp_ms, config):
    """Validates: Requirements 1.6, 3.4

    Invoking ``classify_session`` repeatedly with an identical timestamp and an
    identical config returns element-wise identical results (whether a
    Session_Label or an Unavailable_Marker), including the Session_Phase,
    minutes-since-open, minutes-until-close, the full Expiry_Context, and the
    Time_Favorability.
    """
    # Snapshot the timestamp input so we can confirm the calls did not mutate it
    # (a mutation would be a hidden source of non-determinism across calls).
    ts_snapshot = copy.deepcopy(timestamp_ms)

    first = classify_session(timestamp_ms, config, symbol="RELIANCE", timeframe="15m")
    second = classify_session(timestamp_ms, config, symbol="RELIANCE", timeframe="15m")
    third = classify_session(timestamp_ms, config, symbol="RELIANCE", timeframe="15m")

    assert _deep_equal(first, second), (
        f"non-deterministic across invocations:\n first={first!r}\n second={second!r}"
    )
    assert _deep_equal(second, third), (
        f"non-deterministic across invocations:\n second={second!r}\n third={third!r}"
    )

    # Determinism must also hold for the bare (no symbol/timeframe) call shape:
    # the only difference between the two result families is the optional
    # symbol/timeframe keys, never the phase, minutes, expiry, or favorability.
    bare_first = classify_session(timestamp_ms, config)
    bare_second = classify_session(timestamp_ms, config)
    assert _deep_equal(bare_first, bare_second), (
        f"non-deterministic (bare call):\n first={bare_first!r}\n "
        f"second={bare_second!r}"
    )

    # Inputs must be left unmodified across all invocations (purity underpins
    # determinism — Requirements 1.6 / 3.4).
    assert _deep_equal(timestamp_ms, ts_snapshot) or timestamp_ms == ts_snapshot, (
        "classify_session mutated its timestamp input"
    )
