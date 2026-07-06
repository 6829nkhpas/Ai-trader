# Feature: earnings-event-risk-gate, Property 6: Event_Recommendation is total and tightening-only
"""Property-based test for Event_Recommendation totality and tightening-only
range (events.py, task 2.8).

Feature: earnings-event-risk-gate

This module implements design **Property 6: Event_Recommendation is total and
tightening-only**:

    ``derive_event_recommendation(event_risk, holding_horizon)`` is a *total*
    function — defined for EVERY ``event_risk`` (including unrecognized garbage)
    and BOTH recognized Holding_Horizons (and unrecognized horizons) — whose
    output is ALWAYS one of the fixed four-value tightening-only set
    ``{proceed, size_down, shorten_horizon, stand_aside}``. It never recommends
    increasing size, loosening risk, or entering a trade.

    The exhaustive mapping for recognized inputs is:

      * ``clear``                          -> ``proceed``
      * ``imminent``                       -> ``size_down``       (any horizon)
      * ``through_event`` + ``multi_session`` -> ``shorten_horizon``
      * ``through_event`` + ``intraday``      -> ``stand_aside``

    Any unrecognized ``event_risk`` collapses to ``proceed`` (no tightening
    asserted), keeping the recommendation tightening-only and the function total.

Validates: Requirements 2.7, 12.2.

To make the totality and the tightening-only range concrete, the expected value
for recognized inputs is computed by an INDEPENDENT reference implementation
(``_expected_recommendation``) that re-derives the design mapping table here
rather than calling the implementation's helper — that is what makes this a real
check that the implementation matches the specified mapping.

The sys.path / import pattern mirrors the sibling ``test_event_*`` and
``test_session_*`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from events import (  # noqa: E402
    EVENT_REC_PROCEED,
    EVENT_REC_SIZE_DOWN,
    EVENT_REC_SHORTEN_HORIZON,
    EVENT_REC_STAND_ASIDE,
    EVENT_RISK_CLEAR,
    EVENT_RISK_IMMINENT,
    EVENT_RISK_THROUGH_EVENT,
    derive_event_recommendation,
)

# The complete, fixed four-value tightening-only Event_Recommendation set
# (Requirements 2.7, 12.2). Nothing outside this set may ever be returned, and
# in particular none of these loosen risk or enter a trade.
TIGHTENING_ONLY = {
    EVENT_REC_PROCEED,
    EVENT_REC_SIZE_DOWN,
    EVENT_REC_SHORTEN_HORIZON,
    EVENT_REC_STAND_ASIDE,
}

# The three recognized Event_Risk values.
_RECOGNIZED_RISKS = [
    EVENT_RISK_CLEAR,
    EVENT_RISK_IMMINENT,
    EVENT_RISK_THROUGH_EVENT,
]

# The two recognized Holding_Horizons.
_HORIZONS = ["intraday", "multi_session"]


# ─────────────────────────────────────────────────────────────────────────────
# Independent reference implementation of the design's mapping table.
# ─────────────────────────────────────────────────────────────────────────────


def _expected_recommendation(event_risk: str, holding_horizon: str) -> str:
    """Re-derive the Event_Recommendation from the design mapping table
    (Requirements 2.7, 12.2):

        clear                          -> proceed
        imminent                       -> size_down       (any horizon)
        through_event + intraday       -> stand_aside
        through_event + <other>        -> shorten_horizon
        <unrecognized risk>            -> proceed          (no tightening)
    """
    if event_risk == EVENT_RISK_IMMINENT:
        return EVENT_REC_SIZE_DOWN
    if event_risk == EVENT_RISK_THROUGH_EVENT:
        if holding_horizon == "intraday":
            return EVENT_REC_STAND_ASIDE
        return EVENT_REC_SHORTEN_HORIZON
    # clear and any unrecognized risk -> no tightening required.
    return EVENT_REC_PROCEED


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Arbitrary "garbage" values for event_risk / holding_horizon: unrecognized
# strings plus non-string types, so totality is exercised across the whole
# domain (not just the recognized enum).
_GARBAGE = st.one_of(
    st.text(),
    st.sampled_from(["", "CLEAR", "Imminent", "unknown", "proceed", "buy", "sell"]),
    st.none(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
)

# Any event_risk: recognized values OR garbage.
_ANY_RISK = st.one_of(st.sampled_from(_RECOGNIZED_RISKS), _GARBAGE)

# Any holding_horizon: recognized values OR garbage.
_ANY_HORIZON = st.one_of(st.sampled_from(_HORIZONS), _GARBAGE)


# ─────────────────────────────────────────────────────────────────────────────
# Property 6: Event_Recommendation is total and tightening-only
# ─────────────────────────────────────────────────────────────────────────────


# Feature: earnings-event-risk-gate, Property 6: Event_Recommendation is total and tightening-only
@settings(max_examples=400, deadline=None)
@given(event_risk=_ANY_RISK, holding_horizon=_ANY_HORIZON)
def test_property_6_recommendation_total_and_tightening_only(event_risk, holding_horizon):
    """Validates: Requirements 2.7, 12.2

    For EVERY event_risk (recognized or garbage) and EVERY holding_horizon
    (recognized or garbage), ``derive_event_recommendation`` returns exactly one
    of the fixed four-value tightening-only set and never raises — establishing
    totality and the tightening-only range.
    """
    rec = derive_event_recommendation(event_risk, holding_horizon)

    # Totality + tightening-only range (Requirements 2.7, 12.2): always one of
    # the fixed four values, never anything that increases size / loosens /
    # enters a trade.
    assert rec in TIGHTENING_ONLY, (
        f"recommendation {rec!r} outside the tightening-only set for "
        f"event_risk={event_risk!r}, holding_horizon={holding_horizon!r}"
    )


# Feature: earnings-event-risk-gate, Property 6: Event_Recommendation is total and tightening-only
@settings(max_examples=200, deadline=None)
@given(event_risk=st.sampled_from(_RECOGNIZED_RISKS), holding_horizon=st.sampled_from(_HORIZONS))
def test_property_6_recommendation_matches_mapping_for_recognized_inputs(event_risk, holding_horizon):
    """Validates: Requirements 2.7, 12.2

    For every recognized (event_risk, holding_horizon) pair, the recommendation
    equals the value dictated by the design mapping table (an independent
    re-derivation): ``clear`` -> proceed; ``imminent`` -> size_down;
    ``through_event`` + ``multi_session`` -> shorten_horizon; ``through_event``
    + ``intraday`` -> stand_aside.
    """
    rec = derive_event_recommendation(event_risk, holding_horizon)

    expected = _expected_recommendation(event_risk, holding_horizon)
    assert rec == expected, (
        f"recommendation mismatch for event_risk={event_risk!r} "
        f"holding_horizon={holding_horizon!r}: got {rec!r}, expected {expected!r}"
    )

    # Spell out the acceptance-criteria specifics.
    if event_risk == EVENT_RISK_CLEAR:
        assert rec == EVENT_REC_PROCEED
    if event_risk == EVENT_RISK_IMMINENT:
        assert rec == EVENT_REC_SIZE_DOWN
    if event_risk == EVENT_RISK_THROUGH_EVENT and holding_horizon == "multi_session":
        assert rec == EVENT_REC_SHORTEN_HORIZON
    if event_risk == EVENT_RISK_THROUGH_EVENT and holding_horizon == "intraday":
        assert rec == EVENT_REC_STAND_ASIDE


# Feature: earnings-event-risk-gate, Property 6: Event_Recommendation is total and tightening-only
@settings(max_examples=200, deadline=None)
@given(event_risk=_ANY_RISK, holding_horizon=_ANY_HORIZON)
def test_property_6_unrecognized_risk_never_tightens_beyond_proceed(event_risk, holding_horizon):
    """Validates: Requirements 2.7, 12.2

    An unrecognized event_risk must never fabricate a tightening action: it
    collapses to ``proceed`` (no tightening asserted). Recognized-risk inputs are
    left to the mapping property above; here we assert the fallback behaviour for
    anything outside the recognized three-value enum.
    """
    if event_risk in _RECOGNIZED_RISKS:
        return  # covered by the mapping property

    rec = derive_event_recommendation(event_risk, holding_horizon)
    assert rec == EVENT_REC_PROCEED, (
        f"unrecognized event_risk={event_risk!r} should collapse to proceed, "
        f"got {rec!r}"
    )
