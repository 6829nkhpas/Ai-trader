"""Property-based test for total favorability derivation (regime.py, task 3.6).

Feature: regime-detection-gate

This Hypothesis property exercises :func:`regime.derive_favorability`, asserting
that Favorability is a *total* function of (Trend_State, Volatility_State):

  * Property 6 (1.10) — for any combination of a Trend_State value and a
                        Volatility_State value, ``derive_favorability`` returns
                        exactly one value drawn from ``FAVORABILITY_VALUES``
                        (favorable / unfavorable / neutral), so every one of the
                        nine valid combinations maps to exactly one Favorability,
                        and the derivation is deterministic. The full nine-cell
                        mapping is asserted against the design table.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from regime import (  # noqa: E402
    FAVORABILITY_VALUES,
    TREND_STATES,
    VOLATILITY_STATES,
    derive_favorability,
    resolve_regime_config,
)

# The Favorability derivation depends only on the two state strings (not on any
# threshold), but the signature takes a config; resolve a single deterministic
# one from the (cleared-or-not) environment and reuse it for every example.
_CONFIG = resolve_regime_config()

# The nine-cell Favorability mapping transcribed directly from the design's
# "Favorability derivation (total mapping over Trend_State x Volatility_State)"
# table. This is the authoritative expectation the property checks against.
#
#   Trend_State \ Vol_State |  low      | normal      | high
#   trending                |  neutral  | favorable   | unfavorable
#   ranging                 |  unfav.   | unfavorable | unfavorable
#   transitional            |  neutral  | neutral     | unfavorable
_EXPECTED_MAPPING = {
    ("trending", "low"): "neutral",
    ("trending", "normal"): "favorable",
    ("trending", "high"): "unfavorable",
    ("ranging", "low"): "unfavorable",
    ("ranging", "normal"): "unfavorable",
    ("ranging", "high"): "unfavorable",
    ("transitional", "low"): "neutral",
    ("transitional", "normal"): "neutral",
    ("transitional", "high"): "unfavorable",
}

# Valid states drawn from the module's enumerations, plus arbitrary strings to
# confirm the function is total for *all* inputs (unrecognized pairs must still
# return a single valid Favorability rather than raising or returning garbage).
_trend = st.sampled_from(TREND_STATES)
_vol = st.sampled_from(VOLATILITY_STATES)
_arbitrary = st.text(max_size=12)
_trend_or_garbage = st.one_of(_trend, _arbitrary)
_vol_or_garbage = st.one_of(_vol, _arbitrary)


# ─────────────────────────────────────────────────────────────────────────────
# Property 6 (1.10): Favorability is a total function of the two states
# ─────────────────────────────────────────────────────────────────────────────

@settings(max_examples=300)
@given(trend_state=_trend_or_garbage, volatility_state=_vol_or_garbage)
def test_property_6_favorability_is_total(trend_state, volatility_state):
    # Feature: regime-detection-gate, Property 6
    """Feature: regime-detection-gate, Property 6: Favorability is a total
    function of Trend_State and Volatility_State — for any combination of a
    Trend_State value and a Volatility_State value, ``derive_favorability``
    returns exactly one value from ``FAVORABILITY_VALUES`` and is deterministic.

    Validates: Requirements 1.10
    """
    result = derive_favorability(trend_state, volatility_state, _CONFIG)

    # Totality: every input maps to exactly one value of the Favorability
    # enumeration (a single, well-formed string — never None, never an exception).
    assert result in FAVORABILITY_VALUES

    # Determinism: repeated derivation with identical inputs is identical.
    again = derive_favorability(trend_state, volatility_state, _CONFIG)
    assert again == result

    # When BOTH states are valid enumeration members, the result must match the
    # authoritative nine-cell design mapping exactly.
    if trend_state in TREND_STATES and volatility_state in VOLATILITY_STATES:
        assert result == _EXPECTED_MAPPING[(trend_state, volatility_state)]


def test_property_6_full_nine_cell_mapping():
    # Feature: regime-detection-gate, Property 6
    """Exhaustively assert all nine valid (Trend_State x Volatility_State)
    combinations map to exactly the Favorability dictated by the design table,
    confirming the mapping is total and covers every cell.

    Validates: Requirements 1.10
    """
    covered = set()
    for trend_state in TREND_STATES:
        for volatility_state in VOLATILITY_STATES:
            result = derive_favorability(trend_state, volatility_state, _CONFIG)
            assert result in FAVORABILITY_VALUES
            assert result == _EXPECTED_MAPPING[(trend_state, volatility_state)]
            covered.add((trend_state, volatility_state))

    # All nine cells were exercised exactly once.
    assert len(covered) == len(TREND_STATES) * len(VOLATILITY_STATES) == 9
