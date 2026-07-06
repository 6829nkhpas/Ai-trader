"""Unit tests for options configuration resolution (options.py, task 1.4).

Feature: options-analytics-engine

These plain ``pytest`` unit tests exercise ``options.resolve_options_config`` —
the single, deterministic, environment-driven resolver for the Options Analytics
Engine's parameter set. Task 1.4 covers Requirement 8.1: each environment
variable set to a valid value is honored; each unset variable uses its documented
default; and mis-ordered volatility bounds (``iv_min_vol >= iv_max_vol``) revert
**both** bounds to their documented defaults.

Environment isolation uses the ``monkeypatch`` fixture (per-test scoped), which
fully restores the prior environment after each test. Mirrors the convention in
``test_regime_config.py`` (the catalogue of (env var, attribute, default) specs
and the per-parameter fallback coverage) while using plain example-based tests
rather than Hypothesis, as the task specifies.
"""

import os
import sys

import pytest

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

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

# ─────────────────────────────────────────────────────────────────────────────
# Parameter catalogue: (env var, OptionsConfig attribute, default, valid raw)
# The "valid raw" string is in-range and parseable, so it MUST be honored.
# ─────────────────────────────────────────────────────────────────────────────

# Float parameters. Each "valid raw" lies strictly inside the documented range
# and differs from the default so an honored value is distinguishable.
FLOAT_SPECS = [
    (ENV_RISK_FREE_RATE, "risk_free_rate", DEFAULT_RISK_FREE_RATE, "0.04", 0.04),
    (ENV_IV_TOLERANCE, "iv_tolerance", DEFAULT_IV_TOLERANCE, "0.001", 0.001),
    (ENV_OI_WALL_MIN_OI, "oi_wall_min_oi", DEFAULT_OI_WALL_MIN_OI, "1500", 1500.0),
    (ENV_BUILDUP_OI_EPSILON, "buildup_oi_epsilon", DEFAULT_BUILDUP_OI_EPSILON, "25", 25.0),
    (ENV_BUILDUP_PRICE_EPSILON, "buildup_price_epsilon", DEFAULT_BUILDUP_PRICE_EPSILON, "0.5", 0.5),
]

# Integer parameter.
INT_SPECS = [
    (ENV_IV_MAX_ITERATIONS, "iv_max_iterations", DEFAULT_IV_MAX_ITERATIONS, "250", 250),
]

# Volatility-bound parameters are coupled (iv_min_vol < iv_max_vol) so they are
# honored together; tested separately from the independent params above.
VOL_SPECS = [
    (ENV_IV_MIN_VOL, "iv_min_vol", DEFAULT_IV_MIN_VOL),
    (ENV_IV_MAX_VOL, "iv_max_vol", DEFAULT_IV_MAX_VOL),
]

ALL_ENV_NAMES = (
    [s[0] for s in FLOAT_SPECS]
    + [s[0] for s in INT_SPECS]
    + [s[0] for s in VOL_SPECS]
)


def _clear_all(monkeypatch):
    """Remove every options config env var so each test starts from a clean slate."""
    for name in ALL_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


# ─────────────────────────────────────────────────────────────────────────────
# All-unset → every field is its documented default (Requirement 8.1)
# ─────────────────────────────────────────────────────────────────────────────

def test_all_unset_uses_documented_defaults(monkeypatch):
    """With no options env vars set, every field takes its documented default."""
    _clear_all(monkeypatch)

    config = resolve_options_config()

    assert isinstance(config, OptionsConfig)
    assert config.risk_free_rate == DEFAULT_RISK_FREE_RATE
    assert config.iv_tolerance == DEFAULT_IV_TOLERANCE
    assert config.iv_max_iterations == DEFAULT_IV_MAX_ITERATIONS
    assert config.iv_min_vol == DEFAULT_IV_MIN_VOL
    assert config.iv_max_vol == DEFAULT_IV_MAX_VOL
    assert config.oi_wall_min_oi == DEFAULT_OI_WALL_MIN_OI
    assert config.buildup_oi_epsilon == DEFAULT_BUILDUP_OI_EPSILON
    assert config.buildup_price_epsilon == DEFAULT_BUILDUP_PRICE_EPSILON


# ─────────────────────────────────────────────────────────────────────────────
# Each valid value is honored (Requirement 8.1)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "env_name, attr, default, raw, expected",
    FLOAT_SPECS + INT_SPECS,
    ids=[s[0] for s in FLOAT_SPECS + INT_SPECS],
)
def test_valid_value_is_honored(monkeypatch, env_name, attr, default, raw, expected):
    """A valid, in-range value set on its env var is honored (not the default)."""
    _clear_all(monkeypatch)
    monkeypatch.setenv(env_name, raw)

    config = resolve_options_config()
    resolved = getattr(config, attr)

    assert resolved == expected
    assert type(resolved) is type(default)
    # The valid raw differs from the default, so "honored" is observable.
    assert resolved != default


@pytest.mark.parametrize(
    "env_name, attr, default, raw, expected",
    FLOAT_SPECS + INT_SPECS,
    ids=[s[0] for s in FLOAT_SPECS + INT_SPECS],
)
def test_only_targeted_field_changes(monkeypatch, env_name, attr, default, raw, expected):
    """Setting one var honors that field while every other field stays default."""
    _clear_all(monkeypatch)
    monkeypatch.setenv(env_name, raw)

    config = resolve_options_config()

    assert getattr(config, attr) == expected
    # All independent (non-volatility) fields other than the target remain default.
    others = {a for _, a, *_ in FLOAT_SPECS + INT_SPECS} - {attr}
    defaults_by_attr = {a: d for _, a, d, *_ in FLOAT_SPECS + INT_SPECS}
    for other in others:
        assert getattr(config, other) == defaults_by_attr[other]
    # The (untouched) volatility bounds stay at their defaults too.
    assert config.iv_min_vol == DEFAULT_IV_MIN_VOL
    assert config.iv_max_vol == DEFAULT_IV_MAX_VOL


def test_valid_volatility_bounds_are_honored(monkeypatch):
    """Properly ordered, in-range volatility bounds (min < max) are both honored."""
    _clear_all(monkeypatch)
    monkeypatch.setenv(ENV_IV_MIN_VOL, "0.02")
    monkeypatch.setenv(ENV_IV_MAX_VOL, "3.0")

    config = resolve_options_config()

    assert config.iv_min_vol == 0.02
    assert config.iv_max_vol == 3.0


def test_all_valid_values_honored_together(monkeypatch):
    """Setting every var to a valid value honors all of them simultaneously."""
    _clear_all(monkeypatch)
    for env_name, _attr, _default, raw, _expected in FLOAT_SPECS + INT_SPECS:
        monkeypatch.setenv(env_name, raw)
    monkeypatch.setenv(ENV_IV_MIN_VOL, "0.02")
    monkeypatch.setenv(ENV_IV_MAX_VOL, "3.0")

    config = resolve_options_config()

    assert config.risk_free_rate == 0.04
    assert config.iv_tolerance == 0.001
    assert config.iv_max_iterations == 250
    assert config.iv_min_vol == 0.02
    assert config.iv_max_vol == 3.0
    assert config.oi_wall_min_oi == 1500.0
    assert config.buildup_oi_epsilon == 25.0
    assert config.buildup_price_epsilon == 0.5


# ─────────────────────────────────────────────────────────────────────────────
# Mis-ordered volatility bounds revert BOTH to defaults (Requirement 8.1)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "min_raw, max_raw",
    [
        ("3.0", "1.0"),    # strictly greater: min > max
        ("2.0", "2.0"),    # equal: min == max (>= triggers revert)
        ("0.5", "0.1"),    # both in range but inverted
    ],
)
def test_misordered_volatility_bounds_revert_both_to_defaults(monkeypatch, min_raw, max_raw):
    """When iv_min_vol >= iv_max_vol, BOTH bounds revert to their defaults."""
    _clear_all(monkeypatch)
    monkeypatch.setenv(ENV_IV_MIN_VOL, min_raw)
    monkeypatch.setenv(ENV_IV_MAX_VOL, max_raw)

    config = resolve_options_config()

    assert config.iv_min_vol == DEFAULT_IV_MIN_VOL
    assert config.iv_max_vol == DEFAULT_IV_MAX_VOL


def test_resolution_is_deterministic(monkeypatch):
    """Identical environment yields identical resolved configuration (R8.3)."""
    _clear_all(monkeypatch)
    monkeypatch.setenv(ENV_RISK_FREE_RATE, "0.04")
    monkeypatch.setenv(ENV_IV_MIN_VOL, "0.02")
    monkeypatch.setenv(ENV_IV_MAX_VOL, "3.0")

    assert resolve_options_config() == resolve_options_config()
