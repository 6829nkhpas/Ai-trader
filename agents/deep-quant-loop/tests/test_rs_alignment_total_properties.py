"""Property-based test for total alignment derivation (rs.py, task 3.6).

Feature: relative-strength-context

This module implements design **Property 6: Alignment is a total function of its
three inputs**:

    For any combination of an Index_Direction value, a Relative_Strength_State
    value, and a proposed trade direction (BUY, SELL, or absent),
    ``derive_alignment`` returns exactly one Alignment value drawn from
    ``aligned``/``misaligned``/``neutral``, so that every combination maps to
    exactly one Alignment.

Validates: Requirements 1.8.

``derive_alignment`` is a pure, total classifier over the cartesian product of
Index_Direction x Relative_Strength_State x proposed_direction. This test
generates every Index_Direction in ``{up, down, flat}``, every
Relative_Strength_State in ``{leader, inline, laggard}``, and a proposed
direction drawn from the directional verbs (``BUY``/``SELL``/``HOLD``), the
absent marker (``None``), and arbitrary free text — then asserts the derivation
always returns exactly one of the three Alignment values and never raises
(totality). The sys.path / import pattern mirrors
``tests/test_rs_clamping_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from rs import derive_alignment  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Input space: every Index_Direction x every Relative_Strength_State x a broad
# range of proposed directions (directional verbs, non-directional verbs, the
# absent marker, and arbitrary free text).
# ─────────────────────────────────────────────────────────────────────────────

_INDEX_DIRECTIONS = st.sampled_from(["up", "down", "flat"])
_RS_STATES = st.sampled_from(["leader", "inline", "laggard"])

# Proposed direction covers the recognised directional verbs, the
# non-directional HOLD, the absent marker (None), and arbitrary text so totality
# is exercised over inputs well outside the recognised vocabulary.
_PROPOSED_DIRECTION = st.one_of(
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", " buy ", "Sell", None]),
    st.none(),
    st.text(),
)

_ALIGNMENT_VALUES = {"aligned", "misaligned", "neutral"}


# ─────────────────────────────────────────────────────────────────────────────
# Property 6: Alignment is a total function of its three inputs
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 6: Alignment is a total function of its three inputs
@settings(max_examples=100, deadline=None)
@given(
    index_direction=_INDEX_DIRECTIONS,
    rs_state=_RS_STATES,
    proposed_direction=_PROPOSED_DIRECTION,
)
def test_property_6_alignment_is_a_total_function(
    index_direction, rs_state, proposed_direction
):
    """Feature: relative-strength-context, Property 6: Alignment is a total
    function of its three inputs.

    For every (Index_Direction x Relative_Strength_State x proposed_direction)
    combination, ``derive_alignment`` returns exactly one value drawn from
    ``{aligned, misaligned, neutral}`` and never raises.

    Validates: Requirements 1.8
    """
    # Totality: the derivation must never raise for any input combination.
    alignment = derive_alignment(index_direction, rs_state, proposed_direction)

    # The result is exactly one well-formed Alignment value.
    assert alignment in _ALIGNMENT_VALUES, (
        f"derive_alignment({index_direction!r}, {rs_state!r}, "
        f"{proposed_direction!r}) returned {alignment!r}, "
        f"which is not one of {sorted(_ALIGNMENT_VALUES)}"
    )
