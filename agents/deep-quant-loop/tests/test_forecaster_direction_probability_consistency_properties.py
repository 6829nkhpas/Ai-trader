"""Property-based test for direction/probability consistency (forecaster.py, task 3.6).

Feature: volatility-aware-forecaster

This module implements design **Property 10: Direction and probability are
consistent**:

    The Projected_Direction (``classify_direction``) and the Up_Probability
    (``up_probability``) are both derived from the *same* regime-conditioned
    standardized drift ``z``, so they can never disagree (R3.5):

      * when ``classify_direction(z, config) == 'up'``  -> ``up_probability >= 0.5``,
      * when ``classify_direction(z, config) == 'down'`` -> ``up_probability <= 0.5``,
      * when ``classify_direction(z, config) == 'flat'`` -> no probability
        constraint is required by the spec (only the up/down constraints are
        asserted).

Validates: Requirements 3.5.

The generator deliberately spans negative / zero / positive finite ``z`` values
(tiny to large, plus the exact flat-band boundary where the direction flips) and
varies both ``prob_scale`` (the logistic steepness, across its valid
``[0.0, 50.0]`` range) and ``flat_band`` (the direction boundary, across its
valid ``[0.0, 5.0]`` range) via constructed ``ForecasterConfig``s. The sys.path /
import pattern mirrors the sibling forecaster property modules.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from forecaster import (  # noqa: E402
    ForecasterConfig,
    classify_direction,
    resolve_forecaster_config,
    up_probability,
)

_DIRECTIONS = {"up", "down", "flat"}

# A baseline config resolved from the environment (documented defaults when
# unset). Resolution never raises, so this is safe at import time.
_DEFAULT_CONFIG = resolve_forecaster_config()


def _build_config(flat_band, prob_scale):
    """A ForecasterConfig with chosen flat_band / prob_scale, valid placeholders elsewhere.

    Only ``flat_band`` (direction boundary) and ``prob_scale`` (logistic
    steepness) participate in ``classify_direction`` / ``up_probability``; the
    remaining fields are filled with valid values so the frozen dataclass can be
    constructed.
    """
    return ForecasterConfig(
        drift_lookback=20,
        vol_lookback=20,
        atr_period=14,
        flat_band=flat_band,
        min_candles=30,
        prob_bins=10,
        prob_scale=prob_scale,
    )


# Flat-band values across the documented valid range [0.0, 5.0], including the
# zero band (only z == 0 is flat) and wider bands.
_flat_band = st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False)

# Logistic probability scale across its full valid range, including the
# degenerate ``0.0`` (which flattens the logistic to a constant 0.5).
_prob_scale = st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)

# Arbitrary finite z values spanning negative / zero / positive magnitudes,
# from tiny to large.
_arbitrary_z = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)


@st.composite
def _z_and_config(draw):
    """A (z, config) pair, concentrating extra mass right at the flat-band boundary.

    The boundary cases (``z == ±flat_band`` and tiny offsets either side) drive
    the direction across its ``up`` / ``flat`` / ``down`` transitions, so the
    up/down probability constraints are exercised exactly where the direction
    flips. Both ``flat_band`` and ``prob_scale`` are varied so the consistency
    holds across many configs.
    """
    config = draw(st.one_of(st.just(_DEFAULT_CONFIG), st.builds(_build_config, _flat_band, _prob_scale)))
    band = config.flat_band
    boundary = st.sampled_from(
        [
            band,
            -band,
            math.nextafter(band, math.inf),    # just above +band -> up
            math.nextafter(band, -math.inf),   # just below +band -> flat
            math.nextafter(-band, -math.inf),  # just below -band -> down
            math.nextafter(-band, math.inf),   # just above -band -> flat
            0.0,
            -0.0,
        ]
    )
    z = draw(st.one_of(_arbitrary_z, boundary))
    return z, config


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (task 3.6): Direction and probability are consistent
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 10: Direction and probability are consistent
@settings(max_examples=300, deadline=None)
@given(z_and_config=_z_and_config())
def test_property_10_direction_and_probability_consistent(z_and_config):
    """Feature: volatility-aware-forecaster, Property 10: Direction and
    probability are consistent.

    For any standardized drift ``z`` and config, the Projected_Direction and the
    Up_Probability — both derived from the same ``z`` — agree: an ``up``
    direction yields ``p >= 0.5`` and a ``down`` direction yields ``p <= 0.5``.
    A ``flat`` direction carries no spec-required probability constraint.

    Validates: Requirements 3.5
    """
    z, config = z_and_config

    direction = classify_direction(z, config)
    p = up_probability(z, config)

    # Well-formed direction and probability up front.
    assert direction in _DIRECTIONS, f"classify_direction returned a non-enum value: {direction!r}"
    assert isinstance(p, float) and math.isfinite(p), f"up_probability returned a bad value: {p!r}"
    assert 0.0 <= p <= 1.0, f"up_probability escaped [0.0, 1.0]: {p!r}"

    # ── Direction/probability consistency (R3.5) ─────────────────────────────
    if direction == "up":
        assert p >= 0.5, f"direction 'up' must yield p >= 0.5, got z={z!r}, p={p!r}"
    elif direction == "down":
        assert p <= 0.5, f"direction 'down' must yield p <= 0.5, got z={z!r}, p={p!r}"
    # direction == 'flat': no spec-required probability constraint.
