"""Property-based test for opportunity configuration resolution (opportunity.py, task 1.2).

Feature: adaptive-opportunity-engine

This module implements design **Property 17: Configuration resolution is total,
in-range, and defaults on invalid input**:

    For any environment mapping — including unset, empty, whitespace, unparseable,
    or out-of-range raw values for any knob — ``resolve_opportunity_config`` never
    raises and returns a configuration in which every field lies within its
    documented valid range, applying the documented default for each invalid or
    missing value.

Validates: Requirements 11.1, 11.2.

The strategy fuzzes each of the twelve ``OPPORTUNITY_*`` environment variables the
resolver consults across the full degraded input space (unset / empty / whitespace
/ unparseable / out-of-range / valid). For every combination it asserts the
resolved config:

  * never raises (totality, R11.2),
  * equals the parsed valid value when the raw input is valid and the documented
    default otherwise (R11.1, R11.2), checked against an independent reference
    oracle so the property is a genuine check rather than a tautology, and
  * has every field within its documented valid range (R11.1).

The ``os.environ`` isolation context clears every ``OPPORTUNITY_*`` var, applies
the fuzzed overrides, and restores the prior environment exactly on exit so the
fuzzing leaves no residue for sibling tests. The sys.path / import pattern and the
isolation context mirror ``tests/test_attribution_config_robustness_properties.py``
and ``tests/test_debate_config_resolution_properties.py``.
"""

import math
import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (opportunity.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import opportunity  # noqa: E402
from opportunity import (  # noqa: E402
    DEFAULT_HEARTBEAT_CADENCE_SECS,
    DEFAULT_HEARTBEAT_ENABLED,
    DEFAULT_HEARTBEAT_MAX,
    DEFAULT_LOWER_TIERS_ENABLED,
    DEFAULT_PRUNE_KEEP_RECENT_TURNS,
    DEFAULT_PRUNE_MAX_MESSAGES,
    DEFAULT_SESSION_MAX_TURNS,
    DEFAULT_SESSION_MAX_WALL_SECS,
    DEFAULT_SIZE_FACTOR_A_PLUS,
    DEFAULT_SIZE_FACTOR_B_CONTINUATION,
    DEFAULT_SIZE_FACTOR_SCALP,
    DEFAULT_WATCH_CAP,
    OpportunityConfig,
    resolve_opportunity_config,
)

# Every OPPORTUNITY_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_OPPORTUNITY_ENV_VARS = (
    opportunity.ENV_WATCH_CAP,
    opportunity.ENV_SESSION_MAX_TURNS,
    opportunity.ENV_SESSION_MAX_WALL_SECS,
    opportunity.ENV_SIZE_FACTOR_A_PLUS,
    opportunity.ENV_SIZE_FACTOR_B_CONTINUATION,
    opportunity.ENV_SIZE_FACTOR_SCALP,
    opportunity.ENV_LOWER_TIERS_ENABLED,
    opportunity.ENV_HEARTBEAT_ENABLED,
    opportunity.ENV_HEARTBEAT_CADENCE_SECS,
    opportunity.ENV_HEARTBEAT_MAX,
    opportunity.ENV_PRUNE_KEEP_RECENT_TURNS,
    opportunity.ENV_PRUNE_MAX_MESSAGES,
)


@contextmanager
def _opportunity_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every OPPORTUNITY_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_OPPORTUNITY_ENV_VARS}
    try:
        for name in _ALL_OPPORTUNITY_ENV_VARS:
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
# These mirror Requirement 11.1/11.2 ("valid -> parsed value, else documented
# default") WITHOUT calling the module under test, so the property is a genuine
# check rather than a tautology. ``raw is None`` models an UNSET var.


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


def _expected_float(raw, default, low, high):
    """Expected resolved float over the inclusive band [low, high], else default."""
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except (ValueError, TypeError):
        return default
    if not math.isfinite(value):
        return default
    if value < low or value > high:
        return default
    return value


def _expected_secs(raw, default):
    """Expected wall-clock / cadence seconds in the open interval (0.0, inf).

    Resolved on the inclusive band [0.0, inf); a resolved 0.0 sits on the
    exclusive lower bound and reverts to the documented default.
    """
    value = _expected_float(raw, default, 0.0, math.inf)
    if value <= 0.0:
        value = default
    return value


def _expected_factor(raw, default):
    """Expected per-tier Size_Factor in the half-open interval (0.0, 1.0].

    Resolved on the inclusive band [0.0, 1.0]; a resolved 0.0 sits on the
    exclusive lower bound and reverts to the documented default.
    """
    value = _expected_float(raw, default, 0.0, 1.0)
    if value <= 0.0:
        value = default
    return value


def _expected_bool(raw, default):
    """Expected resolved bool: recognized truthy/falsy spelling, else default."""
    if raw is None or not raw.strip():
        return default
    token = raw.strip().lower()
    if token in opportunity._TRUE_TOKENS:
        return True
    if token in opportunity._FALSE_TOKENS:
        return False
    return default


# ── Per-var "arbitrary string (or unset)" strategies ──────────────────────────
# ``None`` models leaving the var UNSET; every other branch is a string assigned
# to the var. Each union deliberately mixes valid in-range values, out-of-range
# values, empty/whitespace, and unparseable garbage so the property is exercised
# across the whole documented input space (R11.1, R11.2).

_unset_or_blank = st.one_of(st.none(), st.just(""), st.just("   "), st.just("\t\n"))

_int_token = st.one_of(
    _unset_or_blank,
    st.integers(min_value=-10_000, max_value=10_000).map(str),  # valid + out-of-range mix
    st.sampled_from(["1", "3", "40", "0", "-5", "1.5", "abc", " 10 ", "+7", "1_000", "0x10", "nan"]),
    st.text(max_size=8),  # arbitrary garbage
)

_float_token = st.one_of(
    _unset_or_blank,
    st.floats(allow_nan=True, allow_infinity=True).map(repr),            # incl. nan/inf -> default
    st.floats(min_value=-5.0, max_value=5.0).map(lambda f: f"{f:.4f}"),  # in/out of band mix
    st.sampled_from(["0.3", "0", "0.0", "-0.1", "0.6", "1.0", "1.5", "300", "3600",
                     "nan", "inf", "-inf", "abc", " 0.5 "]),
    st.text(max_size=8),
)

_bool_token = st.one_of(
    _unset_or_blank,
    st.sampled_from(["1", "0", "true", "false", "yes", "no", "on", "off",
                     "TRUE", "Off", " yes ", "2", "maybe", "tru"]),
    st.text(max_size=8),
)

# A complete (possibly bad) assignment of an arbitrary string / unset to each var.
_assignment = st.fixed_dictionaries(
    {
        opportunity.ENV_WATCH_CAP: _int_token,
        opportunity.ENV_SESSION_MAX_TURNS: _int_token,
        opportunity.ENV_SESSION_MAX_WALL_SECS: _float_token,
        opportunity.ENV_SIZE_FACTOR_A_PLUS: _float_token,
        opportunity.ENV_SIZE_FACTOR_B_CONTINUATION: _float_token,
        opportunity.ENV_SIZE_FACTOR_SCALP: _float_token,
        opportunity.ENV_LOWER_TIERS_ENABLED: _bool_token,
        opportunity.ENV_HEARTBEAT_ENABLED: _bool_token,
        opportunity.ENV_HEARTBEAT_CADENCE_SECS: _float_token,
        opportunity.ENV_HEARTBEAT_MAX: _int_token,
        opportunity.ENV_PRUNE_KEEP_RECENT_TURNS: _int_token,
        opportunity.ENV_PRUNE_MAX_MESSAGES: _int_token,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 17 (task 1.2): Configuration resolution is total, in-range, and
# defaults on invalid input
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 17: For any environment mapping (unset/empty/whitespace/unparseable/out-of-range for any knob), resolve_opportunity_config never raises and returns a configuration in which every field lies within its documented valid range, applying the documented default for each invalid or missing value.
@settings(max_examples=200, deadline=None)
@given(assignment=_assignment)
def test_property_17_configuration_resolution_total_in_range_defaults(assignment):
    """Feature: adaptive-opportunity-engine, Property 17: Configuration resolution
    is total, in-range, and defaults on invalid input — for any string assigned to
    each env var (unset/empty/whitespace/unparseable/out-of-range),
    ``resolve_opportunity_config`` never raises, each field equals the parsed valid
    value when valid and the documented default otherwise, and every field lies
    within its documented valid range.

    Validates: Requirements 11.1, 11.2
    """
    # ``None`` leaves the var UNSET (exercises the unset-fallback path); every
    # other value is assigned as a raw string.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _opportunity_env(overrides):
        # ── Totality: the call must never raise (R11.2). ─────────────────────
        config = resolve_opportunity_config()

    assert isinstance(config, OpportunityConfig)

    raw = assignment  # raw[var] is the string assigned, or None when unset

    # ── Each field equals the parsed valid value, else the documented default ──
    assert config.watch_cap == _expected_int(
        raw[opportunity.ENV_WATCH_CAP], DEFAULT_WATCH_CAP, 1
    )
    assert config.session_max_turns == _expected_int(
        raw[opportunity.ENV_SESSION_MAX_TURNS], DEFAULT_SESSION_MAX_TURNS, 1
    )
    assert config.session_max_wall_secs == _expected_secs(
        raw[opportunity.ENV_SESSION_MAX_WALL_SECS], DEFAULT_SESSION_MAX_WALL_SECS
    )
    assert config.size_factor_a_plus == _expected_factor(
        raw[opportunity.ENV_SIZE_FACTOR_A_PLUS], DEFAULT_SIZE_FACTOR_A_PLUS
    )
    assert config.size_factor_b_continuation == _expected_factor(
        raw[opportunity.ENV_SIZE_FACTOR_B_CONTINUATION], DEFAULT_SIZE_FACTOR_B_CONTINUATION
    )
    assert config.size_factor_scalp == _expected_factor(
        raw[opportunity.ENV_SIZE_FACTOR_SCALP], DEFAULT_SIZE_FACTOR_SCALP
    )
    assert config.lower_tiers_enabled == _expected_bool(
        raw[opportunity.ENV_LOWER_TIERS_ENABLED], DEFAULT_LOWER_TIERS_ENABLED
    )
    assert config.heartbeat_enabled == _expected_bool(
        raw[opportunity.ENV_HEARTBEAT_ENABLED], DEFAULT_HEARTBEAT_ENABLED
    )
    assert config.heartbeat_cadence_secs == _expected_secs(
        raw[opportunity.ENV_HEARTBEAT_CADENCE_SECS], DEFAULT_HEARTBEAT_CADENCE_SECS
    )
    assert config.heartbeat_max == _expected_int(
        raw[opportunity.ENV_HEARTBEAT_MAX], DEFAULT_HEARTBEAT_MAX, 0
    )
    assert config.prune_keep_recent_turns == _expected_int(
        raw[opportunity.ENV_PRUNE_KEEP_RECENT_TURNS], DEFAULT_PRUNE_KEEP_RECENT_TURNS, 1
    )
    assert config.prune_max_messages == _expected_int(
        raw[opportunity.ENV_PRUNE_MAX_MESSAGES], DEFAULT_PRUNE_MAX_MESSAGES, 1
    )

    # ── Every field lies within its documented range (independent of the oracle) ─
    assert isinstance(config.watch_cap, int) and not isinstance(config.watch_cap, bool)
    assert config.watch_cap >= 1

    assert isinstance(config.session_max_turns, int) and not isinstance(
        config.session_max_turns, bool
    )
    assert config.session_max_turns >= 1

    assert isinstance(config.session_max_wall_secs, float)
    assert math.isfinite(config.session_max_wall_secs)
    assert config.session_max_wall_secs > 0.0

    for name, value in (
        ("size_factor_a_plus", config.size_factor_a_plus),
        ("size_factor_b_continuation", config.size_factor_b_continuation),
        ("size_factor_scalp", config.size_factor_scalp),
    ):
        assert isinstance(value, float), f"{name} must be a float"
        assert 0.0 < value <= 1.0, f"{name} {value} out of (0.0, 1.0]"

    assert isinstance(config.lower_tiers_enabled, bool)
    assert isinstance(config.heartbeat_enabled, bool)

    assert isinstance(config.heartbeat_cadence_secs, float)
    assert math.isfinite(config.heartbeat_cadence_secs)
    assert config.heartbeat_cadence_secs > 0.0

    assert isinstance(config.heartbeat_max, int) and not isinstance(config.heartbeat_max, bool)
    assert config.heartbeat_max >= 0

    assert isinstance(config.prune_keep_recent_turns, int) and not isinstance(
        config.prune_keep_recent_turns, bool
    )
    assert config.prune_keep_recent_turns >= 1

    assert isinstance(config.prune_max_messages, int) and not isinstance(
        config.prune_max_messages, bool
    )
    assert config.prune_max_messages >= 1
