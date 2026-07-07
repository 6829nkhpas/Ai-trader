"""Property-based test for contract identity on conforming results/markers (tools.py, task 4.8).

Feature: earnings-event-risk-gate

This Hypothesis property exercises ``validate_contract``'s ``get_event_risk``
branch:

  * Property 15 (4.6, 4.8) — ``validate_contract`` is the identity on conforming
    results and markers: for any generated conforming ``get_event_risk``
    Event_Assessment, and for any Unavailable_Marker, ``validate_contract(
    "get_event_risk", result)`` returns that result unchanged.

A conforming Event_Assessment carries an ``event_risk`` drawn from the fixed
EVENT_RISK_STATES enum, an ``event_recommendation`` drawn from the fixed
EVENT_RECOMMENDATIONS enum, a ``days_until_event`` present as a finite number or
``null`` (null when no day count is available), and an ``event_date`` string
identifying the reference Scheduled_Event date. An Unavailable_Marker carries
``{"unavailable": true, "reason": ...}`` and (per AD-4) omits the full-label
fields.

The test asserts the call never raises and returns the *same object* unchanged
(both object identity and deep equality), pinning the contract's pass-through
behavior across the full conforming input space.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from tools import (  # noqa: E402
    EVENT_RECOMMENDATIONS,
    EVENT_RISK_STATES,
    validate_contract,
)

# ── Generators ────────────────────────────────────────────────────────────────

# days_until_event must be a finite number or null (the contract uses
# ``_is_number_or_null``). Bools are excluded because ``_is_number`` rejects them.
# The documented domain is a non-negative day count or null, but the contract only
# requires finite-number-or-null; generate the broader finite-or-null space so the
# identity holds across every conforming value.
_finite_number = st.floats(allow_nan=False, allow_infinity=False)
_days_until_event = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=1_000),
    st.floats(min_value=0.0, max_value=1_000.0, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-1_000, max_value=1_000),
    _finite_number,
)

# event_date is any string per the contract (``isinstance(..., str)``).
_event_date = st.one_of(
    st.text(min_size=0, max_size=32),
    st.sampled_from(["2024-01-31", "2025-07-15", "", "N/A", "2024-11-20"]),
)

_symbol = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90), min_size=1, max_size=8
)
_holding_horizon = st.sampled_from(["intraday", "swing", "positional", "scalp"])


@st.composite
def _conforming_assessment(draw):
    """A conforming ``get_event_risk`` Event_Assessment.

    ``event_risk`` and ``event_recommendation`` are drawn from their fixed enums,
    ``days_until_event`` is a finite number or null, and ``event_date`` is a
    string, so the assessment satisfies the contract that ``validate_contract``
    enforces. Extra optional fields (symbol / holding_horizon) are included to
    confirm they are passed through untouched.
    """
    assessment = {
        "event_risk": draw(st.sampled_from(sorted(EVENT_RISK_STATES))),
        "event_recommendation": draw(st.sampled_from(sorted(EVENT_RECOMMENDATIONS))),
        "days_until_event": draw(_days_until_event),
        "event_date": draw(_event_date),
    }
    # Optional context fields present on real assessments — pass-through must not
    # touch them either.
    if draw(st.booleans()):
        assessment["symbol"] = draw(_symbol)
    if draw(st.booleans()):
        assessment["holding_horizon"] = draw(_holding_horizon)
    return assessment


@st.composite
def _unavailable_marker(draw):
    """An Unavailable_Marker ({"unavailable": true, "reason": ...}).

    Per AD-4 the marker omits the full Event_Assessment label fields; it is
    recognized as an honest non-fatal result and must pass through unchanged.
    """
    marker = {
        "unavailable": True,
        "reason": draw(
            st.text(min_size=0, max_size=80)
            | st.sampled_from(
                [
                    "no scheduled event date configured",
                    "event calendar retrieval failed",
                    "invalid event date: expected an ISO date string",
                    "no upcoming event within lookahead window",
                ]
            )
        ),
    }
    # Optional context that a marker may still carry.
    if draw(st.booleans()):
        marker["symbol"] = draw(_symbol)
    if draw(st.booleans()):
        marker["holding_horizon"] = draw(_holding_horizon)
    return marker


_conforming_result = st.one_of(_conforming_assessment(), _unavailable_marker())


# ─────────────────────────────────────────────────────────────────────────────
# Property 15: validate_contract is the identity on conforming results & markers
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 15: validate_contract is the identity on conforming results and markers
@settings(max_examples=25, deadline=None)
@given(result=_conforming_result)
def test_property_15_validate_contract_is_identity_on_conforming_event(result):
    """Validates: Requirements 4.6, 4.8

    For any conforming Event_Assessment or any Unavailable_Marker,
    ``validate_contract("get_event_risk", result)`` returns that result
    unchanged (object identity + deep equality) and never raises.
    """
    # Snapshot for an after-the-fact equality check (defends against any
    # accidental mutation of the input by the validator).
    import copy

    snapshot = copy.deepcopy(result)

    try:
        returned = validate_contract("get_event_risk", result)
    except Exception as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"validate_contract raised {exc!r} on a conforming result/marker"
        )

    # Identity: the exact same object is passed through (the branch returns
    # ``payload`` unchanged; the marker path returns it via _has_honest_marker).
    assert returned is result, "validate_contract did not return the same object"

    # It is not flagged as a violation.
    assert not (
        isinstance(returned, dict) and "contract_violation" in returned
    ), "conforming result/marker was incorrectly flagged as a contract violation"

    # Unchanged: the returned object equals the pre-call snapshot.
    assert returned == snapshot, "validate_contract altered the input result"
