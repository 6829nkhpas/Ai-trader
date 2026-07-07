# Feature: earnings-event-risk-gate, Property 26: Each parameter falls back to its documented default
"""Property-based test for per-parameter default fallback (events.py, task 1.2).

Feature: earnings-event-risk-gate

This module implements design **Property 26: Each parameter falls back to its
documented default**:

    For any environment in which an event parameter variable is unset, empty,
    unparseable as its expected type, or parseable but outside its valid range,
    ``resolve_event_config`` applies that parameter's documented default value
    while reading every parameter from its own variable, and never raises.

Validates: Requirements 11.1, 11.2, 11.3, 11.4.

Two complementary properties are exercised:

* ``test_property_26_all_bad_fall_back_to_defaults`` — when *every* parameter's
  env var holds a bad value (unset / empty / whitespace / unparseable / out of
  range), every resolved parameter equals its own documented default and the
  resolver never raises. Because the documented window defaults satisfy
  ``through_event_window_days <= imminent_window_days``, the ordering revert
  (AD-8) is a no-op here, so this property stays focused on per-parameter
  fallback.

* ``test_property_26_each_parameter_falls_back_independently`` — when exactly one
  parameter's env var holds a bad value while every *other* parameter holds a
  valid, NON-default value, only the bad parameter reverts to its documented
  default and every other parameter takes the (non-default) value from its own
  variable. This proves the resolver reads "every parameter from its own
  variable" and that a bad value never leaks across parameters. The valid
  non-default window pair is chosen so the ``through_event <= imminent`` ordering
  always holds regardless of which single parameter is targeted, keeping this
  property independent of the ordering revert.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_session_config_default_fallback_properties.py`` and
``tests/test_of_config_default_fallback_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import events  # noqa: E402
from events import (  # noqa: E402
    DEFAULT_EVENT_DEFAULT_HOLDING_HORIZON,
    DEFAULT_EVENT_GATE_ENABLED,
    DEFAULT_EVENT_IMMINENT_WINDOW_DAYS,
    DEFAULT_EVENT_MARKET_TIMEZONE,
    DEFAULT_EVENT_SOURCE_TIMEOUT_S,
    DEFAULT_EVENT_THROUGH_EVENT_WINDOW_DAYS,
    resolve_event_config,
)

# Every EVENT_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs. The optional-string source
# vars (API URL / calendar file) are included so a stray value in the ambient
# environment cannot confound the fallback assertions.
_ALL_EVENT_ENV_VARS = (
    events.ENV_EVENT_GATE_ENABLED,
    events.ENV_EVENT_MARKET_TIMEZONE,
    events.ENV_EVENT_DEFAULT_HOLDING_HORIZON,
    events.ENV_EVENT_IMMINENT_WINDOW_DAYS,
    events.ENV_EVENT_THROUGH_EVENT_WINDOW_DAYS,
    events.ENV_EVENT_SOURCE_TIMEOUT_S,
    events.ENV_EVENT_CALENDAR_API_URL,
    events.ENV_EVENT_CALENDAR_FILE,
)


@contextmanager
def _event_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every EVENT_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_EVENT_ENV_VARS}
    try:
        for name in _ALL_EVENT_ENV_VARS:
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
    st.none(),                                                # unset (R11.2)
    st.just(""),                                              # empty (R11.2)
    st.just("   "),                                           # whitespace-only (R11.2)
    st.text(alphabet="ghjklGHJKL#@/-_", min_size=1, max_size=6),  # unparseable garbage (R11.3)
)

# The master enable flag accepts only the recognized 1/0/true/false/yes/no
# spellings (case-insensitive). Bad values: the shared garbage plus tokens that
# are numeric/word-like but not a recognized boolean spelling (R11.3).
_bool_bad = st.one_of(
    _shared_bad,
    st.sampled_from(["2", "-1", "10", "on", "off", "t", "f", "y", "n", "maybe", "true1"]),
)

# The market timezone accepts an IANA name that ``zoneinfo`` can load. Bad
# values: shared garbage plus names that do not resolve to a loadable zone
# (R11.3/11.4).
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

# The default Holding_Horizon accepts only ``intraday`` / ``multi_session``. Bad
# values: shared garbage plus horizon-like-but-unrecognized tokens (R11.3).
_horizon_bad = st.one_of(
    _shared_bad,
    st.sampled_from(["swing", "positional", "day", "INTRADAY ", "multisession", "scalp"]),
)

# The window-length params (imminent / through-event) accept an int >= 0. Bad
# values: shared garbage, negatives (out of range, R11.4), and non-int float text
# (unparseable as int, R11.3).
_window_bad = st.one_of(
    _shared_bad,
    st.integers(min_value=-1000, max_value=-1).map(str),                 # below min 0 (R11.4)
    st.floats(min_value=0.5, max_value=500.0).map(lambda f: f"{f:.3f}"),  # non-int text (R11.3)
)

# The source retrieval timeout accepts a float strictly > 0. Bad values: shared
# garbage, values <= 0 (out of range, R11.4), and non-finite floats (R11.3/11.4).
_timeout_bad = st.one_of(
    _shared_bad,
    st.just("0"),                                                                       # not > 0 (R11.4)
    st.just("0.0"),                                                                      # not > 0 (R11.4)
    st.floats(min_value=-1e6, max_value=-0.0001, allow_nan=False, allow_infinity=False).map(repr),  # < 0 (R11.4)
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),                                # non-finite (R11.3/11.4)
)

# A complete assignment of a bad value for EVERY parameter at once. Because every
# parameter is bad, every one must fall back to its own documented default; the
# documented window defaults satisfy ``through_event <= imminent`` so the ordering
# revert (AD-8) is a no-op and does not confound this property.
_all_bad_assignment = st.fixed_dictionaries(
    {
        events.ENV_EVENT_GATE_ENABLED: _bool_bad,
        events.ENV_EVENT_MARKET_TIMEZONE: _tz_bad,
        events.ENV_EVENT_DEFAULT_HOLDING_HORIZON: _horizon_bad,
        events.ENV_EVENT_IMMINENT_WINDOW_DAYS: _window_bad,
        events.ENV_EVENT_THROUGH_EVENT_WINDOW_DAYS: _window_bad,
        events.ENV_EVENT_SOURCE_TIMEOUT_S: _timeout_bad,
    }
)


# ── Per-parameter specs for the independence property ─────────────────────────
# For each parameter: its bad-value strategy, a VALID NON-DEFAULT string to set
# in the environment, the resolved attribute name, the documented default value,
# and the expected resolved value when the valid string is applied. The valid
# window pair (imminent=7, through_event=1) keeps ``through_event <= imminent``
# true against either the default or the valid value of the other window, so the
# ordering revert never fires regardless of which single parameter is targeted.
_PARAMS = {
    events.ENV_EVENT_GATE_ENABLED: {
        "bad": _bool_bad,
        # default is True -> the valid NON-default value must resolve to False.
        "valid": "false",
        "attr": "enabled",
        "default": DEFAULT_EVENT_GATE_ENABLED,
        "valid_resolved": False,
    },
    events.ENV_EVENT_MARKET_TIMEZONE: {
        "bad": _tz_bad,
        "valid": "America/New_York",
        "attr": "timezone",
        "default": DEFAULT_EVENT_MARKET_TIMEZONE,
        "valid_resolved": "America/New_York",
    },
    events.ENV_EVENT_DEFAULT_HOLDING_HORIZON: {
        "bad": _horizon_bad,
        # default is "multi_session" -> the valid NON-default value is "intraday".
        "valid": "intraday",
        "attr": "default_holding_horizon",
        "default": DEFAULT_EVENT_DEFAULT_HOLDING_HORIZON,
        "valid_resolved": "intraday",
    },
    events.ENV_EVENT_IMMINENT_WINDOW_DAYS: {
        "bad": _window_bad,
        "valid": "7",
        "attr": "imminent_window_days",
        "default": DEFAULT_EVENT_IMMINENT_WINDOW_DAYS,
        "valid_resolved": 7,
    },
    events.ENV_EVENT_THROUGH_EVENT_WINDOW_DAYS: {
        "bad": _window_bad,
        "valid": "1",
        "attr": "through_event_window_days",
        "default": DEFAULT_EVENT_THROUGH_EVENT_WINDOW_DAYS,
        "valid_resolved": 1,
    },
    events.ENV_EVENT_SOURCE_TIMEOUT_S: {
        "bad": _timeout_bad,
        "valid": "3.5",
        "attr": "source_timeout_s",
        "default": DEFAULT_EVENT_SOURCE_TIMEOUT_S,
        "valid_resolved": 3.5,
    },
}

# Sanity: every chosen valid value is genuinely NON-default, so the independence
# property actually proves the value was read from the variable (not a
# coincidence with the default).
for _env, _spec in _PARAMS.items():
    assert _spec["valid_resolved"] != _spec["default"], (
        f"valid value for {_env} must differ from its default"
    )

# Sanity: the valid window pair preserves the ordering invariant against both the
# other window's valid value and its documented default, so the ordering revert
# never fires during the independence property.
assert _PARAMS[events.ENV_EVENT_THROUGH_EVENT_WINDOW_DAYS]["valid_resolved"] <= \
    _PARAMS[events.ENV_EVENT_IMMINENT_WINDOW_DAYS]["valid_resolved"]
assert _PARAMS[events.ENV_EVENT_THROUGH_EVENT_WINDOW_DAYS]["valid_resolved"] <= \
    DEFAULT_EVENT_IMMINENT_WINDOW_DAYS
assert DEFAULT_EVENT_THROUGH_EVENT_WINDOW_DAYS <= \
    _PARAMS[events.ENV_EVENT_IMMINENT_WINDOW_DAYS]["valid_resolved"]


@st.composite
def _independence_case(draw):
    """Pick one target parameter and a bad value for it."""
    target = draw(st.sampled_from(list(_PARAMS.keys())))
    bad_value = draw(_PARAMS[target]["bad"])
    return target, bad_value


# ─────────────────────────────────────────────────────────────────────────────
# Property 26 (task 1.2): Each parameter falls back to its documented default
# ─────────────────────────────────────────────────────────────────────────────


# Feature: earnings-event-risk-gate, Property 26: Each parameter falls back to its documented default
@settings(max_examples=25, deadline=None)
@given(assignment=_all_bad_assignment)
def test_property_26_all_bad_fall_back_to_defaults(assignment):
    """Feature: earnings-event-risk-gate, Property 26: when every parameter's env
    var holds a bad value (unset / empty / whitespace / unparseable / out of
    range), ``resolve_event_config`` applies every parameter's documented default
    and never raises.

    Validates: Requirements 11.1, 11.2, 11.3, 11.4
    """
    # Only set the vars the assignment marks as present; ``None`` leaves the var
    # unset so the unset-fallback path (R11.2) is exercised too.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _event_env(overrides):
        config = resolve_event_config()

    # The resolver never raised and produced a fully-formed EventConfig.
    assert isinstance(config, events.EventConfig)

    # Every parameter independently fell back to its own documented default.
    assert config.enabled == DEFAULT_EVENT_GATE_ENABLED
    assert config.timezone == DEFAULT_EVENT_MARKET_TIMEZONE
    assert config.default_holding_horizon == DEFAULT_EVENT_DEFAULT_HOLDING_HORIZON
    assert config.imminent_window_days == DEFAULT_EVENT_IMMINENT_WINDOW_DAYS
    assert config.through_event_window_days == DEFAULT_EVENT_THROUGH_EVENT_WINDOW_DAYS
    assert config.source_timeout_s == DEFAULT_EVENT_SOURCE_TIMEOUT_S


# Feature: earnings-event-risk-gate, Property 26: Each parameter falls back to its documented default
@settings(max_examples=25, deadline=None)
@given(case=_independence_case())
def test_property_26_each_parameter_falls_back_independently(case):
    """Feature: earnings-event-risk-gate, Property 26: each parameter is read from
    its OWN variable — a single bad parameter reverts only itself to its
    documented default while every other parameter takes its (valid, non-default)
    value, and the resolver never raises.

    Validates: Requirements 11.1, 11.2, 11.3, 11.4
    """
    target, bad_value = case

    # The target gets the bad value (or stays unset when ``bad_value is None``);
    # every other parameter gets its valid NON-default value.
    overrides = {}
    for env, spec in _PARAMS.items():
        if env == target:
            if bad_value is not None:
                overrides[env] = bad_value
            # else: leave unset to exercise the unset-fallback path (R11.2)
        else:
            overrides[env] = spec["valid"]

    with _event_env(overrides):
        config = resolve_event_config()

    assert isinstance(config, events.EventConfig)

    # The targeted (bad) parameter fell back to its documented default ...
    target_spec = _PARAMS[target]
    assert getattr(config, target_spec["attr"]) == target_spec["default"], (
        f"targeted parameter {target} did not fall back to its documented default"
    )

    # ... while every OTHER parameter took the valid value from its own variable,
    # proving the bad value never leaked across parameters. The valid window pair
    # preserves the ordering invariant, so no window was reverted by AD-8.
    for env, spec in _PARAMS.items():
        if env == target:
            continue
        assert getattr(config, spec["attr"]) == spec["valid_resolved"], (
            f"non-targeted parameter {env} did not take its own variable's value"
        )
