"""Property-based test for contract rejection naming the field (tools.py, task 6.8).

Feature: volatility-aware-forecaster

This Hypothesis property exercises ``validate_contract`` from ``tools.py`` for the
``get_forecast`` Tool_Result_Contract. It covers design Property 18: for any
``get_forecast`` result mutated to violate the contract — a bad
``projected_direction``, an out-of-range/non-numeric ``up_probability``, a
missing/non-numeric-non-null ``expected_move_atr``, an out-of-range/non-numeric
``forecast_confidence``, a bad ``forecast_alignment``, a missing/non-object
``measures`` object, a measure that is neither a number nor null, or a non-dict
payload — ``validate_contract`` returns a structured
``{"error", "contract_violation"}`` result whose violation message identifies the
offending field.

The generator starts from a fully conforming Forecast_Label (``projected_direction``
drawn from ``FORECAST_DIRECTIONS``, ``up_probability`` and ``forecast_confidence``
finite numbers in [0.0, 1.0], ``expected_move_atr`` a finite-number-or-null,
``forecast_alignment`` drawn from ``ALIGNMENT_VALUES``, and a ``measures`` mapping
carrying all named measures as finite-number-or-null) and then applies exactly ONE
contract-violating mutation, recording the name of the offending field so the
property can assert the violation message mentions it. No mutation introduces an
honest ``error``/``unavailable`` marker (note that payloads carrying an ``error``
key — or ``unavailable: true`` — are passed through by ``_has_honest_marker``), so
each mutated payload genuinely reaches contract validation as a (broken) label
rather than being passed through as a graceful-degradation result.
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
    FORECAST_DIRECTIONS,
    _FORECAST_MEASURE_FIELDS,
    validate_contract,
)

# The union of the categorical enums — an out-of-enum string must avoid all of
# these so the chosen state field is genuinely non-conforming.
_ALL_ENUM_VALUES = set(FORECAST_DIRECTIONS) | set(ALIGNMENT_VALUES)

# Finite numbers (and null) are the only conforming measure / expected_move_atr
# values; finite numbers in [0.0, 1.0] are the conforming probability/confidence.
_finite_numbers = st.floats(
    allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6
)
_unit_interval = st.floats(
    allow_nan=False, allow_infinity=False, min_value=0.0, max_value=1.0
)
_measure_values = st.one_of(_finite_numbers, st.none())

# Strings that are NOT any categorical enum value — used for out-of-enum states.
_out_of_enum_strings = st.text(min_size=0, max_size=12).filter(
    lambda s: s not in _ALL_ENUM_VALUES
)

# Non-numeric / non-null values for corrupting a measure or expected_move_atr
# (bools are explicitly non-numeric under the contract, alongside strings, lists,
# and dicts).
_non_numeric_values = st.one_of(
    st.text(min_size=0, max_size=8),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)

# Finite numbers strictly outside [0.0, 1.0] — these are numeric but violate the
# probability / confidence bounds.
_out_of_range_numbers = st.one_of(
    st.floats(min_value=1.0, exclude_min=True, max_value=1e6,
              allow_nan=False, allow_infinity=False),
    st.floats(max_value=0.0, exclude_max=True, min_value=-1e6,
              allow_nan=False, allow_infinity=False),
)

# Values that break a bounded probability/confidence field: either non-numeric,
# or a number outside [0.0, 1.0].
_bad_bounded_values = st.one_of(_non_numeric_values, _out_of_range_numbers)

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
    """A fully conforming get_forecast Forecast_Label."""
    return {
        "projected_direction": draw(st.sampled_from(sorted(FORECAST_DIRECTIONS))),
        "up_probability": draw(_unit_interval),
        "expected_move_atr": draw(_measure_values),
        "forecast_confidence": draw(_unit_interval),
        "forecast_alignment": draw(st.sampled_from(sorted(ALIGNMENT_VALUES))),
        "measures": {
            field: draw(_measure_values) for field in _FORECAST_MEASURE_FIELDS
        },
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "candles_used": 120,
    }


@st.composite
def _mutated_label(draw):
    """A conforming Forecast_Label with exactly one contract-violating mutation.

    Returns ``(payload, offending_field)`` where ``offending_field`` is the name
    that the resulting ``contract_violation`` message must mention.
    """
    measure_fields = list(_FORECAST_MEASURE_FIELDS)

    kind = draw(
        st.sampled_from(
            [
                "out_of_enum",
                "bad_up_probability",
                "bad_expected_move_atr",
                "bad_forecast_confidence",
                "missing_field",
                "non_numeric_measure",
                "non_dict",
            ]
        )
    )

    if kind == "non_dict":
        # A non-dict payload — the contract reports it expected an object.
        return draw(_non_dict_payloads), "get_forecast"

    payload = draw(_conforming_label())

    if kind == "out_of_enum":
        # Replace one categorical state with a string outside its enum.
        field = draw(st.sampled_from(["projected_direction", "forecast_alignment"]))
        payload[field] = draw(_out_of_enum_strings)
        return payload, field

    if kind == "bad_up_probability":
        # Non-numeric or out-of-[0,1] up_probability.
        payload["up_probability"] = draw(_bad_bounded_values)
        return payload, "up_probability"

    if kind == "bad_expected_move_atr":
        # Non-numeric, non-null expected_move_atr.
        payload["expected_move_atr"] = draw(_non_numeric_values)
        return payload, "expected_move_atr"

    if kind == "bad_forecast_confidence":
        # Non-numeric or out-of-[0,1] forecast_confidence.
        payload["forecast_confidence"] = draw(_bad_bounded_values)
        return payload, "forecast_confidence"

    if kind == "missing_field":
        # Drop one required field: a categorical state, a bounded field, the
        # expected_move_atr, the whole 'measures' object, or a single named
        # measure inside it.
        field = draw(
            st.sampled_from(
                [
                    "projected_direction",
                    "up_probability",
                    "expected_move_atr",
                    "forecast_confidence",
                    "forecast_alignment",
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

    # kind == "non_numeric_measure": corrupt one measure to a non-numeric,
    # non-null value, OR replace the whole 'measures' object with a non-dict.
    if draw(st.booleans()):
        payload["measures"] = draw(_non_numeric_values)
        return payload, "measures"
    field = draw(st.sampled_from(measure_fields))
    payload["measures"][field] = draw(_non_numeric_values)
    return payload, field


# ─────────────────────────────────────────────────────────────────────────────
# Property 18: validate_contract rejects non-conforming results, naming the field
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 18: validate_contract rejects non-conforming results, naming the field
@settings(max_examples=200, deadline=None)
@given(mutated=_mutated_label())
def test_property_18_contract_rejection_names_offending_field(mutated):
    """Validates: Requirements 5.7

    For any conforming Forecast_Label mutated by a single contract violation
    (a bad projected_direction, an out-of-range/non-numeric up_probability, a
    missing/non-numeric-non-null expected_move_atr, an out-of-range/non-numeric
    forecast_confidence, a bad forecast_alignment, a missing/non-object measures,
    a non-numeric/non-null measure) or a non-dict payload, validate_contract
    returns a structured {"error", "contract_violation"} result whose violation
    message identifies the offending field. Never raises.
    """
    payload, offending_field = mutated

    assume(offending_field is not None)

    # Must never raise — contract failures are data, not exceptions.
    try:
        result = validate_contract("get_forecast", payload)
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
