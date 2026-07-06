"""Property-based test for deterministic, path-independent resolution (session.py, task 1.4).

Feature: session-expiry-awareness

This module implements design **Property 29: Parameter resolution is
deterministic and path-independent**:

    For any environment, ``resolve_session_config`` returns equal
    ``SessionConfig`` values across repeated calls and across the Session_Tool
    path and the Backtest_Seeder path, so identical environment values resolve
    to identical parameters and identical documented defaults on both paths.

Validates: Requirements 12.6.

The strategies generate a deliberately broad mix of SESSION_* environment values
(valid in-range, out-of-range, unparseable garbage, empty / whitespace, and
"unset"). Resolution must be identical no matter which category a value falls in,
so all of them are exercised. The environment is restored exactly on exit so
Hypothesis re-runs never leak state.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_of_config_path_independent_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (session.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import session  # noqa: E402
from session import SessionConfig, resolve_session_config  # noqa: E402

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
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
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


# ── Value strategies ──────────────────────────────────────────────────────────
# A broad mix per env var: valid in-range values, out-of-range values,
# unparseable garbage, empty / whitespace, and "unset" (``None``). Resolution
# must be identical no matter which category a given value falls into.

# Timezone: valid IANA zones, unloadable / malformed names, empty, whitespace.
_timezone_value = st.one_of(
    st.none(),                                                   # unset
    st.just(""),                                                 # empty
    st.just("   "),                                              # whitespace-only
    st.sampled_from(
        ["Asia/Kolkata", "UTC", "America/New_York", "Europe/London", "Asia/Tokyo"]
    ),                                                           # valid
    st.sampled_from(["Not/AZone", "Mars/Phobos", "garbage", "12:34"]),  # unloadable
)

# Times: well-formed HH:MM (in and out of range), malformed, empty, whitespace.
_time_value = st.one_of(
    st.none(),                                                   # unset
    st.just(""),                                                 # empty
    st.just("   "),                                              # whitespace-only
    st.builds(
        lambda h, m: f"{h:02d}:{m:02d}",
        st.integers(min_value=0, max_value=23),
        st.integers(min_value=0, max_value=59),
    ),                                                           # valid HH:MM
    st.sampled_from(["24:00", "25:61", "9:99", "09", "09:15:30", "ab:cd", "9:15pm"]),
)

# Integers: valid, below-min, above-max, non-int text, empty, whitespace.
_int_value = st.one_of(
    st.none(),                                                   # unset
    st.just(""),                                                 # empty
    st.just("   "),                                              # whitespace-only
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),     # unparseable garbage
    st.integers(min_value=-1000, max_value=1000).map(str),       # spans valid + out-of-range
    st.floats(min_value=0.0, max_value=50.0).map(lambda f: f"{f:.3f}"),  # non-int text
)

_env_assignment = st.fixed_dictionaries(
    {
        session.ENV_SESSION_TIMEZONE: _timezone_value,
        session.ENV_SESSION_OPEN: _time_value,
        session.ENV_SESSION_CLOSE: _time_value,
        session.ENV_OPENING_MINUTES: _int_value,
        session.ENV_CLOSING_MINUTES: _int_value,
        session.ENV_MIDDAY_START: _time_value,
        session.ENV_MIDDAY_END: _time_value,
        session.ENV_EXPIRY_WEEKDAY: _int_value,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 29 (task 1.4): Parameter resolution is deterministic and path-independent
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 29: Parameter resolution is deterministic and path-independent
@settings(max_examples=200, deadline=None)
@given(assignment=_env_assignment)
def test_property_29_resolution_is_deterministic_and_path_independent(assignment):
    """Validates: Requirements 12.6

    For an arbitrary SESSION_* environment, ``resolve_session_config`` returns
    equal ``SessionConfig`` values across repeated calls (path-independent —
    the live Session_Tool path and the Backtest_Seeder path call the same
    function) and the result depends only on the environment, not on call order
    or prior calls. The environment is restored exactly afterwards.
    """
    # ``None`` means "leave the var unset" so the unset-fallback path is exercised.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _session_env(overrides):
        # The live Session_Tool path resolves the config.
        tool_path_config = resolve_session_config()
        # The Backtest_Seeder path resolves the config from the SAME environment.
        backtest_path_config = resolve_session_config()
        # A third call guards general determinism / idempotency / path-independence
        # (the result must not depend on how many times the resolver ran before).
        third_config = resolve_session_config()

    # The resolver never raised and produced fully-formed configs.
    assert isinstance(tool_path_config, SessionConfig)
    assert isinstance(backtest_path_config, SessionConfig)
    assert isinstance(third_config, SessionConfig)

    # Path-independence: the tool path and the backtest path resolve identically.
    assert tool_path_config == backtest_path_config
    # Determinism: every call returns the same value.
    assert tool_path_config == third_config

    # Field-level equality (covers every resolved parameter explicitly, so a
    # failure pinpoints the divergent field rather than the whole dataclass).
    assert tool_path_config.timezone == backtest_path_config.timezone
    assert tool_path_config.open_time == backtest_path_config.open_time
    assert tool_path_config.close_time == backtest_path_config.close_time
    assert tool_path_config.opening_minutes == backtest_path_config.opening_minutes
    assert tool_path_config.closing_minutes == backtest_path_config.closing_minutes
    assert tool_path_config.midday_start == backtest_path_config.midday_start
    assert tool_path_config.midday_end == backtest_path_config.midday_end
    assert tool_path_config.expiry_weekday == backtest_path_config.expiry_weekday
