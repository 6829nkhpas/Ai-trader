# Feature: session-expiry-awareness, Property 28: Open/close ordering is enforced
"""Property-based test for open/close ordering enforcement (session.py, task 1.3).

Feature: session-expiry-awareness

This module implements design **Property 28: Open/close ordering is enforced**:

    For any environment in which the resolved open time is not strictly before
    the resolved close time, ``resolve_session_config`` applies the documented
    default open and close times together without raising.

Validates: Requirements 12.5.

Strategy: ``SESSION_OPEN`` / ``SESSION_CLOSE`` are assigned valid ``HH:MM``
time-of-day strings (so per-parameter resolution keeps them verbatim) drawn so
that ``open >= close`` — exactly the out-of-order precondition Property 28
guards. Because both parse as valid times, the only thing that can fix the
ordering is the guard, which must revert BOTH the open and the close to their
documented defaults together (09:15 / 15:30). A complementary case feeds valid
``open < close`` pairs and asserts they are honoured verbatim (the guard does
not fire), so the test pins both sides of the ordering rule. The remaining
SESSION_* parameters are assigned arbitrary values (unset / garbage /
out-of-range) to show the ordering enforcement is independent of them.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_of_config_pressure_ordering_properties.py``.
"""

import os
import sys
from contextlib import contextmanager
from datetime import time as dtime

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (session.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import session  # noqa: E402
from session import (  # noqa: E402
    DEFAULT_SESSION_CLOSE,
    DEFAULT_SESSION_OPEN,
    SessionConfig,
    resolve_session_config,
)

# Every SESSION_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_SESSION_ENV_VARS = (
    session.ENV_SESSION_TIMEZONE,
    session.ENV_SESSION_OPEN,
    session.ENV_SESSION_CLOSE,
    session.ENV_OPENING_MINUTES,
    session.ENV_CLOSING_MINUTES,
    session.ENV_MIDDAY_START,
    session.ENV_MIDDAY_END,
    session.ENV_EXPIRY_WEEKDAY,
)


@contextmanager
def _session_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every SESSION_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the
    test body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_SESSION_ENV_VARS}
    try:
        for name in _ALL_SESSION_ENV_VARS:
            os.environ.pop(name, None)
        for name, value in overrides.items():
            os.environ[name] = value
        yield
    finally:
        for name, prior in saved.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior


def _hhmm(total_minutes):
    """Render a minute-of-day count (0..1439) as a zero-padded ``HH:MM`` string."""
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _minutes_to_time(total_minutes):
    """Build the ``datetime.time`` the resolver should parse from ``_hhmm``."""
    return dtime(total_minutes // 60, total_minutes % 60)


# An arbitrary value for the *other* (non-open/close) parameters: ``None`` leaves
# the var unset; any string spans the realistic input space (valid, empty,
# whitespace, garbage, out-of-range). These must not affect whether the
# open/close ordering guard fires.
_other_value = st.one_of(
    st.none(),
    st.just(""),
    st.just("   "),
    st.just("not-a-time"),
    st.integers(min_value=-500, max_value=500).map(str),
    st.text(max_size=6),
)


@st.composite
def _open_not_before_close(draw):
    """Draw an in-range ``HH:MM`` pair with ``open >= close`` (out of order).

    Both values are valid 24h times so per-parameter resolution keeps them
    verbatim; constraining ``open >= close`` guarantees the resolved open is
    *not strictly before* the resolved close — the precondition of Property 28.
    Equality is allowed so the ``open == close`` boundary (still "not strictly
    before") is exercised.
    """
    close_min = draw(st.integers(min_value=0, max_value=1439))
    open_min = draw(st.integers(min_value=close_min, max_value=1439))
    return open_min, close_min


@st.composite
def _open_before_close(draw):
    """Draw an in-range ``HH:MM`` pair with ``open < close`` (correctly ordered)."""
    open_min = draw(st.integers(min_value=0, max_value=1438))
    close_min = draw(st.integers(min_value=open_min + 1, max_value=1439))
    return open_min, close_min


# ─────────────────────────────────────────────────────────────────────────────
# Property 28 (task 1.3): Open/close ordering is enforced
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 28: Open/close ordering is enforced
@settings(max_examples=200, deadline=None)
@given(
    pair=_open_not_before_close(),
    timezone=_other_value,
    opening_minutes=_other_value,
    closing_minutes=_other_value,
    midday_start=_other_value,
    midday_end=_other_value,
    expiry_weekday=_other_value,
)
def test_property_28_open_not_before_close_reverts_both_to_defaults(
    pair,
    timezone,
    opening_minutes,
    closing_minutes,
    midday_start,
    midday_end,
    expiry_weekday,
):
    """Feature: session-expiry-awareness, Property 28: Open/close ordering is
    enforced — for any environment in which the resolved open time is not
    strictly before the resolved close time, ``resolve_session_config`` reverts
    BOTH the open and close times to their documented defaults together and
    never raises.

    Validates: Requirements 12.5
    """
    open_min, close_min = pair

    candidate = {
        session.ENV_SESSION_OPEN: _hhmm(open_min),
        session.ENV_SESSION_CLOSE: _hhmm(close_min),
        session.ENV_SESSION_TIMEZONE: timezone,
        session.ENV_OPENING_MINUTES: opening_minutes,
        session.ENV_CLOSING_MINUTES: closing_minutes,
        session.ENV_MIDDAY_START: midday_start,
        session.ENV_MIDDAY_END: midday_end,
        session.ENV_EXPIRY_WEEKDAY: expiry_weekday,
    }
    # ``None`` means "leave unset"; everything else is set verbatim.
    overrides = {name: value for name, value in candidate.items() if value is not None}

    with _session_env(overrides):
        config = resolve_session_config()

    # The resolver never raised and produced a fully-formed SessionConfig.
    assert isinstance(config, SessionConfig)

    # Sanity: the precondition we constructed (resolved open >= resolved close)
    # truly holds for these in-range inputs before the guard reverts them.
    assert _minutes_to_time(open_min) >= _minutes_to_time(close_min)

    # The ordering guard reverted BOTH the open and close times to their
    # documented defaults together — never just one, never the out-of-order pair.
    assert config.open_time == DEFAULT_SESSION_OPEN
    assert config.close_time == DEFAULT_SESSION_CLOSE

    # And the documented defaults themselves satisfy the strict ordering.
    assert config.open_time < config.close_time


# Feature: session-expiry-awareness, Property 28: Open/close ordering is enforced
@settings(max_examples=200, deadline=None)
@given(
    pair=_open_before_close(),
    timezone=_other_value,
    opening_minutes=_other_value,
    closing_minutes=_other_value,
    midday_start=_other_value,
    midday_end=_other_value,
    expiry_weekday=_other_value,
)
def test_property_28_valid_open_before_close_is_honored(
    pair,
    timezone,
    opening_minutes,
    closing_minutes,
    midday_start,
    midday_end,
    expiry_weekday,
):
    """Feature: session-expiry-awareness, Property 28 (complement): a correctly
    ordered ``open < close`` pair is honoured verbatim — the ordering guard does
    NOT fire and does not substitute the defaults — confirming the guard reverts
    only genuinely out-of-order configurations.

    Validates: Requirements 12.5
    """
    open_min, close_min = pair

    candidate = {
        session.ENV_SESSION_OPEN: _hhmm(open_min),
        session.ENV_SESSION_CLOSE: _hhmm(close_min),
        session.ENV_SESSION_TIMEZONE: timezone,
        session.ENV_OPENING_MINUTES: opening_minutes,
        session.ENV_CLOSING_MINUTES: closing_minutes,
        session.ENV_MIDDAY_START: midday_start,
        session.ENV_MIDDAY_END: midday_end,
        session.ENV_EXPIRY_WEEKDAY: expiry_weekday,
    }
    overrides = {name: value for name, value in candidate.items() if value is not None}

    with _session_env(overrides):
        config = resolve_session_config()

    assert isinstance(config, SessionConfig)

    # The supplied, correctly-ordered times are kept verbatim.
    assert config.open_time == _minutes_to_time(open_min)
    assert config.close_time == _minutes_to_time(close_min)

    # The resolved configuration still satisfies the strict ordering invariant.
    assert config.open_time < config.close_time
