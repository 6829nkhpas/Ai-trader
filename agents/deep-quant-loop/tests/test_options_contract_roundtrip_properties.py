"""Property-based test for contract round-trip / rejection / pass-through
(tools.py, task 4.6).

Feature: options-agent-integration

This Hypothesis property exercises ``validate_contract``'s ``get_options_analytics``
branch in ``tools.py``. It covers design **Property 9: Contract validation
round-trips conforming results and rejects malformed ones** (Requirement 2.6):

  (a) For any conforming ``Options_Bias_Label``, ``validate_contract(
      "get_options_analytics", label)`` returns that label UNCHANGED (object
      identity + deep equality) — the contract is the identity on conforming
      results.
  (b) An Unavailable_Marker (``{"unavailable": true, "reason": ...}``) and an
      ``{"error": ...}`` payload pass through UNCHANGED via the honest-marker
      path (``_has_honest_marker``).
  (c) For any conforming label mutated by exactly ONE contract violation — a bad
      ``options_bias_state``, a bad ``alignment``, a bad ``chain_context``, a
      numeric-or-null field (``pcr_oi`` / ``pcr_volume`` / ``max_pain`` /
      ``futures_basis``) that is missing or neither-number-nor-null, a malformed
      ``oi_buildup`` (non-object / missing call|put), a malformed ``oi_walls``
      (non-object / missing or non-numeric support|resistance), or a malformed
      ``iv_skew`` (neither object nor null) — or a non-dict payload,
      ``validate_contract`` returns a structured ``{"error",
      "contract_violation"}`` dict whose message NAMES the offending field.
  (d) Validation NEVER raises — contract failures are data, not exceptions.

A conforming label carries the two categorical labels drawn from their fixed
enums (``options_bias_state`` in {bullish, bearish, neutral}, ``alignment`` in
{aligned, misaligned, neutral}), a ``chain_context`` in {own-chain,
broad-market}, each named analytic (``pcr_oi`` / ``pcr_volume`` / ``max_pain`` /
``futures_basis``) as a finite number or null, an ``oi_buildup`` object with
``call`` / ``put``, an ``oi_walls`` object with numeric-or-null ``support`` /
``resistance``, and an ``iv_skew`` object-or-null. No mutation introduces an
honest ``error`` / ``unavailable`` marker, so each mutated payload genuinely
reaches contract validation as a (broken) label rather than being passed
through as a graceful-degradation result.

The sys.path / import pattern mirrors ``tests/test_of_contract_identity_properties.py``
and ``tests/test_of_contract_rejection_properties.py``.
"""

import copy
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
    OPTIONS_BIAS_STATES,
    OPTIONS_CHAIN_CONTEXTS,
    _OPTIONS_NUMERIC_OR_NULL_FIELDS,
    validate_contract,
)

# ── Generators ────────────────────────────────────────────────────────────────

# The union of every categorical enum value — an out-of-enum string must avoid
# all of these so the chosen state field is genuinely non-conforming.
_ALL_ENUM_VALUES = (
    set(OPTIONS_BIAS_STATES) | set(ALIGNMENT_VALUES) | set(OPTIONS_CHAIN_CONTEXTS)
)

# A finite number or null — exactly what each named analytic, and each oi_walls
# level, is allowed to be in a conforming label. Bools are excluded because the
# contract's ``_is_number`` rejects them.
_finite_number = st.floats(
    allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6
)
_numeric_or_null = st.one_of(
    st.none(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    _finite_number,
)

# Per-side OI buildup labels — the contract only checks call/put presence, so any
# value conforms; sampling realistic buildup states keeps the labels lifelike.
_buildup_values = st.sampled_from(
    [
        "long_buildup",
        "short_buildup",
        "long_unwinding",
        "short_covering",
        "neutral",
    ]
)

# iv_skew is an object-or-null in a conforming label.
_iv_skew_values = st.one_of(
    st.none(),
    st.fixed_dictionaries({"put_minus_call": _finite_number}),
)

# Strings that are NOT any categorical enum value — used for out-of-enum states.
_out_of_enum_strings = st.text(min_size=0, max_size=12).filter(
    lambda s: s not in _ALL_ENUM_VALUES
)

# Non-numeric / non-null values for corrupting a numeric analytic or oi_walls
# level (bools are explicitly non-numeric under the contract, alongside strings,
# lists, and dicts).
_non_numeric_values = st.one_of(
    st.text(min_size=0, max_size=8),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)

# Non-dict values for corrupting oi_buildup / oi_walls (none/scalars/lists), none
# of which carry an honest marker.
_non_dict_values = st.one_of(
    st.none(),
    st.integers(),
    _finite_number,
    st.text(min_size=0, max_size=8),
    st.lists(st.integers(), max_size=3),
)

# Non-object, non-null values for corrupting iv_skew (which is object-or-null).
_non_object_non_null_iv_skew = st.one_of(
    st.integers(),
    _finite_number,
    st.text(min_size=0, max_size=8),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
)

# Non-dict (and non-marker) payloads — these never carry an honest marker, so
# they reach the "expected an object" branch of validate_contract.
_non_dict_payloads = st.one_of(
    st.integers(),
    _finite_number,
    st.text(min_size=0, max_size=8),
    st.none(),
    st.lists(st.integers(), max_size=3),
)


@st.composite
def _conforming_label(draw):
    """A fully conforming ``get_options_analytics`` Options_Bias_Label."""
    return {
        "options_bias_state": draw(st.sampled_from(sorted(OPTIONS_BIAS_STATES))),
        "alignment": draw(st.sampled_from(sorted(ALIGNMENT_VALUES))),
        "chain_context": draw(st.sampled_from(sorted(OPTIONS_CHAIN_CONTEXTS))),
        "pcr_oi": draw(_numeric_or_null),
        "pcr_volume": draw(_numeric_or_null),
        "max_pain": draw(_numeric_or_null),
        "futures_basis": draw(_numeric_or_null),
        "oi_buildup": {"call": draw(_buildup_values), "put": draw(_buildup_values)},
        "oi_walls": {
            "support": draw(_numeric_or_null),
            "resistance": draw(_numeric_or_null),
        },
        "iv_skew": draw(_iv_skew_values),
        # Extra context fields the contract does not constrain but a real tool
        # result carries — included so identity holds across realistic labels.
        "underlying": "NIFTY 50",
        "symbol": "RELIANCE",
        "expiry": "2024-01-25",
        "spot": 21420.0,
    }


@st.composite
def _unavailable_marker(draw):
    """An Unavailable_Marker — omits options_bias_state / alignment (AD-6)."""
    return {
        "unavailable": True,
        "reason": draw(
            st.text(min_size=0, max_size=80)
            | st.sampled_from(
                [
                    "no option-chain snapshot for the resolved underlying",
                    "outside market hours",
                    "unsubscribed underlying",
                    "spot unavailable",
                ]
            )
        ),
        "symbol": "RELIANCE",
        "underlying": "NIFTY 50",
        "chain_context": draw(st.sampled_from(sorted(OPTIONS_CHAIN_CONTEXTS))),
    }


@st.composite
def _error_payload(draw):
    """An ``{"error": ...}`` payload — an honest non-fatal result, not a label."""
    return {
        "error": draw(st.text(min_size=0, max_size=60)),
        "symbol": "RELIANCE",
    }


# Conforming-or-marker inputs the contract must return unchanged (a) + (b).
_passthrough_result = st.one_of(
    _conforming_label(), _unavailable_marker(), _error_payload()
)


@st.composite
def _mutated_label(draw):
    """A conforming label with exactly one contract-violating mutation.

    Returns ``(payload, offending_field)`` where ``offending_field`` is the name
    that the resulting ``contract_violation`` message must mention.
    """
    numeric_fields = list(_OPTIONS_NUMERIC_OR_NULL_FIELDS)

    kind = draw(
        st.sampled_from(
            [
                "non_dict",
                "bad_options_bias_state",
                "bad_alignment",
                "bad_chain_context",
                "missing_numeric",
                "non_numeric",
                "bad_oi_buildup",
                "bad_oi_walls",
                "bad_iv_skew",
            ]
        )
    )

    if kind == "non_dict":
        # A non-dict payload — the contract reports it expected an object,
        # naming the tool.
        return draw(_non_dict_payloads), "get_options_analytics"

    payload = draw(_conforming_label())

    if kind == "bad_options_bias_state":
        payload["options_bias_state"] = draw(_out_of_enum_strings)
        return payload, "options_bias_state"

    if kind == "bad_alignment":
        payload["alignment"] = draw(_out_of_enum_strings)
        return payload, "alignment"

    if kind == "bad_chain_context":
        payload["chain_context"] = draw(_out_of_enum_strings)
        return payload, "chain_context"

    if kind == "missing_numeric":
        # Drop one required numeric-or-null analytic entirely.
        field = draw(st.sampled_from(numeric_fields))
        del payload[field]
        return payload, field

    if kind == "non_numeric":
        # Corrupt one numeric analytic to a non-numeric, non-null value.
        field = draw(st.sampled_from(numeric_fields))
        payload[field] = draw(_non_numeric_values)
        return payload, field

    if kind == "bad_oi_buildup":
        # Replace oi_buildup with a non-object, OR drop one of call/put.
        if draw(st.booleans()):
            payload["oi_buildup"] = draw(_non_dict_values)
            return payload, "oi_buildup"
        side = draw(st.sampled_from(["call", "put"]))
        del payload["oi_buildup"][side]
        return payload, side

    if kind == "bad_oi_walls":
        # Replace oi_walls with a non-object, drop a level, or make a level
        # non-numeric.
        choice = draw(st.sampled_from(["non_object", "missing", "non_numeric"]))
        if choice == "non_object":
            payload["oi_walls"] = draw(_non_dict_values)
            return payload, "oi_walls"
        level = draw(st.sampled_from(["support", "resistance"]))
        if choice == "missing":
            del payload["oi_walls"][level]
        else:
            payload["oi_walls"][level] = draw(_non_numeric_values)
        return payload, level

    # kind == "bad_iv_skew": iv_skew is neither an object nor null.
    payload["iv_skew"] = draw(_non_object_non_null_iv_skew)
    return payload, "iv_skew"


# ─────────────────────────────────────────────────────────────────────────────
# Property 9: Contract validation round-trips conforming results and rejects
# malformed ones
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 9: Contract validation round-trips conforming results and rejects malformed ones
@settings(max_examples=200, deadline=None)
@given(result=_passthrough_result)
def test_property_9_validate_contract_is_identity_on_conforming_and_markers(result):
    """Validates: Requirements 2.6

    (a)+(b): For any conforming Options_Bias_Label, any Unavailable_Marker, and
    any {"error": ...} payload, validate_contract("get_options_analytics", result)
    returns that result UNCHANGED (object identity + deep equality) and never
    raises.
    """
    snapshot = copy.deepcopy(result)

    try:
        returned = validate_contract("get_options_analytics", result)
    except Exception as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"validate_contract raised {exc!r} on a conforming result/marker"
        )

    # Identity: the exact same object is passed through.
    assert returned is result, "validate_contract did not return the same object"

    # It is not flagged as a violation.
    assert not (
        isinstance(returned, dict) and "contract_violation" in returned
    ), "conforming result/marker was incorrectly flagged as a contract violation"

    # Unchanged: the returned object equals the pre-call snapshot.
    assert returned == snapshot, "validate_contract altered the input result"


# Feature: options-agent-integration, Property 9: Contract validation round-trips conforming results and rejects malformed ones
@settings(max_examples=200, deadline=None)
@given(mutated=_mutated_label())
def test_property_9_validate_contract_rejection_names_offending_field(mutated):
    """Validates: Requirements 2.6

    (c)+(d): For any conforming Options_Bias_Label mutated by a single contract
    violation (bad options_bias_state / alignment / chain_context, a
    missing/non-numeric numeric-or-null analytic, a malformed oi_buildup /
    oi_walls / iv_skew) or a non-dict payload, validate_contract returns a
    structured {"error", "contract_violation"} result whose violation message
    identifies the offending field. Never raises.
    """
    payload, offending_field = mutated

    assume(offending_field is not None)

    # Must never raise — contract failures are data, not exceptions.
    try:
        result = validate_contract("get_options_analytics", payload)
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
