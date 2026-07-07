"""Property-based test for event contract rejection (tools.py, task 4.9).

Feature: earnings-event-risk-gate

This Hypothesis property exercises ``validate_contract``'s ``get_event_risk``
branch for design Property 16: for any conforming ``get_event_risk``
Event_Assessment mutated to violate the Tool_Result_Contract — an out-of-enum
``event_risk`` or ``event_recommendation``, a non-numeric/non-null
``days_until_event``, or a missing/non-string ``event_date`` — ``validate_contract``
returns a structured ``{"error", "contract_violation"}`` result whose violation
message identifies the offending field.

The generator starts from a fully conforming Event_Assessment and applies exactly
ONE contract-violating mutation, recording the offending field name so the
property can assert the violation message mentions it. No mutation introduces an
honest ``error`` / ``unavailable`` marker (the only keys touched are the event
assessment fields), so each mutated payload genuinely reaches the event contract
branch as a (broken) assessment rather than being passed through as a
graceful-degradation result by ``_has_honest_marker``.

Note on the days_until_event mutation: the contract accepts any finite-or-not
real number for this field (``_is_number_or_null`` admits NaN / ±inf), so a
genuine violation must replace the value with a *non-numeric, non-null* value (a
string, bool, list, or dict). Using NaN / ±inf would be accepted by the contract
and is therefore deliberately avoided here.
"""

import os
import sys

from hypothesis import assume, given, settings
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

# A conforming days_until_event value: a finite number or null.
_days_value = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=3650),
    st.floats(min_value=0.0, max_value=3650.0, allow_nan=False, allow_infinity=False),
)

# A conforming event_date string (the exact format is unconstrained by the
# contract — it need only be a string).
_event_date = st.text(min_size=1, max_size=10)

# The union of every categorical enum value — an out-of-enum string must avoid
# all of these so the chosen enum field is genuinely non-conforming.
_ALL_ENUM_VALUES = set(EVENT_RISK_STATES) | set(EVENT_RECOMMENDATIONS)
_out_of_enum_strings = st.text(min_size=0, max_size=12).filter(
    lambda s: s not in _ALL_ENUM_VALUES
)

# Non-numeric, non-null values — these genuinely violate `_is_number_or_null`
# for days_until_event. Bools are non-numeric under the contract, alongside
# strings, lists, and dicts.
_non_numeric_values = st.one_of(
    st.text(min_size=0, max_size=8),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)

# Non-string values for corrupting `event_date`.
_non_string_values = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.none(),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)


@st.composite
def _conforming_assessment(draw):
    """A fully conforming ``get_event_risk`` Event_Assessment."""
    return {
        "event_risk": draw(st.sampled_from(sorted(EVENT_RISK_STATES))),
        "event_recommendation": draw(st.sampled_from(sorted(EVENT_RECOMMENDATIONS))),
        "days_until_event": draw(_days_value),
        "event_date": draw(_event_date),
    }


@st.composite
def _mutated_assessment(draw):
    """A conforming Event_Assessment with exactly one contract violation.

    Returns ``(payload, offending_field)`` where ``offending_field`` is the name
    the resulting ``contract_violation`` message must mention.
    """
    payload = draw(_conforming_assessment())

    kind = draw(
        st.sampled_from(
            [
                "bad_event_risk",
                "bad_event_recommendation",
                "non_numeric_days_until_event",
                "missing_event_date",
                "non_string_event_date",
            ]
        )
    )

    if kind == "bad_event_risk":
        payload["event_risk"] = draw(_out_of_enum_strings)
        return payload, "event_risk"

    if kind == "bad_event_recommendation":
        payload["event_recommendation"] = draw(_out_of_enum_strings)
        return payload, "event_recommendation"

    if kind == "non_numeric_days_until_event":
        payload["days_until_event"] = draw(_non_numeric_values)
        return payload, "days_until_event"

    if kind == "missing_event_date":
        del payload["event_date"]
        return payload, "event_date"

    # kind == "non_string_event_date"
    payload["event_date"] = draw(_non_string_values)
    return payload, "event_date"


# ─────────────────────────────────────────────────────────────────────────────
# Property 16: validate_contract rejects non-conforming results, naming the field
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 16: validate_contract rejects non-conforming results, naming the field
@settings(max_examples=25, deadline=None)
@given(mutated=_mutated_assessment())
def test_property_16_event_contract_rejection_names_offending_field(mutated):
    """Validates: Requirements 4.7

    For any conforming get_event_risk Event_Assessment mutated by a single
    contract violation (an out-of-enum event_risk / event_recommendation, a
    non-numeric/non-null days_until_event, or a missing/non-string event_date),
    validate_contract returns a structured {"error", "contract_violation"} result
    whose violation message identifies the offending field. Never raises, and
    never passes a broken assessment through as an honest marker.
    """
    payload, offending_field = mutated

    assume(offending_field is not None)

    # Sanity: the mutated payload must not look like an honest error/unavailable
    # marker, otherwise it would (correctly) be passed through rather than
    # rejected. The mutations only touch event assessment fields, so this holds.
    assume("error" not in payload)
    assume(payload.get("unavailable") is not True)

    # Must never raise — contract failures are data, not exceptions.
    try:
        result = validate_contract("get_event_risk", payload)
    except Exception as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"validate_contract raised {exc!r} instead of returning a "
            f"structured contract-violation result for {payload!r}"
        )

    # The result is a structured contract-violation dict.
    assert isinstance(result, dict), (
        f"expected a dict result, got {type(result).__name__}: {result!r}"
    )
    assert "error" in result, f"violation result missing 'error' key: {result!r}"
    assert "contract_violation" in result, (
        f"violation result missing 'contract_violation' key: {result!r}"
    )

    # The violation message identifies the offending field by name.
    violation = result["contract_violation"]
    assert isinstance(violation, str), (
        f"'contract_violation' is not a string: {violation!r}"
    )
    assert offending_field in violation, (
        f"violation message does not name offending field "
        f"{offending_field!r}: {violation!r}"
    )
