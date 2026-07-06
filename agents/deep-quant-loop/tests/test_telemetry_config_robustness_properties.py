"""Property-based test for telemetry configuration robustness (telemetry.py, task 1.2).

Feature: session-telemetry

This module implements design **Property 19: Configuration resolution is robust
and falls back to defaults**:

    For any environment-variable value — including unset, empty, whitespace,
    unparseable, and out-of-range strings — ``resolve_telemetry_config`` returns
    the valid supplied value when it is parseable and in range, and otherwise the
    documented default, and never raises.

Validates: Requirements 9.1, 9.2.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_attribution_config_robustness_properties.py``.
"""

import math
import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import telemetry  # noqa: E402
from telemetry import (  # noqa: E402
    DEFAULT_INCOMPLETE_HORIZON_SECONDS,
    DEFAULT_WEAK_PRIOR_MIN_SESSIONS,
    TelemetryConfig,
    resolve_telemetry_config,
)

# Every TELEMETRY_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_TELEMETRY_ENV_VARS = (
    telemetry.ENV_TELEMETRY_DB_PATH,
    telemetry.ENV_WEAK_PRIOR_MIN_SESSIONS,
    telemetry.ENV_INCOMPLETE_HORIZON,
)


@contextmanager
def _telemetry_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every TELEMETRY_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_TELEMETRY_ENV_VARS}
    try:
        for name in _ALL_TELEMETRY_ENV_VARS:
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


# ── Independent reference oracles for the documented per-parameter semantics ──
# These mirror Requirement 9.1/9.2 ("valid -> supplied value, else documented
# default") WITHOUT calling the module under test, so the property is a genuine
# check rather than a tautology. ``raw is None`` models an UNSET var.


def _expected_str(raw, default):
    """Expected resolved str: stripped value when non-blank, else default."""
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _expected_int(raw, default, low):
    """Expected resolved int: parsed value when valid (>= low), else default."""
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except (ValueError, TypeError):
        return default
    if value < low:
        return default
    return value


def _expected_horizon(raw):
    """Expected incomplete_horizon_seconds in the open interval (0.0, inf).

    Resolved on the inclusive band [0.0, inf]; a resolved 0.0 (or any non-positive
    value) sits on the exclusive lower bound and reverts to the documented default.
    """
    if raw is None or not raw.strip():
        return DEFAULT_INCOMPLETE_HORIZON_SECONDS
    try:
        value = float(raw.strip())
    except (ValueError, TypeError):
        return DEFAULT_INCOMPLETE_HORIZON_SECONDS
    if not math.isfinite(value):
        return DEFAULT_INCOMPLETE_HORIZON_SECONDS
    if value < 0.0:  # below inclusive band low -> default
        return DEFAULT_INCOMPLETE_HORIZON_SECONDS
    if value <= 0.0:  # exclusive lower bound -> default
        return DEFAULT_INCOMPLETE_HORIZON_SECONDS
    return value


# ── Per-var "arbitrary string (or unset)" strategies ──────────────────────────
# ``None`` models leaving the var UNSET; every other branch is a string assigned
# to the var. Each union deliberately mixes valid in-range values, out-of-range
# values, empty/whitespace, and unparseable garbage so the property is exercised
# across the whole documented input space (R9.1, R9.2).

_unset_or_blank = st.one_of(st.none(), st.just(""), st.just("   "), st.just("\t\n"))

_int_token = st.one_of(
    _unset_or_blank,
    st.integers(min_value=-10_000, max_value=10_000).map(str),  # valid (>=1) + out-of-range (<1)
    st.sampled_from(["1", "20", "0", "-5", "1.5", "abc", " 30 ", "+7", "1_000", "0x10", "nan"]),
    st.text(max_size=8),  # arbitrary garbage
)

_horizon_token = st.one_of(
    _unset_or_blank,
    st.floats(allow_nan=True, allow_infinity=True).map(repr),                 # incl. nan/inf -> default
    st.floats(min_value=-100.0, max_value=100_000.0).map(lambda f: f"{f:.4f}"),  # in/out of band mix
    st.sampled_from(["86400", "0", "0.0", "-0.1", "1.0", "3600", "nan", "inf", "-inf", "abc", " 7200 "]),
    st.text(max_size=8),
)

_str_token = st.one_of(
    _unset_or_blank,
    st.sampled_from(["/tmp/t.db", "telemetry.db", " ./x.db ", "C:/data/t.sqlite", "relative/path.db"]),
    st.text(max_size=16),
)

# A complete (possibly bad) assignment of an arbitrary string / unset to each var.
_assignment = st.fixed_dictionaries(
    {
        telemetry.ENV_TELEMETRY_DB_PATH: _str_token,
        telemetry.ENV_WEAK_PRIOR_MIN_SESSIONS: _int_token,
        telemetry.ENV_INCOMPLETE_HORIZON: _horizon_token,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 19 (task 1.2): Configuration resolution is robust and falls back to defaults
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 19: Configuration resolution is robust and falls back to defaults
@settings(max_examples=100, deadline=None)
@given(assignment=_assignment)
def test_property_19_configuration_resolution_robust(assignment):
    """Feature: session-telemetry, Property 19: Configuration resolution is robust
    and falls back to defaults — for any env-var value (unset/empty/whitespace/
    unparseable/out-of-range), ``resolve_telemetry_config`` never raises, returns
    the valid supplied value when parseable and in range and the documented
    default otherwise, and every field lies within its documented range.

    Validates: Requirements 9.1, 9.2
    """
    # ``None`` leaves the var UNSET (exercises the unset-fallback path); every
    # other value is assigned as a raw string.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _telemetry_env(overrides):
        # Never raises (R9.2).
        config = resolve_telemetry_config()

    assert isinstance(config, TelemetryConfig)

    raw = assignment  # raw[var] is the string assigned, or None when unset

    # ── Each field equals the valid supplied value, else the documented default ─
    assert config.db_path == _expected_str(
        raw[telemetry.ENV_TELEMETRY_DB_PATH], telemetry._DEFAULT_DB
    )
    assert config.weak_prior_min_sessions == _expected_int(
        raw[telemetry.ENV_WEAK_PRIOR_MIN_SESSIONS], DEFAULT_WEAK_PRIOR_MIN_SESSIONS, 1
    )
    assert config.incomplete_horizon_seconds == _expected_horizon(
        raw[telemetry.ENV_INCOMPLETE_HORIZON]
    )

    # ── Every field lies within its documented range (independent of the oracle) ─
    assert isinstance(config.db_path, str) and config.db_path.strip() != ""

    assert isinstance(config.weak_prior_min_sessions, int)
    assert config.weak_prior_min_sessions >= 1

    # The value must be a finite, strictly-positive real. The documented default
    # is written as the integer expression ``24 * 3600`` in the design, so a
    # defaulted value may be an ``int`` while a parsed override is a ``float`` —
    # Property 19 constrains the value, not the Python type.
    assert isinstance(config.incomplete_horizon_seconds, (int, float))
    assert not isinstance(config.incomplete_horizon_seconds, bool)
    assert math.isfinite(config.incomplete_horizon_seconds)
    assert config.incomplete_horizon_seconds > 0.0
