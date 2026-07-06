"""Property-based test for contract rejection naming the field (tools.py, task 6.8).

Feature: order-flow-context

This Hypothesis property exercises ``validate_contract`` from ``tools.py`` for the
``get_order_flow`` Tool_Result_Contract. It covers design Property 19: for any
``get_order_flow`` result mutated to violate the contract — a bad
``order_flow_state``, a bad ``alignment``, a missing/non-object ``measures``, a
measure that is neither a number nor null, a missing/non-number ``tick_ofi``, a
non-boolean ``live_tick_contributed``, or a non-dict payload — ``validate_contract``
returns a structured ``{"error", "contract_violation"}`` result whose violation
message identifies the offending field.

The generator starts from a fully conforming Order_Flow_Label (``order_flow_state``
and ``alignment`` each drawn from their fixed enums, a ``measures`` mapping
carrying all named proxy measures as finite-number-or-null, a finite-number-or-null
``tick_ofi``, and a boolean ``live_tick_contributed``) and then applies exactly ONE
contract-violating mutation, recording the name of the offending field so the
property can assert the violation message mentions it. No mutation introduces an
honest ``error``/``unavailable`` marker (note that payloads carrying an ``error``
key are passed through by ``_has_honest_marker``), so each mutated payload genuinely
reaches contract validation as a (broken) label rather than being passed through as
a graceful-degradation result.
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
    ALIGNMENT_VALUES,
    ORDER_FLOW_STATES,
    _OF_MEASURE_FIELDS,
    validate_contract,
)

# The union of the two categorical enums — an out-of-enum string must avoid all
# of these so the chosen state field is genuinely non-conforming.
_ALL_ENUM_VALUES = set(ORDER_FLOW_STATES) | set(ALIGNMENT_VALUES)

# Finite numbers (and null) are the only conforming measure / tick_ofi values.
_finite_numbers = st.floats(
    allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6
)
_measure_values = st.one_of(_finite_numbers, st.none())

# Strings that are NOT any categorical enum value — used for out-of-enum states.
_out_of_enum_strings = st.text(min_size=0, max_size=12).filter(
    lambda s: s not in _ALL_ENUM_VALUES
)

# Non-numeric / non-null values for corrupting a measure or tick_ofi (bools are
# explicitly non-numeric under the contract, alongside strings, lists, and dicts).
_non_numeric_values = st.one_of(
    st.text(min_size=0, max_size=8),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)

# Non-boolean values for corrupting live_tick_contributed.
_non_boolean_values = st.one_of(
    st.text(min_size=0, max_size=8),
    st.integers(),
    _finite_numbers,
    st.none(),
    st.lists(st.integers(), max_size=3),
)

# Non-dict (and non-marker) payloads — these never carry an honest marker, so
# they reach the "expected an object" branch of validate_contract.
_non_dict_payloads = st.one_of(
    st.integers(),
    _finite_numbers,
    st.text(min_size=0, max_size=8),
    st.none(),
    st.lists(st.integers(), max_size=3),
)


@st.composite
def _conforming_label(draw):
    """A fully conforming get_order_flow Order_Flow_Label."""
    return {
        "order_flow_state": draw(st.sampled_from(sorted(ORDER_FLOW_STATES))),
        "alignment": draw(st.sampled_from(sorted(ALIGNMENT_VALUES))),
        "measures": {field: draw(_measure_values) for field in _OF_MEASURE_FIELDS},
        "tick_ofi": draw(_measure_values),
        "live_tick_contributed": draw(st.booleans()),
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "candles_used": 120,
    }


@st.composite
def _mutated_label(draw):
    """A conforming Order_Flow_Label with exactly one contract-violating mutation.

    Returns ``(payload, offending_field)`` where ``offending_field`` is the name
    that the resulting ``contract_violation`` message must mention.
    """
    measure_fields = list(_OF_MEASURE_FIELDS)

    kind = draw(
        st.sampled_from(
            [
                "out_of_enum",
                "missing_field",
                "non_numeric_measure",
                "bad_tick_ofi",
                "non_boolean_flag",
                "non_dict",
            ]
        )
    )

    if kind == "non_dict":
        # A non-dict payload — the contract reports it expected an object.
        return draw(_non_dict_payloads), "get_order_flow"

    payload = draw(_conforming_label())

    if kind == "out_of_enum":
        # Replace one categorical state with a string outside its enum.
        field = draw(st.sampled_from(["order_flow_state", "alignment"]))
        payload[field] = draw(_out_of_enum_strings)
        return payload, field

    if kind == "missing_field":
        # Drop one required field: a state, the whole 'measures' object, a single
        # named measure inside it, the tick_ofi, or the live_tick flag.
        field = draw(
            st.sampled_from(
                ["order_flow_state", "alignment", "measures", "tick_ofi"]
                + measure_fields
            )
        )
        if field in measure_fields:
            del payload["measures"][field]
        else:
            del payload[field]
        return payload, field

    if kind == "non_numeric_measure":
        # Corrupt one measure to a non-numeric, non-null value, OR replace the
        # whole 'measures' object with a non-dict.
        if draw(st.booleans()):
            payload["measures"] = draw(_non_numeric_values)
            return payload, "measures"
        field = draw(st.sampled_from(measure_fields))
        payload["measures"][field] = draw(_non_numeric_values)
        return payload, field

    if kind == "bad_tick_ofi":
        # Corrupt the tick_ofi to a non-numeric, non-null value.
        payload["tick_ofi"] = draw(_non_numeric_values)
        return payload, "tick_ofi"

    # kind == "non_boolean_flag": corrupt live_tick_contributed to a non-boolean.
    payload["live_tick_contributed"] = draw(_non_boolean_values)
    return payload, "live_tick_contributed"


# ─────────────────────────────────────────────────────────────────────────────
# Property 19: validate_contract rejects non-conforming results, naming the field
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 19: validate_contract rejects non-conforming results, naming the field
@settings(max_examples=200, deadline=None)
@given(mutated=_mutated_label())
def test_property_19_contract_rejection_names_offending_field(mutated):
    """Validates: Requirements 5.7

    For any conforming Order_Flow_Label mutated by a single contract violation
    (a bad order_flow_state, a bad alignment, a missing/non-object measures, a
    non-numeric/non-null measure, a missing/non-number tick_ofi, a non-boolean
    live_tick_contributed) or a non-dict payload, validate_contract returns a
    structured {"error", "contract_violation"} result whose violation message
    identifies the offending field. Never raises.
    """
    payload, offending_field = mutated

    assume(offending_field is not None)

    # Must never raise — contract failures are data, not exceptions.
    try:
        result = validate_contract("get_order_flow", payload)
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
