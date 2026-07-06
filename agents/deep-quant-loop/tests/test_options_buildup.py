"""Unit tests for per-strike OI-buildup classification (options.py, task 5.5).

Feature: options-analytics-engine

These plain ``pytest`` unit tests exercise ``options.classify_oi_buildup`` — the
deterministic, total function that maps an open-interest change (``d_oi``) and a
price change (``d_price``) to exactly one of the five OI-buildup labels following
the design's mapping table (Requirements 3.1, 3.4):

    | sign(ΔOI) \\ sign(Δprice) |   > 0          |   < 0           |   0       |
    | ------------------------- | -------------- | --------------- | --------- |
    |   > 0                     | long_buildup   | short_buildup   | neutral   |
    |   < 0                     | short_covering | long_unwinding  | neutral   |
    |   0                       | neutral        | neutral         | neutral   |

Task 5.5 asks for one concrete example per label plus the dead-band (neutral)
cases, and a custom config with non-zero dead-bands. These are plain
example-based tests (no Hypothesis), mirroring the convention in
``test_options_config.py`` (the ``sys.path`` shim so ``options.py`` one level up
is importable, plain ``pytest`` functions, ``resolve_options_config`` for the
default config).
"""

import os
import sys

import pytest

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options import (  # noqa: E402
    BUILDUP_LONG,
    BUILDUP_LONG_UNWINDING,
    BUILDUP_NEUTRAL,
    BUILDUP_SHORT,
    BUILDUP_SHORT_COVERING,
    OptionsConfig,
    classify_oi_buildup,
    resolve_options_config,
)


@pytest.fixture
def default_config():
    """The documented default config: exact-zero dead-bands (eps = 0.0)."""
    config = resolve_options_config()
    # Sanity-check the assumption these tests rely on: default dead-bands are 0.
    assert config.buildup_oi_epsilon == 0.0
    assert config.buildup_price_epsilon == 0.0
    return config


# ─────────────────────────────────────────────────────────────────────────────
# One concrete example per label (Requirement 3.1) — default (zero) dead-bands.
# ─────────────────────────────────────────────────────────────────────────────

def test_long_buildup(default_config):
    """Rising OI + rising price -> long_buildup (ΔOI > 0, Δprice > 0)."""
    assert classify_oi_buildup(1000.0, 5.0, default_config) == BUILDUP_LONG


def test_short_buildup(default_config):
    """Rising OI + falling price -> short_buildup (ΔOI > 0, Δprice < 0)."""
    assert classify_oi_buildup(1000.0, -5.0, default_config) == BUILDUP_SHORT


def test_short_covering(default_config):
    """Falling OI + rising price -> short_covering (ΔOI < 0, Δprice > 0)."""
    assert classify_oi_buildup(-1000.0, 5.0, default_config) == BUILDUP_SHORT_COVERING


def test_long_unwinding(default_config):
    """Falling OI + falling price -> long_unwinding (ΔOI < 0, Δprice < 0)."""
    assert classify_oi_buildup(-1000.0, -5.0, default_config) == BUILDUP_LONG_UNWINDING


# ─────────────────────────────────────────────────────────────────────────────
# Neutral: a zero (or within-dead-band) ΔOI or Δprice (Requirements 3.4).
# ─────────────────────────────────────────────────────────────────────────────

def test_neutral_zero_oi(default_config):
    """Exactly-zero ΔOI -> neutral regardless of a non-zero Δprice."""
    assert classify_oi_buildup(0.0, 5.0, default_config) == BUILDUP_NEUTRAL


def test_neutral_zero_price(default_config):
    """Exactly-zero Δprice -> neutral regardless of a non-zero ΔOI."""
    assert classify_oi_buildup(1000.0, 0.0, default_config) == BUILDUP_NEUTRAL


def test_neutral_both_zero(default_config):
    """Both changes zero -> neutral."""
    assert classify_oi_buildup(0.0, 0.0, default_config) == BUILDUP_NEUTRAL


# ─────────────────────────────────────────────────────────────────────────────
# Custom config with non-zero dead-bands (Requirement 3.4).
# A change whose magnitude is within its epsilon is treated as "no change".
# ─────────────────────────────────────────────────────────────────────────────

def _config_with_deadbands(oi_eps: float, price_eps: float) -> OptionsConfig:
    """Build an OptionsConfig identical to defaults but with the given dead-bands."""
    base = resolve_options_config()
    return OptionsConfig(
        risk_free_rate=base.risk_free_rate,
        iv_tolerance=base.iv_tolerance,
        iv_max_iterations=base.iv_max_iterations,
        iv_min_vol=base.iv_min_vol,
        iv_max_vol=base.iv_max_vol,
        oi_wall_min_oi=base.oi_wall_min_oi,
        buildup_oi_epsilon=oi_eps,
        buildup_price_epsilon=price_eps,
    )


def test_custom_deadband_oi_within_band_is_neutral():
    """A ΔOI within the OI dead-band collapses to neutral even with a real Δprice."""
    config = _config_with_deadbands(oi_eps=100.0, price_eps=1.0)
    # |ΔOI| = 50 <= 100 -> within band -> no OI direction -> neutral.
    assert classify_oi_buildup(50.0, 5.0, config) == BUILDUP_NEUTRAL


def test_custom_deadband_price_within_band_is_neutral():
    """A Δprice within the price dead-band collapses to neutral even with a real ΔOI."""
    config = _config_with_deadbands(oi_eps=100.0, price_eps=1.0)
    # |Δprice| = 0.5 <= 1.0 -> within band -> no price direction -> neutral.
    assert classify_oi_buildup(1000.0, 0.5, config) == BUILDUP_NEUTRAL


def test_custom_deadband_both_outside_band_classifies():
    """Both changes beyond their dead-bands classify by sign (here long_buildup)."""
    config = _config_with_deadbands(oi_eps=100.0, price_eps=1.0)
    # |ΔOI| = 150 > 100 and |Δprice| = 2.0 > 1.0 -> rising OI + rising price.
    assert classify_oi_buildup(150.0, 2.0, config) == BUILDUP_LONG


def test_custom_deadband_short_covering_outside_band():
    """Falling OI + rising price beyond the dead-bands -> short_covering."""
    config = _config_with_deadbands(oi_eps=100.0, price_eps=1.0)
    # |ΔOI| = 150 > 100 (negative) and |Δprice| = 2.0 > 1.0 (positive).
    assert classify_oi_buildup(-150.0, 2.0, config) == BUILDUP_SHORT_COVERING
