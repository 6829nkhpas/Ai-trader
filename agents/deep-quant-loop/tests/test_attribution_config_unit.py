"""Unit/example tests: attribution config env-var resolution (attribution.py, task 1.3).

Feature: feature-attribution-pruning

Requirement 7.1: each tunable parameter is read from its OWN documented
environment variable with a documented valid range/type.
Requirement 7.2: when a parameter's env var is unset / empty / unparseable /
out of range, ``resolve_attribution_config`` falls back to the documented
default (and never raises).

These are concrete EXAMPLE-based unit tests (NOT property tests). For every
parameter we assert four cases — unset, a valid in-range value (which parses
THROUGH untouched), garbage (unparseable), and out-of-range — and confirm that
only the valid value parses through while every other case degrades to the
documented default. ``down_weight_factor`` additionally covers its EXCLUSIVE
lower bound ``0.0`` (reverts to default) and its inclusive upper bound ``1.0``;
the opt-in boolean flag covers truthy/falsy spellings and garbage.

Env vars are set/unset through pytest's ``monkeypatch`` so the process
environment is restored after each test and tests never leak state into one
another. The service package is made importable exactly as the sibling unit
tests do (insert the parent dir on ``sys.path``).
"""

import os
import sys

import pytest

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
    ENV_CONTRIBUTION_THRESHOLD,
    ENV_DOWN_WEIGHT_FACTOR,
    ENV_GLOBAL_MIN_SCORED,
    ENV_MIN_SAMPLE_DIMENSION,
    ENV_MIN_SAMPLE_VALUE,
    ENV_WEIGHT_MAP_ENABLED,
    resolve_attribution_config,
)

# Every attribution env var — cleared before each test so the only values seen
# are the ones a test explicitly sets via monkeypatch.
_ALL_ENV = (
    ENV_MIN_SAMPLE_DIMENSION,
    ENV_MIN_SAMPLE_VALUE,
    ENV_CONTRIBUTION_THRESHOLD,
    ENV_GLOBAL_MIN_SCORED,
    ENV_DOWN_WEIGHT_FACTOR,
    ENV_WEIGHT_MAP_ENABLED,
)


@pytest.fixture(autouse=True)
def _clean_attribution_env(monkeypatch):
    """Start every test from a clean slate: no attribution env var is set."""
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


# ── ATTRIBUTION_MIN_SAMPLE_DIMENSION — int >= 1, default 30 ───────────────────

def test_min_sample_dimension_unset_uses_default(monkeypatch):
    """Validates: Requirements 7.2"""
    assert resolve_attribution_config().min_sample_dimension == DEFAULT_MIN_SAMPLE_DIMENSION


def test_min_sample_dimension_valid_parses_through(monkeypatch):
    """Validates: Requirements 7.1"""
    monkeypatch.setenv(ENV_MIN_SAMPLE_DIMENSION, "45")
    assert resolve_attribution_config().min_sample_dimension == 45


def test_min_sample_dimension_garbage_uses_default(monkeypatch):
    """Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_MIN_SAMPLE_DIMENSION, "not-a-number")
    assert resolve_attribution_config().min_sample_dimension == DEFAULT_MIN_SAMPLE_DIMENSION


def test_min_sample_dimension_out_of_range_uses_default(monkeypatch):
    """Below the minimum valid value (1) -> default. Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_MIN_SAMPLE_DIMENSION, "0")
    assert resolve_attribution_config().min_sample_dimension == DEFAULT_MIN_SAMPLE_DIMENSION


# ── ATTRIBUTION_MIN_SAMPLE_VALUE — int >= 1, default 10 ───────────────────────

def test_min_sample_value_unset_uses_default(monkeypatch):
    """Validates: Requirements 7.2"""
    assert resolve_attribution_config().min_sample_value == DEFAULT_MIN_SAMPLE_VALUE


def test_min_sample_value_valid_parses_through(monkeypatch):
    """Validates: Requirements 7.1"""
    monkeypatch.setenv(ENV_MIN_SAMPLE_VALUE, "25")
    assert resolve_attribution_config().min_sample_value == 25


def test_min_sample_value_garbage_uses_default(monkeypatch):
    """Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_MIN_SAMPLE_VALUE, "3.5")  # not an int
    assert resolve_attribution_config().min_sample_value == DEFAULT_MIN_SAMPLE_VALUE


def test_min_sample_value_out_of_range_uses_default(monkeypatch):
    """Negative / below-minimum -> default. Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_MIN_SAMPLE_VALUE, "-4")
    assert resolve_attribution_config().min_sample_value == DEFAULT_MIN_SAMPLE_VALUE


# ── ATTRIBUTION_CONTRIBUTION_THRESHOLD — float >= 0.0, default 0.15 ───────────

def test_contribution_threshold_unset_uses_default(monkeypatch):
    """Validates: Requirements 7.2"""
    assert resolve_attribution_config().contribution_threshold == DEFAULT_CONTRIBUTION_THRESHOLD


def test_contribution_threshold_valid_parses_through(monkeypatch):
    """Validates: Requirements 7.1"""
    monkeypatch.setenv(ENV_CONTRIBUTION_THRESHOLD, "0.42")
    assert resolve_attribution_config().contribution_threshold == 0.42


def test_contribution_threshold_garbage_uses_default(monkeypatch):
    """Unparseable (and the NaN guard) -> default. Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_CONTRIBUTION_THRESHOLD, "abc")
    assert resolve_attribution_config().contribution_threshold == DEFAULT_CONTRIBUTION_THRESHOLD


def test_contribution_threshold_out_of_range_uses_default(monkeypatch):
    """Negative threshold is out of [0.0, inf) -> default. Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_CONTRIBUTION_THRESHOLD, "-0.1")
    assert resolve_attribution_config().contribution_threshold == DEFAULT_CONTRIBUTION_THRESHOLD


# ── ATTRIBUTION_GLOBAL_MIN_SCORED — int >= 1, default 50 ──────────────────────

def test_global_min_scored_unset_uses_default(monkeypatch):
    """Validates: Requirements 7.2"""
    assert resolve_attribution_config().global_min_scored == DEFAULT_GLOBAL_MIN_SCORED


def test_global_min_scored_valid_parses_through(monkeypatch):
    """Validates: Requirements 7.1"""
    monkeypatch.setenv(ENV_GLOBAL_MIN_SCORED, "120")
    assert resolve_attribution_config().global_min_scored == 120


def test_global_min_scored_garbage_uses_default(monkeypatch):
    """Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_GLOBAL_MIN_SCORED, "")  # empty / whitespace
    assert resolve_attribution_config().global_min_scored == DEFAULT_GLOBAL_MIN_SCORED


def test_global_min_scored_out_of_range_uses_default(monkeypatch):
    """Below minimum (1) -> default. Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_GLOBAL_MIN_SCORED, "0")
    assert resolve_attribution_config().global_min_scored == DEFAULT_GLOBAL_MIN_SCORED


# ── ATTRIBUTION_DOWN_WEIGHT_FACTOR — float in (0.0, 1.0], default 0.5 ─────────

def test_down_weight_factor_unset_uses_default(monkeypatch):
    """Validates: Requirements 7.2"""
    assert resolve_attribution_config().down_weight_factor == DEFAULT_DOWN_WEIGHT_FACTOR


def test_down_weight_factor_valid_parses_through(monkeypatch):
    """An in-(0,1] value parses through untouched. Validates: Requirements 7.1"""
    monkeypatch.setenv(ENV_DOWN_WEIGHT_FACTOR, "0.75")
    assert resolve_attribution_config().down_weight_factor == 0.75


def test_down_weight_factor_upper_bound_inclusive_parses_through(monkeypatch):
    """The inclusive upper bound 1.0 is valid and parses through. Validates: Requirements 7.1"""
    monkeypatch.setenv(ENV_DOWN_WEIGHT_FACTOR, "1.0")
    assert resolve_attribution_config().down_weight_factor == 1.0


def test_down_weight_factor_garbage_uses_default(monkeypatch):
    """Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_DOWN_WEIGHT_FACTOR, "half")
    assert resolve_attribution_config().down_weight_factor == DEFAULT_DOWN_WEIGHT_FACTOR


def test_down_weight_factor_out_of_range_uses_default(monkeypatch):
    """Above the inclusive upper bound 1.0 -> default. Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_DOWN_WEIGHT_FACTOR, "1.5")
    assert resolve_attribution_config().down_weight_factor == DEFAULT_DOWN_WEIGHT_FACTOR


def test_down_weight_factor_zero_exclusive_lower_bound_reverts_to_default(monkeypatch):
    """0.0 sits on the EXCLUSIVE lower bound and reverts to default. Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_DOWN_WEIGHT_FACTOR, "0.0")
    assert resolve_attribution_config().down_weight_factor == DEFAULT_DOWN_WEIGHT_FACTOR


def test_down_weight_factor_negative_uses_default(monkeypatch):
    """A negative value is below the band -> default. Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_DOWN_WEIGHT_FACTOR, "-0.3")
    assert resolve_attribution_config().down_weight_factor == DEFAULT_DOWN_WEIGHT_FACTOR


# ── ATTRIBUTION_WEIGHT_MAP_ENABLED — bool, default False ──────────────────────

def test_weight_map_enabled_unset_uses_default(monkeypatch):
    """Validates: Requirements 7.2"""
    assert resolve_attribution_config().weight_map_enabled is DEFAULT_WEIGHT_MAP_ENABLED


def test_weight_map_enabled_truthy_parses_through(monkeypatch):
    """A recognized truthy spelling parses through to True. Validates: Requirements 7.1"""
    monkeypatch.setenv(ENV_WEIGHT_MAP_ENABLED, "true")
    assert resolve_attribution_config().weight_map_enabled is True


def test_weight_map_enabled_truthy_case_insensitive_parses_through(monkeypatch):
    """Truthy parsing is case-insensitive / whitespace-tolerant. Validates: Requirements 7.1"""
    monkeypatch.setenv(ENV_WEIGHT_MAP_ENABLED, "  YES ")
    assert resolve_attribution_config().weight_map_enabled is True


def test_weight_map_enabled_falsy_parses_through(monkeypatch):
    """A recognized falsy spelling parses through to False. Validates: Requirements 7.1"""
    monkeypatch.setenv(ENV_WEIGHT_MAP_ENABLED, "0")
    assert resolve_attribution_config().weight_map_enabled is False


def test_weight_map_enabled_garbage_uses_default(monkeypatch):
    """An unrecognized token degrades to the documented default. Validates: Requirements 7.2"""
    monkeypatch.setenv(ENV_WEIGHT_MAP_ENABLED, "maybe")
    assert resolve_attribution_config().weight_map_enabled is DEFAULT_WEIGHT_MAP_ENABLED


# ── Cross-cutting: a fully valid environment parses through on every field ────

def test_all_valid_env_parses_through_together(monkeypatch):
    """Every parameter set to a distinct valid value resolves to exactly that value.

    Validates: Requirements 7.1
    """
    monkeypatch.setenv(ENV_MIN_SAMPLE_DIMENSION, "40")
    monkeypatch.setenv(ENV_MIN_SAMPLE_VALUE, "15")
    monkeypatch.setenv(ENV_CONTRIBUTION_THRESHOLD, "0.2")
    monkeypatch.setenv(ENV_GLOBAL_MIN_SCORED, "60")
    monkeypatch.setenv(ENV_DOWN_WEIGHT_FACTOR, "0.6")
    monkeypatch.setenv(ENV_WEIGHT_MAP_ENABLED, "on")

    cfg = resolve_attribution_config()
    assert cfg.min_sample_dimension == 40
    assert cfg.min_sample_value == 15
    assert cfg.contribution_threshold == 0.2
    assert cfg.global_min_scored == 60
    assert cfg.down_weight_factor == 0.6
    assert cfg.weight_map_enabled is True


def test_resolve_never_raises_on_all_garbage(monkeypatch):
    """A fully garbage environment still resolves to the documented defaults.

    Validates: Requirements 7.2
    """
    for name in _ALL_ENV:
        monkeypatch.setenv(name, "@@garbage@@")

    cfg = resolve_attribution_config()
    assert cfg.min_sample_dimension == DEFAULT_MIN_SAMPLE_DIMENSION
    assert cfg.min_sample_value == DEFAULT_MIN_SAMPLE_VALUE
    assert cfg.contribution_threshold == DEFAULT_CONTRIBUTION_THRESHOLD
    assert cfg.global_min_scored == DEFAULT_GLOBAL_MIN_SCORED
    assert cfg.down_weight_factor == DEFAULT_DOWN_WEIGHT_FACTOR
    assert cfg.weight_map_enabled is DEFAULT_WEIGHT_MAP_ENABLED
