"""Property-based tests for options config resolution (options.py, task 1.3).

Feature: options-analytics-engine

This Hypothesis property exercises the deterministic options-config resolver
(:func:`options.resolve_options_config`) across the full env-var input space —
unset, empty/whitespace, unparseable garbage, out-of-range, non-finite, and
valid values — asserting the universal totality/safe-default invariant:

  * Property 15 (8.2) — Configuration resolution is total and defaults safely:
                        ``resolve_options_config`` never raises for any
                        environment state, every field of the resolved config is
                        in its documented valid range, every unset/empty/invalid
                        setting takes its documented default, and the strict
                        ``iv_min_vol < iv_max_vol`` ordering always holds.
"""

import math
import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import options  # noqa: E402
from options import (  # noqa: E402
    DEFAULT_BUILDUP_OI_EPSILON,
    DEFAULT_BUILDUP_PRICE_EPSILON,
    DEFAULT_IV_MAX_ITERATIONS,
    DEFAULT_IV_MAX_VOL,
    DEFAULT_IV_MIN_VOL,
    DEFAULT_IV_TOLERANCE,
    DEFAULT_OI_WALL_MIN_OI,
    DEFAULT_RISK_FREE_RATE,
    ENV_BUILDUP_OI_EPSILON,
    ENV_BUILDUP_PRICE_EPSILON,
    ENV_IV_MAX_ITERATIONS,
    ENV_IV_MAX_VOL,
    ENV_IV_MIN_VOL,
    ENV_IV_TOLERANCE,
    ENV_OI_WALL_MIN_OI,
    ENV_RISK_FREE_RATE,
    OptionsConfig,
    resolve_options_config,
)

# Every OPTIONS_* env var the resolver reads. We clear all of them inside the
# isolation context so only the generated assignment influences the result and
# the remaining parameters fall back to their documented defaults deterministically.
_ALL_OPTIONS_ENV_VARS = (
    ENV_RISK_FREE_RATE,
    ENV_IV_TOLERANCE,
    ENV_IV_MAX_ITERATIONS,
    ENV_IV_MIN_VOL,
    ENV_IV_MAX_VOL,
    ENV_OI_WALL_MIN_OI,
    ENV_BUILDUP_OI_EPSILON,
    ENV_BUILDUP_PRICE_EPSILON,
)


@contextmanager
def _options_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every OPTIONS_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_OPTIONS_ENV_VARS}
    try:
        for name in _ALL_OPTIONS_ENV_VARS:
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


# A single env-var value: ``None`` means "leave the variable unset"; any string
# (valid numeric, out-of-range, non-finite, empty/whitespace, integer-like, or
# unparseable garbage) is set verbatim. This spans the realistic input space the
# resolver must tolerate without raising.
_env_value = st.one_of(
    st.none(),                                                  # unset
    st.just(""),                                                # empty
    st.just("   "),                                             # whitespace-only
    st.floats(allow_nan=True, allow_infinity=True).map(repr),   # numeric (+ nan/inf)
    st.integers(min_value=-1000, max_value=1000).map(str),      # integer-like
    st.floats(min_value=-10.0, max_value=10.0).map(repr),       # in/out-of-range floats
    st.text(max_size=8),                                        # arbitrary garbage
)

# A complete assignment over every options env var at once.
_env_assignments = st.fixed_dictionaries(
    {name: _env_value for name in _ALL_OPTIONS_ENV_VARS}
)


def _assert_field_in_range(config):
    """Every resolved field lies in its documented valid range (design table)."""
    assert config.risk_free_rate == config.risk_free_rate  # not NaN
    assert math.isfinite(config.risk_free_rate)
    assert 0.0 <= config.risk_free_rate <= 1.0

    assert math.isfinite(config.iv_tolerance)
    assert 0.0 < config.iv_tolerance <= 1.0

    assert isinstance(config.iv_max_iterations, int)
    assert config.iv_max_iterations >= 1

    assert math.isfinite(config.iv_min_vol)
    assert config.iv_min_vol >= 0.0

    assert math.isfinite(config.iv_max_vol)
    assert config.iv_max_vol > 0.0

    assert math.isfinite(config.oi_wall_min_oi)
    assert config.oi_wall_min_oi >= 0.0

    assert math.isfinite(config.buildup_oi_epsilon)
    assert config.buildup_oi_epsilon >= 0.0

    assert math.isfinite(config.buildup_price_epsilon)
    assert config.buildup_price_epsilon >= 0.0


# Maps each env var to (resolved-field-name, documented-default). An unset /
# empty / whitespace-only value MUST resolve that field to exactly its default.
_DEFAULTS_BY_ENV = {
    ENV_RISK_FREE_RATE: ("risk_free_rate", DEFAULT_RISK_FREE_RATE),
    ENV_IV_TOLERANCE: ("iv_tolerance", DEFAULT_IV_TOLERANCE),
    ENV_IV_MAX_ITERATIONS: ("iv_max_iterations", DEFAULT_IV_MAX_ITERATIONS),
    ENV_OI_WALL_MIN_OI: ("oi_wall_min_oi", DEFAULT_OI_WALL_MIN_OI),
    ENV_BUILDUP_OI_EPSILON: ("buildup_oi_epsilon", DEFAULT_BUILDUP_OI_EPSILON),
    ENV_BUILDUP_PRICE_EPSILON: ("buildup_price_epsilon", DEFAULT_BUILDUP_PRICE_EPSILON),
}


@settings(max_examples=100)
@given(assignment=_env_assignments)
def test_property_15_config_resolution_is_total_and_defaults_safely(assignment):
    # Feature: options-analytics-engine, Property 15: Configuration resolution is total and defaults safely
    """Feature: options-analytics-engine, Property 15: Configuration resolution
    is total and defaults safely — for ANY environment state (unset, empty,
    unparseable, out-of-range, or non-finite values), ``resolve_options_config``
    returns without raising a configuration in which every field is valid (in its
    documented range), every unset/empty/invalid setting takes its documented
    default, and the strict ``iv_min_vol < iv_max_vol`` ordering holds.

    Validates: Requirements 8.2
    """
    # Only set the vars the assignment marks as present; the rest stay unset.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _options_env(overrides):
        # Totality: the resolver NEVER raises for any environment state.
        config = resolve_options_config()

    # It always produces a well-formed OptionsConfig.
    assert isinstance(config, OptionsConfig)

    # Every field is valid / in its documented range.
    _assert_field_in_range(config)

    # The strict volatility-bound ordering always holds.
    assert config.iv_min_vol < config.iv_max_vol

    # Every unset / empty / whitespace-only setting takes EXACTLY its documented
    # default. (These are the unambiguously-invalid inputs whose resolved value
    # is fully determined by the spec, independent of the cross-field ordering
    # rule which couples the two volatility bounds.)
    for env_name, (field_name, default) in _DEFAULTS_BY_ENV.items():
        raw = overrides.get(env_name)
        if raw is None or not raw.strip():
            assert getattr(config, field_name) == default
