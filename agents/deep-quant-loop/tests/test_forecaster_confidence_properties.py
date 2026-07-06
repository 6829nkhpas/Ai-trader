"""Property-based test for finite, bounded confidence increasing with drift strength (forecaster.py, task 3.5).

Feature: volatility-aware-forecaster

This module implements design **Property 9: Forecast_Confidence is finite,
bounded, and increases with drift strength**:

    For any standardized drift ``z``, ``forecast_confidence(z, config)`` is a
    finite value within ``[0.0, 1.0]`` (R3.4, R4.4); it equals ``0.0`` exactly
    when ``z == 0`` (a flat / zero-drift forecast); and it is non-decreasing in
    the *magnitude* of the drift: for any pair ``z1``, ``z2`` with
    ``abs(z1) < abs(z2)`` it holds that
    ``forecast_confidence(z1) <= forecast_confidence(z2)``.

    ``forecast_confidence`` is defined as
    ``clamp(2 * abs(up_probability(z) - 0.5), 0.0, 1.0)`` — a function of
    ``abs(z)`` (the drift strength relative to volatility) via the logistic
    ``up_probability``. Because the logistic saturates toward its bounds for
    large-magnitude drift, the magnitude relationship is asserted as
    non-decreasing (``<=``) to account for clamping saturation at the extremes.

Validates: Requirements 3.4, 4.4.

The standardized-drift ``z`` spans ordinary, near-zero, large, and extreme
magnitudes (including values that saturate the logistic) so the finite/bounded,
zero-at-zero, and monotone-in-magnitude paths are all exercised. The sys.path /
import pattern mirrors the sibling
``test_forecaster_estimation_measures_properties.py`` module.
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
    forecast_confidence,
    resolve_forecaster_config,
)

# Standardized-drift values spanning ordinary, near-zero, large, and extreme
# magnitudes (the extremes drive the logistic into its saturated region, where
# clamping makes the magnitude relationship non-strict).
_Z = st.one_of(
    st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    st.floats(min_value=-1e-6, max_value=1e-6, allow_nan=False, allow_infinity=False),
    st.sampled_from([0.0, -0.0, 0.25, -0.25, 1.0, -1.0, 1e3, -1e3, 1e6, -1e6]),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 9 (task 3.5): Forecast_Confidence is finite, bounded, and increases
# with drift strength
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 9: Forecast_Confidence is finite, bounded, and increases with drift strength
@settings(max_examples=200, deadline=None)
@given(z1=_Z, z2=_Z)
def test_property_9_confidence_finite_bounded_increasing_with_drift_strength(z1, z2):
    """Feature: volatility-aware-forecaster, Property 9: Forecast_Confidence is
    finite, bounded, and increases with drift strength.

    For any standardized drift ``z``, ``forecast_confidence`` is finite within
    ``[0.0, 1.0]``, equals ``0.0`` when ``z == 0``, and is non-decreasing in
    ``abs(z)`` (``<=`` accounts for clamping saturation at the extremes).

    Validates: Requirements 3.4, 4.4
    """
    config = resolve_forecaster_config()

    c1 = forecast_confidence(z1, config)
    c2 = forecast_confidence(z2, config)

    # ── Finite and bounded within [0.0, 1.0] (R3.4, R4.4) ────────────────────
    for z, c in ((z1, c1), (z2, c2)):
        assert isinstance(c, float) and math.isfinite(c), (
            f"forecast_confidence({z!r}) returned a non-finite value: {c!r}"
        )
        assert 0.0 <= c <= 1.0, (
            f"forecast_confidence({z!r}) = {c!r} outside [0.0, 1.0]"
        )

    # ── Zero confidence exactly at zero drift (flat / no drift) ──────────────
    if z1 == 0.0:
        assert c1 == 0.0, f"forecast_confidence(0) should be 0.0, got {c1!r}"
    if z2 == 0.0:
        assert c2 == 0.0, f"forecast_confidence(0) should be 0.0, got {c2!r}"

    # ── Non-decreasing in drift strength abs(z) (R3.4) ───────────────────────
    # For abs(z1) < abs(z2), confidence is non-decreasing; <= accounts for
    # clamping saturation when both magnitudes push the logistic to its bound.
    if abs(z1) < abs(z2):
        assert c1 <= c2, (
            f"confidence not non-decreasing in |z|: |z1|={abs(z1)!r} -> {c1!r} "
            f"but |z2|={abs(z2)!r} -> {c2!r}"
        )
    elif abs(z2) < abs(z1):
        assert c2 <= c1, (
            f"confidence not non-decreasing in |z|: |z2|={abs(z2)!r} -> {c2!r} "
            f"but |z1|={abs(z1)!r} -> {c1!r}"
        )
