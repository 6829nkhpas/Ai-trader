"""Property-based test for total alignment derivation (forecaster.py, task 3.7).

Feature: volatility-aware-forecaster

This module implements design **Property 11: Forecast_Alignment is a total
function of projected and proposed direction**:

    For any Projected_Direction value (``up`` / ``down`` / ``flat`` plus garbage
    / ``None``) and any proposed trade direction (``up`` / ``down`` / ``buy`` /
    ``sell`` / ``long`` / ``short`` / ``hold`` / ``''`` / ``None`` / garbage /
    non-strings), ``derive_forecast_alignment`` returns exactly one
    Forecast_Alignment value drawn from ``{aligned, misaligned, neutral}`` — so
    every combination maps to exactly one value and the function never raises
    (totality).

Validates: Requirements 3.6.

``derive_forecast_alignment`` is a pure, total classifier. A proposed direction
is normalized so a ``buy`` / ``long`` / ``up`` proposal is the ``up`` side and a
``sell`` / ``short`` / ``down`` proposal is the ``down`` side; ``None`` / empty /
``hold`` / any unrecognized value means "no proposed direction" and yields
``neutral`` for every Projected_Direction. When a proposed side exists, a
projected ``up`` / ``down`` that matches the side is ``aligned``, an opposed one
is ``misaligned``, and a ``flat`` / unrecognized projected direction is
``neutral``. The sys.path / import pattern mirrors
``tests/test_rs_alignment_total_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from forecaster import derive_forecast_alignment  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Input space.
#
# Projected_Direction: the well-formed enum (up/down/flat) plus garbage and
# non-string values so the unrecognized -> neutral path is exercised.
# Proposed_Direction: the recognised up-side verbs (up/buy/long), the recognised
# down-side verbs (down/sell/short), HOLD, the empty string, the absent marker
# (None), arbitrary free text, and non-string values.
# ─────────────────────────────────────────────────────────────────────────────

_PROJECTED_DIRECTION = st.one_of(
    st.sampled_from(["up", "down", "flat", "UP", "Down", " flat ", "sideways", ""]),
    st.none(),
    st.integers(),
    st.text(),
)

_PROPOSED_DIRECTION = st.one_of(
    st.sampled_from(
        [
            "up", "down", "buy", "sell", "long", "short", "hold",
            "BUY", "Sell", " long ", "HOLD", "", "garbage",
        ]
    ),
    st.none(),
    st.integers(),
    st.floats(),
    st.text(),
)

_ALIGNMENT_VALUES = {"aligned", "misaligned", "neutral"}

# Canonical normalization oracle for the well-defined cases (mirrors the
# documented mapping in forecaster.derive_forecast_alignment without importing
# the private helper).
_UP_SIDE = {"up", "buy", "long"}
_DOWN_SIDE = {"down", "sell", "short"}


def _expected_proposed_side(proposed_direction):
    """Return ``'up'`` / ``'down'`` / ``None`` for a proposed direction."""
    if not isinstance(proposed_direction, str):
        return None
    token = proposed_direction.strip().lower()
    if token in _UP_SIDE:
        return "up"
    if token in _DOWN_SIDE:
        return "down"
    return None


def _expected_alignment(projected_direction, proposed_direction):
    """Independent oracle of the expected Forecast_Alignment value."""
    side = _expected_proposed_side(proposed_direction)
    if side is None:
        return "neutral"
    projected = projected_direction if isinstance(projected_direction, str) else ""
    projected = projected.strip().lower()
    if projected not in ("up", "down"):
        return "neutral"
    return "aligned" if projected == side else "misaligned"


# ─────────────────────────────────────────────────────────────────────────────
# Property 11: Forecast_Alignment is a total function of projected and proposed
# direction
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 11: Forecast_Alignment is a total function of projected and proposed direction
@settings(max_examples=200, deadline=None)
@given(
    projected_direction=_PROJECTED_DIRECTION,
    proposed_direction=_PROPOSED_DIRECTION,
)
def test_property_11_alignment_is_a_total_function(
    projected_direction, proposed_direction
):
    """Feature: volatility-aware-forecaster, Property 11: Forecast_Alignment is a
    total function of projected and proposed direction.

    For every (Projected_Direction x proposed_direction) combination,
    ``derive_forecast_alignment`` returns exactly one value drawn from
    ``{aligned, misaligned, neutral}`` (totality, never raises) and matches the
    documented mapping for the well-defined cases.

    Validates: Requirements 3.6
    """
    # Totality: the derivation must never raise for any input combination.
    alignment = derive_forecast_alignment(projected_direction, proposed_direction)

    # The result is exactly one well-formed Alignment value.
    assert alignment in _ALIGNMENT_VALUES, (
        f"derive_forecast_alignment({projected_direction!r}, "
        f"{proposed_direction!r}) returned {alignment!r}, which is not one of "
        f"{sorted(_ALIGNMENT_VALUES)}"
    )

    # The result matches the documented mapping for the well-defined cases:
    #   projected up + proposed buy  -> aligned
    #   projected up + proposed sell -> misaligned
    #   projected flat               -> neutral
    #   no / HOLD / unrecognized     -> neutral
    assert alignment == _expected_alignment(projected_direction, proposed_direction), (
        f"derive_forecast_alignment({projected_direction!r}, "
        f"{proposed_direction!r}) returned {alignment!r}, expected "
        f"{_expected_alignment(projected_direction, proposed_direction)!r}"
    )
