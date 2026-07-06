"""Property-based test for finite, bounded, clamped Up_Probability (forecaster.py, task 3.4).

Feature: volatility-aware-forecaster

This module implements design **Property 7: Up_Probability is finite, bounded,
and clamped**:

    ``up_probability(z, config)`` maps a standardized drift ``z`` through the
    logistic ``clamp(1 / (1 + exp(-prob_scale * z)), 0.0, 1.0)``. For any
    ``z`` — including very large magnitude positive/negative values that would
    overflow a naive ``exp`` — and any valid ``prob_scale``:

      * the result is always a finite float within ``[0.0, 1.0]`` (R3.2, R4.4),
      * it equals exactly ``0.5`` when ``z == 0`` (the logistic midpoint), and
      * monotonicity is consistent: ``z >= 0 => p >= 0.5`` and
        ``z <= 0 => p <= 0.5`` (direction/probability agreement).

Validates: Requirements 3.2, 4.4.

The generator deliberately spans ordinary magnitudes, very large positive and
negative magnitudes (to exercise the overflow / clamping guard), and the exact
``z == 0`` midpoint. ``prob_scale`` is varied across its valid ``[0.0, 50.0]``
range via ``dataclasses.replace`` over the resolved default config. The
sys.path / import pattern mirrors the sibling forecaster property modules.
"""

import dataclasses
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
    resolve_forecaster_config,
    up_probability,
)

_BASE_CONFIG = resolve_forecaster_config()

# Standardized drift values: ordinary magnitudes, very large positive/negative
# magnitudes (to drive the logistic toward its saturated 0.0 / 1.0 edges and
# exercise the overflow guard), and the exact midpoint ``z == 0``.
_z = st.one_of(
    st.just(0.0),
    st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=-1e308, max_value=1e308, allow_nan=False, allow_infinity=False),
    st.sampled_from([1e6, -1e6, 1e150, -1e150, 1e300, -1e300]),
)

# Logistic probability scale across its full valid range, including the
# degenerate ``0.0`` (which flattens the logistic to a constant 0.5).
_prob_scale = st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)


# ─────────────────────────────────────────────────────────────────────────────
# Property 7 (task 3.4): Up_Probability is finite, bounded, and clamped
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 7: Up_Probability is finite, bounded, and clamped
@settings(max_examples=300, deadline=None)
@given(z=_z, prob_scale=_prob_scale)
def test_property_7_up_probability_finite_bounded_clamped(z, prob_scale):
    """Feature: volatility-aware-forecaster, Property 7: Up_Probability is
    finite, bounded, and clamped.

    For any standardized drift ``z`` (including very large magnitudes and the
    exact midpoint) and any valid ``prob_scale``, ``up_probability`` returns a
    finite float in ``[0.0, 1.0]``, equals exactly ``0.5`` when ``z == 0``, and
    is monotone-consistent (``z >= 0 => p >= 0.5``, ``z <= 0 => p <= 0.5``).

    Validates: Requirements 3.2, 4.4
    """
    config = dataclasses.replace(_BASE_CONFIG, prob_scale=prob_scale)

    p = up_probability(z, config)

    # ── Finite, bounded, clamped float (R3.2, R4.4) ──────────────────────────
    assert isinstance(p, float), f"up_probability returned a non-float: {p!r}"
    assert math.isfinite(p), f"up_probability returned a non-finite value: {p!r}"
    assert 0.0 <= p <= 1.0, f"up_probability escaped [0.0, 1.0]: {p!r}"

    # ── Exact midpoint at z == 0 ─────────────────────────────────────────────
    if z == 0.0:
        assert p == 0.5, f"up_probability(0) must be exactly 0.5, got {p!r}"

    # ── Monotone direction/probability consistency ───────────────────────────
    if z >= 0.0:
        assert p >= 0.5, f"z >= 0 must yield p >= 0.5, got z={z!r}, p={p!r}"
    if z <= 0.0:
        assert p <= 0.5, f"z <= 0 must yield p <= 0.5, got z={z!r}, p={p!r}"
