"""Property-based test for relative-strength contract rejection (tools.py, task 5.9).

Feature: relative-strength-context

This Hypothesis property exercises ``validate_contract`` from ``tools.py`` for the
``get_relative_strength`` Tool_Result_Contract. It covers design Property 16: for
any conforming ``get_relative_strength`` Relative_Strength_Label mutated to
violate the contract — an out-of-enum ``index_direction`` /
``relative_strength_state`` / ``alignment``, a missing required field, a
non-string ``benchmark``, or a non-numeric/non-null measure — ``validate_contract``
returns a structured ``{"error", "contract_violation"}`` result whose violation
message identifies the offending field.

The generator starts from a fully conforming Relative_Strength_Label (the three
categorical states each drawn from their fixed enums, a ``benchmark`` string, and
a ``measures`` mapping carrying all named measures each as a finite number or
null) and then applies exactly ONE contract-violating mutation, recording the
name of the offending field so the property can assert the violation message
mentions it. No mutation introduces an honest ``error`` / ``unavailable`` marker,
so each mutated payload genuinely reaches the relative-strength contract branch
as a (broken) label rather than being passed through as a graceful-degradation
result.
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
    INDEX_DIRECTIONS,
    RELATIVE_STRENGTH_STATES,
    _RS_MEASURE_FIELDS,
    validate_contract,
)

# The union of every categorical enum value — an out-of-enum string must avoid
# all of these so the chosen state field is genuinely non-conforming.
_ALL_ENUM_VALUES = (
    set(INDEX_DIRECTIONS)
    | set(RELATIVE_STRENGTH_STATES)
    | set(ALIGNMENT_VALUES)
)

# Finite numbers (and null) are the only conforming measure values.
_finite_numbers = st.floats(
    allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6
)
_measure_values = st.one_of(_finite_numbers, st.none())

# Strings that are NOT any categorical enum value — used for out-of-enum states.
_out_of_enum_strings = st.text(min_size=0, max_size=12).filter(
    lambda s: s not in _ALL_ENUM_VALUES
)

# Non-numeric / non-null values for corrupting a measure (bools are explicitly
# non-numeric under the contract, alongside strings, lists, and dicts).
_non_numeric_values = st.one_of(
    st.text(min_size=0, max_size=8),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)

# Non-string values for corrupting the `benchmark` field.
_non_string_values = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
    st.lists(st.text(max_size=3), max_size=3),
)


@st.composite
def _conforming_label(draw):
    """A fully conforming get_relative_strength Relative_Strength_Label."""
    return {
        "index_direction": draw(st.sampled_from(sorted(INDEX_DIRECTIONS))),
        "relative_strength_state": draw(
            st.sampled_from(sorted(RELATIVE_STRENGTH_STATES))
        ),
        "alignment": draw(st.sampled_from(sorted(ALIGNMENT_VALUES))),
        "benchmark": draw(st.sampled_from(["NIFTY 50", "BANKNIFTY", "FINNIFTY"])),
        "measures": {field: draw(_measure_values) for field in _RS_MEASURE_FIELDS},
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "aligned_candles": 64,
    }


@st.composite
def _mutated_label(draw):
    """A conforming Relative_Strength_Label with exactly one contract violation.

    Returns ``(payload, offending_field)`` where ``offending_field`` is the name
    that the resulting ``contract_violation`` message must mention.
    """
    payload = draw(_conforming_label())
    measure_fields = list(_RS_MEASURE_FIELDS)

    kind = draw(
        st.sampled_from(
            [
                "out_of_enum",
                "missing_field",
                "non_string_benchmark",
                "non_numeric_measure",
            ]
        )
    )

    if kind == "out_of_enum":
        # Replace one categorical state with a string outside its enum.
        field = draw(
            st.sampled_from(
                ["index_direction", "relative_strength_state", "alignment"]
            )
        )
        payload[field] = draw(_out_of_enum_strings)
        return payload, field

    if kind == "missing_field":
        # Drop one required field: a state, the `benchmark`, the whole 'measures'
        # object, or a single named measure inside it.
        field = draw(
            st.sampled_from(
                [
                    "index_direction",
                    "relative_strength_state",
                    "alignment",
                    "benchmark",
                    "measures",
                ]
                + measure_fields
            )
        )
        if field in measure_fields:
            del payload["measures"][field]
        else:
            del payload[field]
        return payload, field

    if kind == "non_string_benchmark":
        # Corrupt the resolved Benchmark_Index to a non-string value.
        payload["benchmark"] = draw(_non_string_values)
        return payload, "benchmark"

    # kind == "non_numeric_measure": corrupt one measure to a non-numeric,
    # non-null value.
    field = draw(st.sampled_from(measure_fields))
    payload["measures"][field] = draw(_non_numeric_values)
    return payload, field


# ─────────────────────────────────────────────────────────────────────────────
# Property 16: validate_contract rejects non-conforming results, naming the field
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 16: validate_contract rejects non-conforming results, naming the field
@settings(max_examples=100, deadline=None)
@given(mutated=_mutated_label())
def test_property_16_rs_contract_rejection_names_offending_field(mutated):
    """Feature: relative-strength-context, Property 16: validate_contract rejects
    non-conforming get_relative_strength results, naming the offending field —
    for any conforming Relative_Strength_Label mutated by a single contract
    violation (an out-of-enum index_direction / relative_strength_state /
    alignment, a missing required field, a non-string benchmark, or a
    non-numeric/non-null measure), validate_contract returns a structured
    {"error", "contract_violation"} result whose violation message identifies
    the offending field. Never raises.

    Validates: Requirements 4.7
    """
    payload, offending_field = mutated

    # Guard against a degenerate generated case where a mutation happens to leave
    # the dict in a conforming shape (it should not, but assume keeps the
    # property honest about what it asserts).
    assume(offending_field is not None)

    # Must never raise — contract failures are data, not exceptions.
    try:
        result = validate_contract("get_relative_strength", payload)
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
