# Feature: earnings-event-risk-gate, Property 27: Parameter resolution is deterministic
"""Property-based test for deterministic parameter resolution (events.py, task 1.3).

Feature: earnings-event-risk-gate

This module implements design **Property 27: Parameter resolution is
deterministic**:

    For identical environment-variable values, repeated invocations of
    ``resolve_event_config`` produce identical resolved ``EventConfig`` values
    (and identical documented defaults). The same function is called on every
    tool invocation and on the backtest path, so identical environment values
    resolve to identical parameters no matter the call order or the number of
    prior calls.

Validates: Requirements 11.6.

The strategies generate a deliberately broad mix of EVENT_* environment values
per variable: valid in-range values, out-of-range values, unparseable garbage,
empty / whitespace, and "unset" (``None``). Determinism must hold no matter
which category a value falls into, so every fallback path (unset / empty /
unparseable / out-of-range / ordering-revert) is exercised. The environment is
restored exactly on exit so Hypothesis re-runs never leak state.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_session_config_path_independent_properties.py``.
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
from events import EventConfig, resolve_event_config  # noqa: E402

# Every EVENT_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
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


# ── Value strategies ──────────────────────────────────────────────────────────
# A broad mix per env var: valid in-range values, out-of-range values,
# unparseable garbage, empty / whitespace, and "unset" (``None``). Determinism
# must hold no matter which category a given value falls into.

# Master enable flag: recognized bool spellings, unrecognized text, empty, ws.
_bool_value = st.one_of(
    st.none(),                                                   # unset
    st.just(""),                                                 # empty
    st.just("   "),                                              # whitespace-only
    st.sampled_from(["1", "0", "true", "false", "yes", "no",
                     "TRUE", "False", "Yes", "NO"]),             # valid spellings
    st.sampled_from(["maybe", "enable", "2", "on", "off", "y"]),  # unrecognized
)

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

# Holding_Horizon: recognized values, unrecognized text, empty, whitespace.
_horizon_value = st.one_of(
    st.none(),                                                   # unset
    st.just(""),                                                 # empty
    st.just("   "),                                              # whitespace-only
    st.sampled_from(["intraday", "multi_session"]),              # valid
    st.sampled_from(["swing", "positional", "scalp", "MULTI_SESSION"]),  # unrecognized
)

# Window days: valid (spans in/out of range), negatives, non-int text, empty, ws.
_int_value = st.one_of(
    st.none(),                                                   # unset
    st.just(""),                                                 # empty
    st.just("   "),                                              # whitespace-only
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),     # unparseable garbage
    st.integers(min_value=-1000, max_value=1000).map(str),       # spans valid + negatives
    st.floats(min_value=0.0, max_value=50.0).map(lambda f: f"{f:.3f}"),  # non-int text
)

# Source timeout: valid floats, non-positive (out of range), NaN/inf, garbage.
_timeout_value = st.one_of(
    st.none(),                                                   # unset
    st.just(""),                                                 # empty
    st.just("   "),                                              # whitespace-only
    st.text(alphabet="abcXYZ#@/", min_size=1, max_size=6),       # unparseable garbage
    st.floats(min_value=-100.0, max_value=100.0).map(lambda f: f"{f:.3f}"),  # spans valid + <= 0
    st.sampled_from(["nan", "inf", "-inf", "0", "0.0"]),         # non-finite / boundary
)

# Optional source strings: unset / empty / whitespace collapse to None; any
# non-empty string resolves to its stripped self.
_optional_str_value = st.one_of(
    st.none(),                                                   # unset
    st.just(""),                                                 # empty
    st.just("   "),                                              # whitespace-only
    st.sampled_from(
        ["/tmp/cal.json", "cal.csv", "https://example.test/cal", "  spaced  "]
    ),                                                           # non-empty
)

_env_assignment = st.fixed_dictionaries(
    {
        events.ENV_EVENT_GATE_ENABLED: _bool_value,
        events.ENV_EVENT_MARKET_TIMEZONE: _timezone_value,
        events.ENV_EVENT_DEFAULT_HOLDING_HORIZON: _horizon_value,
        events.ENV_EVENT_IMMINENT_WINDOW_DAYS: _int_value,
        events.ENV_EVENT_THROUGH_EVENT_WINDOW_DAYS: _int_value,
        events.ENV_EVENT_SOURCE_TIMEOUT_S: _timeout_value,
        events.ENV_EVENT_CALENDAR_API_URL: _optional_str_value,
        events.ENV_EVENT_CALENDAR_FILE: _optional_str_value,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 27 (task 1.3): Parameter resolution is deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 27: Parameter resolution is deterministic
@settings(max_examples=200, deadline=None)
@given(assignment=_env_assignment)
def test_property_27_resolution_is_deterministic(assignment):
    """Validates: Requirements 11.6

    For an arbitrary EVENT_* environment, ``resolve_event_config`` returns equal
    ``EventConfig`` values across repeated calls. The live tool path and the
    backtest path call the same function, so identical environment values must
    resolve to identical parameters regardless of call order or the number of
    prior calls. The environment is restored exactly afterwards.
    """
    # ``None`` means "leave the var unset" so the unset-fallback path is exercised.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _event_env(overrides):
        # The live tool path resolves the config.
        first = resolve_event_config()
        # A second invocation from the SAME environment (e.g. a later tool call
        # or the backtest path) must resolve identically.
        second = resolve_event_config()
        # A third call guards general determinism / idempotency (the result must
        # not depend on how many times the resolver ran before).
        third = resolve_event_config()

    # The resolver never raised and produced fully-formed configs.
    assert isinstance(first, EventConfig)
    assert isinstance(second, EventConfig)
    assert isinstance(third, EventConfig)

    # Determinism: every call returns the same value.
    assert first == second
    assert first == third

    # Field-level equality (covers every resolved parameter explicitly, so a
    # failure pinpoints the divergent field rather than the whole dataclass).
    assert first.enabled == second.enabled
    assert first.timezone == second.timezone
    assert first.default_holding_horizon == second.default_holding_horizon
    assert first.imminent_window_days == second.imminent_window_days
    assert first.through_event_window_days == second.through_event_window_days
    assert first.source_timeout_s == second.source_timeout_s
    assert first.calendar_api_url == second.calendar_api_url
    assert first.calendar_file_path == second.calendar_file_path


# Feature: earnings-event-risk-gate, Property 27: Parameter resolution is deterministic
@settings(max_examples=200, deadline=None)
@given(assignment=_env_assignment)
def test_property_27_defaults_are_deterministic_across_reset(assignment):
    """Validates: Requirements 11.6

    Determinism must also hold across a *fresh* environment: resolving under an
    arbitrary environment, tearing that environment down, then resolving again
    from the same values yields the identical ``EventConfig`` (and identical
    documented defaults on every fallback path). This proves resolution depends
    only on the current environment and never on residual process state.
    """
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _event_env(overrides):
        run_a = resolve_event_config()

    # Environment is fully restored here; resolve again in a fresh isolation
    # context built from the exact same overrides.
    with _event_env(overrides):
        run_b = resolve_event_config()

    assert isinstance(run_a, EventConfig)
    assert isinstance(run_b, EventConfig)
    assert run_a == run_b
