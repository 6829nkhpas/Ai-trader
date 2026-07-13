"""Property-based test for attribution configuration robustness (attribution.py, task 1.2).

Feature: feature-attribution-pruning

This module implements design **Property 15: Configuration robustness**:

    For any string assigned to each attribution environment variable (unset,
    empty, whitespace, unparseable, or out-of-range), ``resolve_attribution_config``
    returns a config without raising in which every field equals the parsed valid
    value when the input is valid and the documented default otherwise, and every
    field lies within its documented range.

Validates: Requirements 7.1, 7.2.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_of_config_default_fallback_properties.py`` and
``tests/test_rs_config_default_fallback_properties.py``.
"""

import math
import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (attribution.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import attribution  # noqa: E402
from attribution import (  # noqa: E402
    DEFAULT_CONTRIBUTION_THRESHOLD,
    DEFAULT_DOWN_WEIGHT_FACTOR,
    DEFAULT_GLOBAL_MIN_SCORED,
    DEFAULT_MIN_SAMPLE_DIMENSION,
    DEFAULT_MIN_SAMPLE_VALUE,
    DEFAULT_WEIGHT_MAP_ENABLED,
    AttributionConfig,
    resolve_attribution_config,
)

# Every ATTRIBUTION_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_ATTRIBUTION_ENV_VARS = (
    attribution.ENV_MIN_SAMPLE_DIMENSION,
    attribution.ENV_MIN_SAMPLE_VALUE,
    attribution.ENV_CONTRIBUTION_THRESHOLD,
    attribution.ENV_GLOBAL_MIN_SCORED,
    attribution.ENV_DOWN_WEIGHT_FACTOR,
    attribution.ENV_WEIGHT_MAP_ENABLED,
)


@contextmanager
def _attribution_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every ATTRIBUTION_* var, applies ``overrides``, and restores the
    prior environment exactly on exit (so Hypothesis re-runs never leak state).
    Used instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the
    test body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_ATTRIBUTION_ENV_VARS}
    try:
        for name in _ALL_ATTRIBUTION_ENV_VARS:
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
# These mirror Requirement 7.1/7.2 ("valid -> parsed value, else documented
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


def _expected_factor(raw):
    """Expected down_weight_factor in the half-open interval (0.0, 1.0].

    Resolved on the inclusive band [0.0, 1.0]; a resolved 0.0 sits on the
    exclusive lower bound and reverts to the documented default.
    """
    value = _expected_float(raw, DEFAULT_DOWN_WEIGHT_FACTOR, 0.0, 1.0)
    if value <= 0.0:
        value = DEFAULT_DOWN_WEIGHT_FACTOR
    return value


def _expected_bool(raw, default):
    """Expected resolved bool: recognized truthy/falsy spelling, else default."""
    if raw is None or not raw.strip():
        return default
    token = raw.strip().lower()
    if token in attribution._TRUE_TOKENS:
        return True
    if token in attribution._FALSE_TOKENS:
        return False
    return default


# ── Per-var "arbitrary string (or unset)" strategies ──────────────────────────
# ``None`` models leaving the var UNSET; every other branch is a string assigned
# to the var. Each union deliberately mixes valid in-range values, out-of-range
# values, empty/whitespace, and unparseable garbage so the property is exercised
# across the whole documented input space (R7.1, R7.2).

_unset_or_blank = st.one_of(st.none(), st.just(""), st.just("   "), st.just("\t\n"))

_int_token = st.one_of(
    _unset_or_blank,
    st.integers(min_value=-10_000, max_value=10_000).map(str),  # valid (>=1) + out-of-range (<1)
    st.sampled_from(["1", "30", "0", "-5", "1.5", "abc", " 10 ", "+7", "1_000", "0x10", "nan"]),
    st.text(max_size=8),  # arbitrary garbage
)

_float_token = st.one_of(
    _unset_or_blank,
    st.floats(allow_nan=True, allow_infinity=True).map(repr),                 # incl. nan/inf -> default
    st.floats(min_value=-5.0, max_value=5.0).map(lambda f: f"{f:.4f}"),       # in/out of band mix
    st.sampled_from(["0.15", "0", "0.0", "-0.1", "0.5", "1.0", "1.5", "nan", "inf", "-inf", "abc", " 0.2 "]),
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
        attribution.ENV_MIN_SAMPLE_DIMENSION: _int_token,
        attribution.ENV_MIN_SAMPLE_VALUE: _int_token,
        attribution.ENV_GLOBAL_MIN_SCORED: _int_token,
        attribution.ENV_CONTRIBUTION_THRESHOLD: _float_token,
        attribution.ENV_DOWN_WEIGHT_FACTOR: _float_token,
        attribution.ENV_WEIGHT_MAP_ENABLED: _bool_token,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 15 (task 1.2): Configuration robustness
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 15: For any string assigned to each attribution environment variable (unset, empty, whitespace, unparseable, or out-of-range), resolve_attribution_config returns a config without raising in which every field equals the parsed valid value when the input is valid and the documented default otherwise, and every field lies within its documented range.
@settings(max_examples=100, deadline=None)
@given(assignment=_assignment)
def test_property_15_configuration_robustness(assignment):
    """Feature: feature-attribution-pruning, Property 15: Configuration robustness
    — for any string assigned to each env var (unset/empty/whitespace/unparseable/
    out-of-range), ``resolve_attribution_config`` never raises, each field equals
    the parsed valid value when valid and the documented default otherwise, and
    every field lies within its documented range.

    Validates: Requirements 7.1, 7.2
    """
    # ``None`` leaves the var UNSET (exercises the unset-fallback path); every
    # other value is assigned as a raw string.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _attribution_env(overrides):
        # Never raises (R7.2).
        config = resolve_attribution_config()

    assert isinstance(config, AttributionConfig)

    raw = assignment  # raw[var] is the string assigned, or None when unset

    # ── Each field equals the parsed valid value, else the documented default ──
    assert config.min_sample_dimension == _expected_int(
        raw[attribution.ENV_MIN_SAMPLE_DIMENSION], DEFAULT_MIN_SAMPLE_DIMENSION, 1
    )
    assert config.min_sample_value == _expected_int(
        raw[attribution.ENV_MIN_SAMPLE_VALUE], DEFAULT_MIN_SAMPLE_VALUE, 1
    )
    assert config.global_min_scored == _expected_int(
        raw[attribution.ENV_GLOBAL_MIN_SCORED], DEFAULT_GLOBAL_MIN_SCORED, 1
    )
    assert config.contribution_threshold == _expected_float(
        raw[attribution.ENV_CONTRIBUTION_THRESHOLD], DEFAULT_CONTRIBUTION_THRESHOLD, 0.0, math.inf
    )
    assert config.down_weight_factor == _expected_factor(
        raw[attribution.ENV_DOWN_WEIGHT_FACTOR]
    )
    assert config.weight_map_enabled == _expected_bool(
        raw[attribution.ENV_WEIGHT_MAP_ENABLED], DEFAULT_WEIGHT_MAP_ENABLED
    )

    # ── Every field lies within its documented range (independent of the oracle) ─
    assert isinstance(config.min_sample_dimension, int) and config.min_sample_dimension >= 1
    assert isinstance(config.min_sample_value, int) and config.min_sample_value >= 1
    assert isinstance(config.global_min_scored, int) and config.global_min_scored >= 1

    assert isinstance(config.contribution_threshold, float)
    assert math.isfinite(config.contribution_threshold)
    assert config.contribution_threshold >= 0.0

    assert isinstance(config.down_weight_factor, float)
    assert 0.0 < config.down_weight_factor <= 1.0

    assert isinstance(config.weight_map_enabled, bool)
