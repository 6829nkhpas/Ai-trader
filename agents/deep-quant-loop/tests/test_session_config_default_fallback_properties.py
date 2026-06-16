# Feature: session-expiry-awareness, Property 27: For any environment in which a session parameter variable is unset, empty, unparseable as its expected type, or parseable but outside its valid range, resolve_session_config applies that parameter's documented default value while reading every parameter from its own variable, and never raises.
"""Property-based test for per-parameter default fallback (session.py, task 1.2).

Feature: session-expiry-awareness

This module implements design **Property 27: Each parameter falls back to its
documented default**:

    For any environment in which a session parameter variable is unset, empty,
    unparseable as its expected type, or parseable but outside its valid range,
    ``resolve_session_config`` applies that parameter's documented default value
    while reading every parameter from its own variable, and never raises.

Validates: Requirements 12.1, 12.2, 12.3, 12.4.

Two complementary properties are exercised:

* ``test_property_27_all_bad_fall_back_to_defaults`` — when *every* parameter's
  env var holds a bad value (unset / empty / whitespace / unparseable / out of
  range), every resolved parameter equals its own documented default and the
  resolver never raises. (Because the defaults satisfy ``open < close``, the
  ordering revert of R12.5 — covered separately by Property 28 — is a no-op
  here, so this property stays focused on per-parameter fallback.)

* ``test_property_27_each_parameter_falls_back_independently`` — when exactly one
  parameter's env var holds a bad value while every *other* parameter holds a
  valid, NON-default value, only the bad parameter reverts to its documented
  default and every other parameter takes the (non-default) value from its own
  variable. This proves the resolver reads "every parameter from its own
  variable" and that a bad value never leaks across parameters. The valid
  non-default open/close pair is chosen so the ``open < close`` ordering always
  holds regardless of which single parameter is targeted, keeping this property
  independent of the R12.5 ordering revert.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_rs_config_default_fallback_properties.py`` and
``tests/test_of_config_default_fallback_properties.py``.
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
    DEFAULT_CLOSING_MINUTES,
    DEFAULT_EXPIRY_WEEKDAY,
    DEFAULT_MIDDAY_END,
    DEFAULT_MIDDAY_START,
    DEFAULT_OPENING_MINUTES,
    DEFAULT_SESSION_CLOSE,
    DEFAULT_SESSION_OPEN,
    DEFAULT_SESSION_TIMEZONE,
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


# ── "Bad value" strategies (each should force the documented default) ─────────
# Shared categories that are bad for ANY parameter type: empty, whitespace-only,
# and unparseable non-numeric garbage. ``None`` means "leave the var unset".
_shared_bad = st.one_of(
    st.none(),                                                # unset (R12.2)
    st.just(""),                                              # empty (R12.2)
    st.just("   "),                                           # whitespace-only (R12.2)
    st.text(alphabet="abcXYZ#@-_", min_size=1, max_size=6),   # unparseable garbage (R12.3)
)

# Time-of-day params (open, close, midday_start, midday_end) accept only a valid
# ``HH:MM`` 24h string. Bad values: the shared garbage plus strings that fail to
# parse as HH:MM or fall outside the valid 24h range (R12.3, R12.4).
_time_bad = st.one_of(
    _shared_bad,
    st.sampled_from(
        [
            "24:00",       # hour out of range (R12.4)
            "25:30",       # hour out of range (R12.4)
            "12:60",       # minute out of range (R12.4)
            "23:99",       # minute out of range (R12.4)
            "9",           # not HH:MM (R12.3)
            "0930",        # not HH:MM (R12.3)
            "12:30:00",    # seconds not accepted (R12.3)
            "-1:00",       # not digit hour (R12.3)
            ":",           # empty components (R12.3)
            "ab:cd",       # non-numeric (R12.3)
            "9:5a",        # non-numeric minute (R12.3)
        ]
    ),
)

# Window-length params (opening_minutes, closing_minutes) accept an int >= 0.
# Bad values: shared garbage, negatives (out of range, R12.4), and non-int
# float text (unparseable as int, R12.3).
_minutes_bad = st.one_of(
    _shared_bad,
    st.integers(min_value=-1000, max_value=-1).map(str),                # below min 0 (R12.4)
    st.floats(min_value=0.5, max_value=500.0).map(lambda f: f"{f:.3f}"),  # non-int text (R12.3)
)

# The expiry weekday accepts an int in [0, 6]. Bad values: shared garbage,
# out-of-range ints (>= 7 or < 0), and non-int float text.
_weekday_bad = st.one_of(
    _shared_bad,
    st.integers(min_value=7, max_value=1000).map(str),                 # > 6 (R12.4)
    st.integers(min_value=-1000, max_value=-1).map(str),               # < 0 (R12.4)
    st.floats(min_value=0.5, max_value=6.0).map(lambda f: f"{f:.3f}"),  # non-int text (R12.3)
)

# The timezone accepts an IANA name that ``zoneinfo`` can load. Bad values:
# shared garbage plus names that do not resolve to a loadable zone (R12.3/12.4).
_tz_bad = st.one_of(
    _shared_bad,
    st.sampled_from(
        [
            "Not/AZone",
            "Mars/Olympus",
            "Definitely/NotReal",
            "abc123",
            "12345",
        ]
    ),
)

# A complete assignment of a bad value for EVERY parameter at once. Because every
# parameter is bad, every one must fall back to its own documented default; the
# documented open/close defaults satisfy ``open < close`` so the ordering revert
# (Property 28 / R12.5) is a no-op and does not confound this property.
_all_bad_assignment = st.fixed_dictionaries(
    {
        session.ENV_SESSION_TIMEZONE: _tz_bad,
        session.ENV_SESSION_OPEN: _time_bad,
        session.ENV_SESSION_CLOSE: _time_bad,
        session.ENV_OPENING_MINUTES: _minutes_bad,
        session.ENV_CLOSING_MINUTES: _minutes_bad,
        session.ENV_MIDDAY_START: _time_bad,
        session.ENV_MIDDAY_END: _time_bad,
        session.ENV_EXPIRY_WEEKDAY: _weekday_bad,
    }
)


# ── Per-parameter specs for the independence property ─────────────────────────
# For each parameter: its bad-value strategy, a VALID NON-DEFAULT string to set
# in the environment, the resolved attribute name, the documented default value,
# and the expected resolved value when the valid string is applied. The valid
# open/close pair (09:00 / 15:00) keeps ``open < close`` true against either the
# default or the valid value of the other, so the ordering revert never fires
# regardless of which single parameter is targeted.
_PARAMS = {
    session.ENV_SESSION_TIMEZONE: {
        "bad": _tz_bad,
        "valid": "America/New_York",
        "attr": "timezone",
        "default": DEFAULT_SESSION_TIMEZONE,
        "valid_resolved": "America/New_York",
    },
    session.ENV_SESSION_OPEN: {
        "bad": _time_bad,
        "valid": "09:00",
        "attr": "open_time",
        "default": DEFAULT_SESSION_OPEN,
        "valid_resolved": dtime(9, 0),
    },
    session.ENV_SESSION_CLOSE: {
        "bad": _time_bad,
        "valid": "15:00",
        "attr": "close_time",
        "default": DEFAULT_SESSION_CLOSE,
        "valid_resolved": dtime(15, 0),
    },
    session.ENV_OPENING_MINUTES: {
        "bad": _minutes_bad,
        "valid": "20",
        "attr": "opening_minutes",
        "default": DEFAULT_OPENING_MINUTES,
        "valid_resolved": 20,
    },
    session.ENV_CLOSING_MINUTES: {
        "bad": _minutes_bad,
        "valid": "25",
        "attr": "closing_minutes",
        "default": DEFAULT_CLOSING_MINUTES,
        "valid_resolved": 25,
    },
    session.ENV_MIDDAY_START: {
        "bad": _time_bad,
        "valid": "11:00",
        "attr": "midday_start",
        "default": DEFAULT_MIDDAY_START,
        "valid_resolved": dtime(11, 0),
    },
    session.ENV_MIDDAY_END: {
        "bad": _time_bad,
        "valid": "13:00",
        "attr": "midday_end",
        "default": DEFAULT_MIDDAY_END,
        "valid_resolved": dtime(13, 0),
    },
    session.ENV_EXPIRY_WEEKDAY: {
        "bad": _weekday_bad,
        "valid": "2",
        "attr": "expiry_weekday",
        "default": DEFAULT_EXPIRY_WEEKDAY,
        "valid_resolved": 2,
    },
}

# Sanity: every chosen valid value is genuinely NON-default, so the independence
# property actually proves the value was read from the variable (not a
# coincidence with the default).
for _env, _spec in _PARAMS.items():
    assert _spec["valid_resolved"] != _spec["default"], (
        f"valid value for {_env} must differ from its default"
    )


@st.composite
def _independence_case(draw):
    """Pick one target parameter and a bad value for it."""
    target = draw(st.sampled_from(list(_PARAMS.keys())))
    bad_value = draw(_PARAMS[target]["bad"])
    return target, bad_value


# ─────────────────────────────────────────────────────────────────────────────
# Property 27 (task 1.2): Each parameter falls back to its documented default
# ─────────────────────────────────────────────────────────────────────────────


# Feature: session-expiry-awareness, Property 27: Each parameter falls back to its documented default
@settings(max_examples=200, deadline=None)
@given(assignment=_all_bad_assignment)
def test_property_27_all_bad_fall_back_to_defaults(assignment):
    """Feature: session-expiry-awareness, Property 27: when every parameter's env
    var holds a bad value (unset / empty / whitespace / unparseable / out of
    range), ``resolve_session_config`` applies every parameter's documented
    default and never raises.

    Validates: Requirements 12.1, 12.2, 12.3, 12.4
    """
    # Only set the vars the assignment marks as present; ``None`` leaves the var
    # unset so the unset-fallback path (R12.2) is exercised too.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _session_env(overrides):
        config = resolve_session_config()

    # The resolver never raised and produced a fully-formed SessionConfig.
    assert isinstance(config, session.SessionConfig)

    # Every parameter independently fell back to its own documented default.
    assert config.timezone == DEFAULT_SESSION_TIMEZONE
    assert config.open_time == DEFAULT_SESSION_OPEN
    assert config.close_time == DEFAULT_SESSION_CLOSE
    assert config.opening_minutes == DEFAULT_OPENING_MINUTES
    assert config.closing_minutes == DEFAULT_CLOSING_MINUTES
    assert config.midday_start == DEFAULT_MIDDAY_START
    assert config.midday_end == DEFAULT_MIDDAY_END
    assert config.expiry_weekday == DEFAULT_EXPIRY_WEEKDAY


# Feature: session-expiry-awareness, Property 27: Each parameter falls back to its documented default
@settings(max_examples=200, deadline=None)
@given(case=_independence_case())
def test_property_27_each_parameter_falls_back_independently(case):
    """Feature: session-expiry-awareness, Property 27: each parameter is read from
    its OWN variable — a single bad parameter reverts only itself to its
    documented default while every other parameter takes its (valid, non-default)
    value, and the resolver never raises.

    Validates: Requirements 12.1, 12.2, 12.3, 12.4
    """
    target, bad_value = case

    # The target gets the bad value (or stays unset when ``bad_value is None``);
    # every other parameter gets its valid NON-default value.
    overrides = {}
    for env, spec in _PARAMS.items():
        if env == target:
            if bad_value is not None:
                overrides[env] = bad_value
            # else: leave unset to exercise the unset-fallback path (R12.2)
        else:
            overrides[env] = spec["valid"]

    with _session_env(overrides):
        config = resolve_session_config()

    assert isinstance(config, session.SessionConfig)

    # The targeted (bad) parameter fell back to its documented default ...
    target_spec = _PARAMS[target]
    assert getattr(config, target_spec["attr"]) == target_spec["default"], (
        f"targeted parameter {target} did not fall back to its documented default"
    )

    # ... while every OTHER parameter took the valid value from its own variable,
    # proving the bad value never leaked across parameters.
    for env, spec in _PARAMS.items():
        if env == target:
            continue
        assert getattr(config, spec["attr"]) == spec["valid_resolved"], (
            f"non-targeted parameter {env} did not take its own variable's value"
        )
