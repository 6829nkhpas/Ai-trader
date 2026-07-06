"""Property-based test for well-formed direction matching the flat-band mapping (forecaster.py, task 3.3).

Feature: volatility-aware-forecaster

This module implements design **Property 6: Projected_Direction is well-formed
and matches the flat-band mapping**:

    For any finite standardized drift ``z`` and any resolved/constructed
    ``ForecasterConfig``, ``classify_direction(z, config)`` returns exactly one
    of ``'up'`` / ``'down'`` / ``'flat'`` and matches the exact flat-band
    mapping table:

      * ``abs(z) <= flat_band`` -> ``'flat'``,
      * ``z > flat_band``       -> ``'up'``,
      * ``z < -flat_band``      -> ``'down'``.

Validates: Requirements 3.1.

The generator deliberately spans negative / zero / positive finite ``z`` values
and concentrates extra mass right at the flat-band boundary (``±flat_band`` and
tiny offsets either side) so the inclusive ``<=`` boundary of the ``flat`` band
is exercised exactly. Configs are both the env-resolved default and explicitly
constructed configs with varied ``flat_band`` values so the mapping is checked
against many bands. The sys.path / import pattern mirrors the sibling
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
    ForecasterConfig,
    classify_direction,
    resolve_forecaster_config,
)

_DIRECTIONS = {"up", "down", "flat"}

# A baseline config resolved from the environment (documented defaults when
# unset). Resolution never raises, so this is safe at import time.
_DEFAULT_CONFIG = resolve_forecaster_config()


def _with_flat_band(flat_band):
    """A ForecasterConfig identical to the default but with a chosen flat_band.

    Only ``flat_band`` participates in ``classify_direction``; the remaining
    fields are filled with valid placeholder values so the frozen dataclass can
    be constructed.
    """
    return ForecasterConfig(
        drift_lookback=20,
        vol_lookback=20,
        atr_period=14,
        flat_band=flat_band,
        min_candles=30,
        prob_bins=10,
        prob_scale=2.0,
    )


# Flat-band values spanning the documented valid range [0.0, 5.0], including the
# zero band (where only z == 0 is flat) and wider bands.
_flat_band = st.floats(
    min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False
)

# A config strategy: either the env-resolved default or an explicitly constructed
# config with a varied flat_band.
_config = st.one_of(
    st.just(_DEFAULT_CONFIG),
    _flat_band.map(_with_flat_band),
)

# Arbitrary finite z values spanning negative / zero / positive magnitudes,
# from tiny to large.
_arbitrary_z = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)


@st.composite
def _z_and_config(draw):
    """A (z, config) pair, concentrating extra mass at the flat-band boundary.

    With the boundary cases mixed in, the inclusive ``abs(z) <= flat_band`` edge
    (``z == ±flat_band`` and tiny offsets either side) is exercised directly in
    addition to broad arbitrary z values.
    """
    config = draw(_config)
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
# Property 6 (task 3.3): Projected_Direction is well-formed and matches the
# flat-band mapping
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 6: Projected_Direction is well-formed and matches the flat-band mapping
@settings(max_examples=300, deadline=None)
@given(z_and_config=_z_and_config())
def test_property_6_direction_well_formed_matches_flat_band_mapping(z_and_config):
    """Feature: volatility-aware-forecaster, Property 6: Projected_Direction is
    well-formed and matches the flat-band mapping.

    For any finite ``z`` and config, ``classify_direction`` returns exactly one
    of ``up`` / ``down`` / ``flat`` and matches the exact mapping:
    ``abs(z) <= flat_band`` -> ``flat``; ``z > flat_band`` -> ``up``;
    ``z < -flat_band`` -> ``down``.

    Validates: Requirements 3.1
    """
    z, config = z_and_config
    flat_band = config.flat_band

    direction = classify_direction(z, config)

    # Well-formed: always exactly one of the three categorical values.
    assert direction in _DIRECTIONS, (
        f"classify_direction returned a non-enum value: {direction!r}"
    )

    # Exact flat-band mapping.
    if abs(z) <= flat_band:
        expected = "flat"
    elif z > flat_band:
        expected = "up"
    else:  # z < -flat_band
        expected = "down"

    assert direction == expected, (
        f"classify_direction({z!r}, flat_band={flat_band!r}) returned "
        f"{direction!r}, expected {expected!r}"
    )
