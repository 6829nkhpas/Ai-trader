"""Property-based test for session contract rejection (tools.py, task 4.7).

Feature: session-expiry-awareness

This Hypothesis property exercises ``validate_contract``'s ``get_session_context``
branch for design Property 13: for any conforming ``get_session_context``
Session_Label mutated to violate the Tool_Result_Contract — an out-of-enum
``session_phase`` or ``time_favorability``, a non-numeric/non-null minutes field,
a missing or malformed ``expiry_context``, a non-boolean ``is_expiry_day``, or a
non-numeric ``days_until_expiry`` — ``validate_contract`` returns a structured
``{"error", "contract_violation"}`` result whose violation message identifies the
offending field.

The generator starts from a fully conforming Session_Label and applies exactly
ONE contract-violating mutation, recording the offending field name so the
property can assert the violation message mentions it. No mutation introduces an
honest ``error`` / ``unavailable`` marker (the only keys touched are the session
fields), so each mutated payload genuinely reaches the session contract branch as
a (broken) label rather than being passed through as a graceful-degradation
result by ``_has_honest_marker``.

Note on minutes / days mutations: the contract accepts any finite-or-not real
number for these fields (``_is_number`` admits NaN / ±inf), so a genuine
violation must replace the value with a *non-numeric, non-null* value (a string,
bool, list, or dict). Using NaN / ±inf would be accepted by the contract and is
therefore deliberately avoided here.
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
    SESSION_PHASES,
    TIME_FAVORABILITY,
    validate_contract,
)

# ── Generators ────────────────────────────────────────────────────────────────

_timeframe = st.sampled_from(sorted({"1m", "5m", "10m", "15m", "1h", "4h", "1d"}))
_symbol = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90), min_size=1, max_size=8
)

# A conforming minutes value: a finite non-negative number or null.
_minutes_value = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=1_000_000),
    st.floats(min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False),
)

# The union of every categorical enum value — an out-of-enum string must avoid
# all of these so the chosen enum field is genuinely non-conforming.
_ALL_ENUM_VALUES = set(SESSION_PHASES) | set(TIME_FAVORABILITY)
_out_of_enum_strings = st.text(min_size=0, max_size=12).filter(
    lambda s: s not in _ALL_ENUM_VALUES
)

# Non-numeric, non-null values — these genuinely violate `_is_number_or_null`
# (minutes) and `_is_number` (days_until_expiry). Bools are non-numeric under the
# contract, alongside strings, lists, and dicts.
_non_numeric_values = st.one_of(
    st.text(min_size=0, max_size=8),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)

# Non-boolean values for corrupting `is_expiry_day`.
_non_boolean_values = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(min_size=0, max_size=6),
    st.none(),
    st.lists(st.integers(), max_size=3),
)

# Non-object values for corrupting the whole `expiry_context`.
_non_object_values = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(min_size=0, max_size=6),
    st.none(),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
)


@st.composite
def _conforming_label(draw):
    """A fully conforming ``get_session_context`` Session_Label."""
    return {
        "session_phase": draw(st.sampled_from(sorted(SESSION_PHASES))),
        "minutes_since_open": draw(_minutes_value),
        "minutes_until_close": draw(_minutes_value),
        "expiry_context": {
            "is_expiry_day": draw(st.booleans()),
            "days_until_expiry": draw(st.integers(min_value=0, max_value=6)),
        },
        "time_favorability": draw(st.sampled_from(sorted(TIME_FAVORABILITY))),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }


@st.composite
def _mutated_label(draw):
    """A conforming Session_Label with exactly one contract violation.

    Returns ``(payload, offending_field)`` where ``offending_field`` is the name
    the resulting ``contract_violation`` message must mention.
    """
    payload = draw(_conforming_label())

    kind = draw(
        st.sampled_from(
            [
                "bad_session_phase",
                "bad_time_favorability",
                "non_numeric_minutes_since_open",
                "non_numeric_minutes_until_close",
                "missing_expiry_context",
                "non_object_expiry_context",
                "non_boolean_is_expiry_day",
                "non_numeric_days_until_expiry",
            ]
        )
    )

    if kind == "bad_session_phase":
        payload["session_phase"] = draw(_out_of_enum_strings)
        return payload, "session_phase"

    if kind == "bad_time_favorability":
        payload["time_favorability"] = draw(_out_of_enum_strings)
        return payload, "time_favorability"

    if kind == "non_numeric_minutes_since_open":
        payload["minutes_since_open"] = draw(_non_numeric_values)
        return payload, "minutes_since_open"

    if kind == "non_numeric_minutes_until_close":
        payload["minutes_until_close"] = draw(_non_numeric_values)
        return payload, "minutes_until_close"

    if kind == "missing_expiry_context":
        del payload["expiry_context"]
        return payload, "expiry_context"

    if kind == "non_object_expiry_context":
        payload["expiry_context"] = draw(_non_object_values)
        return payload, "expiry_context"

    if kind == "non_boolean_is_expiry_day":
        payload["expiry_context"]["is_expiry_day"] = draw(_non_boolean_values)
        return payload, "is_expiry_day"

    # kind == "non_numeric_days_until_expiry"
    payload["expiry_context"]["days_until_expiry"] = draw(_non_numeric_values)
    return payload, "days_until_expiry"


# ─────────────────────────────────────────────────────────────────────────────
# Property 13: validate_contract rejects non-conforming results, naming the field
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 13: validate_contract rejects non-conforming results, naming the field
@settings(max_examples=200, deadline=None)
@given(mutated=_mutated_label())
def test_property_13_session_contract_rejection_names_offending_field(mutated):
    """Validates: Requirements 4.7

    For any conforming get_session_context Session_Label mutated by a single
    contract violation (an out-of-enum session_phase / time_favorability, a
    non-numeric/non-null minutes field, a missing or malformed expiry_context, a
    non-boolean is_expiry_day, or a non-numeric days_until_expiry),
    validate_contract returns a structured {"error", "contract_violation"} result
    whose violation message identifies the offending field. Never raises, and
    never passes a broken label through as an honest marker.
    """
    payload, offending_field = mutated

    assume(offending_field is not None)

    # Sanity: the mutated payload must not look like an honest error/unavailable
    # marker, otherwise it would (correctly) be passed through rather than
    # rejected. The mutations only touch session fields, so this holds.
    assume("error" not in payload)
    assume(payload.get("unavailable") is not True)

    # Must never raise — contract failures are data, not exceptions.
    try:
        result = validate_contract("get_session_context", payload)
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
