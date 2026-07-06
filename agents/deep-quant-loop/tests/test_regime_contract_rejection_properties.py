"""Property-based test for contract rejection naming the field (tools.py, task 5.7).

Feature: regime-detection-gate

This Hypothesis property exercises ``validate_contract`` from ``tools.py`` for the
``get_market_regime`` Tool_Result_Contract. It covers design Property 13: for any
``get_market_regime`` result mutated to violate the contract — an out-of-enum
state, a missing required field, or a non-numeric/non-null measure —
``validate_contract`` returns a structured ``{"error", "contract_violation"}``
result whose violation message identifies the offending field.

The generator starts from a fully conforming Regime_Label (trend_state /
volatility_state / favorability each drawn from their fixed enums, and a
``measures`` mapping carrying all five named measures, each a finite number or
null) and then applies exactly ONE contract-violating mutation, recording the
name of the offending field so the property can assert the violation message
mentions it. No mutation introduces an honest ``error``/``unavailable`` marker,
so each mutated payload genuinely reaches contract validation as a (broken)
label rather than being passed through as a graceful-degradation result.
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
    REGIME_FAVORABILITY,
    REGIME_TREND_STATES,
    REGIME_VOLATILITY_STATES,
    _REGIME_MEASURE_FIELDS,
    validate_contract,
)

# The union of every categorical enum value — an out-of-enum string must avoid
# all of these so the chosen state field is genuinely non-conforming.
_ALL_ENUM_VALUES = (
    set(REGIME_TREND_STATES)
    | set(REGIME_VOLATILITY_STATES)
    | set(REGIME_FAVORABILITY)
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


@st.composite
def _conforming_label(draw):
    """A fully conforming get_market_regime Regime_Label."""
    return {
        "trend_state": draw(st.sampled_from(sorted(REGIME_TREND_STATES))),
        "volatility_state": draw(st.sampled_from(sorted(REGIME_VOLATILITY_STATES))),
        "favorability": draw(st.sampled_from(sorted(REGIME_FAVORABILITY))),
        "measures": {field: draw(_measure_values) for field in _REGIME_MEASURE_FIELDS},
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "candles_used": 120,
    }


@st.composite
def _mutated_label(draw):
    """A conforming Regime_Label with exactly one contract-violating mutation.

    Returns ``(payload, offending_field)`` where ``offending_field`` is the name
    that the resulting ``contract_violation`` message must mention.
    """
    payload = draw(_conforming_label())
    measure_fields = list(_REGIME_MEASURE_FIELDS)

    kind = draw(
        st.sampled_from(["out_of_enum", "missing_field", "non_numeric_measure"])
    )

    if kind == "out_of_enum":
        # Replace one categorical state with a string outside its enum.
        field = draw(st.sampled_from(["trend_state", "volatility_state", "favorability"]))
        payload[field] = draw(_out_of_enum_strings)
        return payload, field

    if kind == "missing_field":
        # Drop one required field: a state, the whole 'measures' object, or a
        # single named measure inside it.
        field = draw(
            st.sampled_from(
                ["trend_state", "volatility_state", "favorability", "measures"]
                + measure_fields
            )
        )
        if field in measure_fields:
            del payload["measures"][field]
        else:
            del payload[field]
        return payload, field

    # kind == "non_numeric_measure": corrupt one measure to a non-numeric,
    # non-null value.
    field = draw(st.sampled_from(measure_fields))
    payload["measures"][field] = draw(_non_numeric_values)
    return payload, field


# ─────────────────────────────────────────────────────────────────────────────
# Property 13: validate_contract rejects non-conforming results, naming the field
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 13
@settings(max_examples=200, deadline=None)
@given(mutated=_mutated_label())
def test_property_13_contract_rejection_names_offending_field(mutated):
    """Feature: regime-detection-gate, Property 13: validate_contract rejects
    non-conforming get_market_regime results, naming the offending field — for
    any conforming Regime_Label mutated by a single contract violation (an
    out-of-enum state, a missing required field, or a non-numeric/non-null
    measure), validate_contract returns a structured {"error",
    "contract_violation"} result whose violation message identifies the
    offending field. Never raises.

    Validates: Requirements 3.6
    """
    payload, offending_field = mutated

    # Guard against a degenerate generated case where removing a value happens
    # to leave the dict in a conforming shape (it should not, but assume keeps
    # the property honest about what it asserts).
    assume(offending_field is not None)

    # Must never raise — contract failures are data, not exceptions.
    try:
        result = validate_contract("get_market_regime", payload)
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
