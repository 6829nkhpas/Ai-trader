"""Property-based test for total alignment derivation (order_flow.py, task 4.7).

Feature: order-flow-context

This module implements design **Property 11: Alignment is a total function of
state and proposed direction**:

    For any Order_Flow_State value (``buying`` / ``selling`` / ``balanced``) and
    any proposed trade direction (``BUY`` / ``SELL`` / ``HOLD`` / absent /
    arbitrary string / ``None``), ``derive_alignment`` returns exactly one
    Alignment value drawn from ``aligned`` / ``misaligned`` / ``neutral`` — a
    total function with no exceptions and no missing combinations — and the
    returned value matches the design's Alignment derivation tables.

Validates: Requirements 3.3.

The design's Alignment derivation tables (total over Order_Flow_State x
proposed_direction) are:

    proposed BUY  + buying   -> aligned
    proposed BUY  + selling  -> misaligned
    proposed SELL + selling  -> aligned
    proposed SELL + buying   -> misaligned
    balanced state, any dir  -> neutral
    absent / None / HOLD / non-directional / unrecognised direction -> neutral

``derive_alignment`` normalises the proposed direction case-insensitively and
trims surrounding whitespace, so ``"buy"`` / ``" BUY "`` / ``"Sell"`` are treated
as their canonical verbs while any other string (including ``""`` and arbitrary
free text), ``None``, and non-string values are non-directional -> ``neutral``.

This test generates the full cross product of the three Order_Flow_States and a
wide range of direction inputs (the directional verbs in several case variants,
``HOLD``, the absent marker ``None``, the empty string, and arbitrary free text)
and asserts the derivation always returns exactly one of the three Alignment
values, never raises (totality), and equals the value the design tables dictate.
The sys.path / import pattern mirrors the sibling ``test_of_*_properties.py``
modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import derive_alignment  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Input space: every Order_Flow_State x a broad range of proposed directions
# (directional verbs in several case/whitespace variants, the non-directional
# HOLD, the absent marker None, the empty string, and arbitrary free text).
# ─────────────────────────────────────────────────────────────────────────────

_ORDER_FLOW_STATES = st.sampled_from(["buying", "selling", "balanced"])

_PROPOSED_DIRECTION = st.one_of(
    st.sampled_from(
        [
            "BUY",
            "SELL",
            "HOLD",
            "buy",
            "sell",
            "hold",
            " buy ",
            " SELL ",
            "Buy",
            "Sell",
            "",
            None,
        ]
    ),
    st.none(),
    st.text(),
)

_ALIGNMENT_VALUES = {"aligned", "misaligned", "neutral"}


def _expected_alignment(order_flow_state, proposed_direction):
    """Independent recomputation of the Alignment per the design tables."""
    if not isinstance(proposed_direction, str):
        return "neutral"
    direction = proposed_direction.strip().upper()
    if direction == "BUY":
        if order_flow_state == "buying":
            return "aligned"
        if order_flow_state == "selling":
            return "misaligned"
        return "neutral"  # balanced
    if direction == "SELL":
        if order_flow_state == "selling":
            return "aligned"
        if order_flow_state == "buying":
            return "misaligned"
        return "neutral"  # balanced
    # HOLD / absent / unrecognised direction -> neutral
    return "neutral"


# ─────────────────────────────────────────────────────────────────────────────
# Property 11 (task 4.7): Alignment is a total function of state and direction
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 11: Alignment is a total function of state and proposed direction
@settings(max_examples=300, deadline=None)
@given(
    order_flow_state=_ORDER_FLOW_STATES,
    proposed_direction=_PROPOSED_DIRECTION,
)
def test_property_11_alignment_is_a_total_function(order_flow_state, proposed_direction):
    """Feature: order-flow-context, Property 11: Alignment is a total function of
    state and proposed direction.

    For every (Order_Flow_State x proposed_direction) combination,
    ``derive_alignment`` returns exactly one value drawn from
    ``{aligned, misaligned, neutral}``, never raises (totality), and equals the
    value the design's Alignment derivation tables dictate.

    Validates: Requirements 3.3
    """
    # Totality: the derivation must never raise for any input combination.
    alignment = derive_alignment(order_flow_state, proposed_direction)

    # The result is exactly one well-formed Alignment value.
    assert alignment in _ALIGNMENT_VALUES, (
        f"derive_alignment({order_flow_state!r}, {proposed_direction!r}) returned "
        f"{alignment!r}, which is not one of {sorted(_ALIGNMENT_VALUES)}"
    )

    # The value matches the design's Alignment derivation tables.
    expected = _expected_alignment(order_flow_state, proposed_direction)
    assert alignment == expected, (
        f"derive_alignment({order_flow_state!r}, {proposed_direction!r}) = "
        f"{alignment!r} != table-dictated {expected!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Exhaustive enumeration: the full, finite cross product of the three states and
# the recognised/representative directions maps to exactly one Alignment with no
# missing combination (the "no missing combinations" half of Property 11).
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 11: Alignment is a total function of state and proposed direction
def test_property_11_exhaustive_cross_product_is_total():
    """Feature: order-flow-context, Property 11: Alignment is a total function of
    state and proposed direction.

    Enumerates the full cross product of the three Order_Flow_States and a
    representative set of direction inputs (directional verbs with case/whitespace
    variants, HOLD, the empty string, an arbitrary string, and None) and asserts
    every combination yields exactly one Alignment matching the design tables —
    proving there are no missing combinations.

    Validates: Requirements 3.3
    """
    states = ["buying", "selling", "balanced"]
    directions = [
        "BUY",
        "SELL",
        "HOLD",
        "buy",
        "sell",
        " buy ",
        " SELL ",
        "Buy",
        "Sell",
        "",
        "wibble",
        None,
        123,  # non-string -> neutral (totality over non-string inputs)
    ]

    for state in states:
        for direction in directions:
            alignment = derive_alignment(state, direction)
            assert alignment in _ALIGNMENT_VALUES, (
                f"derive_alignment({state!r}, {direction!r}) returned {alignment!r}"
            )
            expected = _expected_alignment(state, direction)
            assert alignment == expected, (
                f"derive_alignment({state!r}, {direction!r}) = {alignment!r} "
                f"!= table-dictated {expected!r}"
            )

    # Spot-check the four canonical directional pairings (the heart of the table).
    assert derive_alignment("buying", "BUY") == "aligned"
    assert derive_alignment("selling", "BUY") == "misaligned"
    assert derive_alignment("selling", "SELL") == "aligned"
    assert derive_alignment("buying", "SELL") == "misaligned"
    # Balanced is always neutral; HOLD/absent is always neutral.
    assert derive_alignment("balanced", "BUY") == "neutral"
    assert derive_alignment("balanced", "SELL") == "neutral"
    assert derive_alignment("buying", "HOLD") == "neutral"
    assert derive_alignment("selling", None) == "neutral"
