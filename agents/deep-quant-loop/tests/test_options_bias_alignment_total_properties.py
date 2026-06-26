"""Property-based test for total alignment derivation (options_bias.py, task 2.3).

Feature: options-agent-integration

This module implements design **Property 2: Alignment is a total function of
(bias state x proposed direction)**:

    For any Options_Bias_State value (``bullish`` / ``bearish`` / ``neutral`` —
    plus arbitrary/garbage states) and any proposed trade direction (``BUY`` /
    ``SELL`` / ``HOLD`` / absent / empty / arbitrary string / ``None`` / non-string
    / case & whitespace variants), ``derive_alignment`` returns exactly one
    Alignment value drawn from ``aligned`` / ``misaligned`` / ``neutral`` — a
    total function with no exceptions and no missing combinations — and the
    returned value matches the design's alignment table.

Validates: Requirements 1.2.

The design's alignment table (total over Options_Bias_State x proposed_direction)
is:

    bullish + BUY                                   -> aligned
    bullish + SELL                                  -> misaligned
    bearish + BUY                                   -> misaligned
    bearish + SELL                                  -> aligned
    neutral / HOLD / absent / unrecognized          -> neutral

``derive_alignment`` normalises the proposed direction case-insensitively and
trims surrounding whitespace, so ``"buy"`` / ``" BUY "`` / ``"Sell"`` are treated
as their canonical verbs while any other string (including ``""`` and arbitrary
free text), ``None``, and non-string values are non-directional -> ``neutral``.
Any unrecognized bias state likewise collapses to ``neutral``.

This test generates the full cross product of the bias states (the three valid
states plus garbage) and a wide range of direction inputs (the directional verbs
in several case variants, ``HOLD``, the absent marker ``None``, the empty string,
non-string values, and arbitrary free text) and asserts the derivation always
returns exactly one of the three Alignment values, never raises (totality), and
equals the value the design table dictates. The sys.path / import pattern mirrors
the sibling ``test_*_properties.py`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options_bias.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options_bias import derive_alignment  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Input space: every Options_Bias_State (the three valid states plus garbage) x a
# broad range of proposed directions (directional verbs in several case/whitespace
# variants, the non-directional HOLD, the absent marker None, the empty string,
# non-string values, and arbitrary free text).
# ─────────────────────────────────────────────────────────────────────────────

_BIAS_STATES = st.one_of(
    st.sampled_from(["bullish", "bearish", "neutral"]),
    # Garbage / unrecognized bias states must collapse to neutral, never raise.
    st.sampled_from(["", "BULLISH", "Bearish", "sideways", "unknown", None, 42]),
    st.text(),
)

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
            123,  # non-string -> neutral
        ]
    ),
    st.none(),
    st.text(),
)

_ALIGNMENT_VALUES = {"aligned", "misaligned", "neutral"}


def _expected_alignment(options_bias_state, proposed_direction):
    """Independent recomputation of the Alignment per the design table."""
    direction = (
        proposed_direction.strip().upper()
        if isinstance(proposed_direction, str)
        else ""
    )
    if options_bias_state == "bullish":
        if direction == "BUY":
            return "aligned"
        if direction == "SELL":
            return "misaligned"
        return "neutral"
    if options_bias_state == "bearish":
        if direction == "BUY":
            return "misaligned"
        if direction == "SELL":
            return "aligned"
        return "neutral"
    # neutral bias or any unrecognized bias state -> neutral
    return "neutral"


# ─────────────────────────────────────────────────────────────────────────────
# Property 2 (task 2.3): Alignment is a total function of (bias state x direction)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 2: Alignment is a total function of (bias state × proposed direction)
@settings(max_examples=300, deadline=None)
@given(
    options_bias_state=_BIAS_STATES,
    proposed_direction=_PROPOSED_DIRECTION,
)
def test_property_2_alignment_is_a_total_function(
    options_bias_state, proposed_direction
):
    """Feature: options-agent-integration, Property 2: Alignment is a total
    function of (bias state × proposed direction).

    For every (Options_Bias_State x proposed_direction) combination,
    ``derive_alignment`` returns exactly one value drawn from
    ``{aligned, misaligned, neutral}``, never raises (totality), and equals the
    value the design's alignment table dictates.

    Validates: Requirements 1.2
    """
    # Totality: the derivation must never raise for any input combination.
    alignment = derive_alignment(options_bias_state, proposed_direction)

    # The result is exactly one well-formed Alignment value.
    assert alignment in _ALIGNMENT_VALUES, (
        f"derive_alignment({options_bias_state!r}, {proposed_direction!r}) returned "
        f"{alignment!r}, which is not one of {sorted(_ALIGNMENT_VALUES)}"
    )

    # The value matches the design's alignment table.
    expected = _expected_alignment(options_bias_state, proposed_direction)
    assert alignment == expected, (
        f"derive_alignment({options_bias_state!r}, {proposed_direction!r}) = "
        f"{alignment!r} != table-dictated {expected!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Exhaustive enumeration: the full, finite cross product of the bias states
# (valid + garbage) and the recognised/representative directions maps to exactly
# one Alignment with no missing combination (the "no missing combinations" half
# of Property 2).
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 2: Alignment is a total function of (bias state × proposed direction)
def test_property_2_exhaustive_cross_product_is_total():
    """Feature: options-agent-integration, Property 2: Alignment is a total
    function of (bias state × proposed direction).

    Enumerates the full cross product of the bias states (the three valid states
    plus garbage) and a representative set of direction inputs (directional verbs
    with case/whitespace variants, HOLD, the empty string, an arbitrary string, a
    non-string, and None) and asserts every combination yields exactly one
    Alignment matching the design table — proving there are no missing
    combinations.

    Validates: Requirements 1.2
    """
    states = ["bullish", "bearish", "neutral", "", "sideways", None, 42]
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
    assert derive_alignment("bullish", "BUY") == "aligned"
    assert derive_alignment("bullish", "SELL") == "misaligned"
    assert derive_alignment("bearish", "BUY") == "misaligned"
    assert derive_alignment("bearish", "SELL") == "aligned"
    # Neutral bias is always neutral; HOLD / absent / garbage is always neutral.
    assert derive_alignment("neutral", "BUY") == "neutral"
    assert derive_alignment("neutral", "SELL") == "neutral"
    assert derive_alignment("bullish", "HOLD") == "neutral"
    assert derive_alignment("bearish", None) == "neutral"
    assert derive_alignment("sideways", "BUY") == "neutral"
