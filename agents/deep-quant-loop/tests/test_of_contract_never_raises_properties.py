"""Property-based test that validate_contract never raises (tools.py, task 6.9).

Feature: order-flow-context

This Hypothesis property exercises ``validate_contract`` from ``tools.py`` for
the ``get_order_flow`` Tool_Result_Contract. It covers design Property 20:

  * Property 20 (5.9) — ``validate_contract`` never raises on an order-flow
    result, including when the result is malformed, missing fields, not an
    object, or otherwise arbitrary garbage.

Mirroring AD-4 ("contract failures are data, not exceptions"), calling
``validate_contract("get_order_flow", result)`` with ANY input must never
propagate an exception. It must instead always return a dict — either the
pass-through payload (conforming label / honest Unavailable_Marker) or a
structured ``{"error", "contract_violation"}`` dict for everything else.

The generator deliberately spans the full junk space:

  * NON-dict inputs: ``None``, lists, strings, ints, floats, bools.
  * Dicts with arbitrary/random keys and deeply nested junk.
  * Dicts that look like an order-flow label but are missing fields or carry
    wrong-typed fields (states that are not strings, a ``measures`` field that is
    not an object, measures that are non-numeric, a non-bool
    ``live_tick_contributed``, a non-numeric ``tick_ofi``, partial labels, etc.).

The sys.path / import pattern mirrors
``tests/test_rs_contract_never_raises_properties.py``.
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
    ALIGNMENT_VALUES,
    ORDER_FLOW_STATES,
    _OF_MEASURE_FIELDS,
    validate_contract,
)

# ── Generators ──────────────────────────────────────────────────────────────

# Arbitrary leaf scalars covering every non-dict primitive type the validator
# might be handed (bools are deliberately included alongside ints/floats).
_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=20),
)

# Arbitrary JSON-like structures: scalars, lists, and dicts nested a few levels
# deep. This is the "deeply nested junk" / "dicts with random keys" space.
_json_like = st.recursive(
    _scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=8), children, max_size=5),
    ),
    max_leaves=25,
)

# Values used to fill order-flow-shaped fields with plausible-but-possibly-wrong
# content (so we hit the "missing fields / wrong-typed fields" cases, including
# partial labels, too).
_maybe_state = st.one_of(
    st.sampled_from(sorted(ORDER_FLOW_STATES)),
    st.sampled_from(sorted(ALIGNMENT_VALUES)),
    st.text(max_size=12),
    _scalars,
)

_maybe_measure_value = st.one_of(
    st.none(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=8),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
)

_maybe_tick_ofi = st.one_of(
    st.none(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.integers(min_value=-5, max_value=5),
    st.text(max_size=6),
    st.booleans(),
)

_maybe_flag = st.one_of(
    st.booleans(),
    st.none(),
    st.text(max_size=6),
    st.integers(min_value=-3, max_value=3),
)


@st.composite
def _of_shaped_junk(draw):
    """A dict that resembles an order-flow result but may be malformed.

    Each order-flow-relevant key is independently included-or-omitted, and when
    present is filled with a possibly-wrong-typed value. This exercises the
    label branch of ``validate_contract`` across malformed, missing-field, and
    partial-label inputs without ever guaranteeing conformance. With some
    probability the dict carries an ``unavailable`` marker (so the honest
    pass-through branch is exercised too).
    """
    payload = {}
    # Sometimes shape it like an honest Unavailable_Marker.
    if draw(st.booleans()):
        payload["unavailable"] = draw(st.one_of(st.booleans(), _scalars))
        if draw(st.booleans()):
            payload["reason"] = draw(st.one_of(st.text(max_size=16), _scalars))
    if draw(st.booleans()):
        payload["order_flow_state"] = draw(_maybe_state)
    if draw(st.booleans()):
        payload["alignment"] = draw(_maybe_state)
    if draw(st.booleans()):
        payload["tick_ofi"] = draw(_maybe_tick_ofi)
    if draw(st.booleans()):
        payload["live_tick_contributed"] = draw(_maybe_flag)
    if draw(st.booleans()):
        # 'measures' is sometimes a proper dict, sometimes wrong-typed junk.
        if draw(st.booleans()):
            measures = {}
            for field in _OF_MEASURE_FIELDS:
                if draw(st.booleans()):
                    measures[field] = draw(_maybe_measure_value)
            payload["measures"] = measures
        else:
            payload["measures"] = draw(
                st.one_of(_scalars, st.lists(_scalars, max_size=3))
            )
    # Sprinkle in arbitrary extra keys.
    for _ in range(draw(st.integers(min_value=0, max_value=3))):
        payload[draw(st.text(max_size=6))] = draw(_json_like)
    return payload


# The full garbage space: arbitrary JSON-like values (incl. non-dicts) PLUS
# order-flow-shaped-but-malformed dicts.
_any_input = st.one_of(_json_like, _of_shaped_junk())

# Tool names: the order-flow tool plus a couple of others and random strings, so
# the property holds regardless of which contract branch is taken.
_tool_names = st.one_of(
    st.just("get_order_flow"),
    st.sampled_from(
        ["get_candles", "get_market_regime", "get_relative_strength", "declare_trade"]
    ),
    st.text(max_size=10),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 20: validate_contract never raises on an order-flow result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 20: validate_contract never raises on an order-flow result
@settings(max_examples=200, deadline=None)
@given(payload=_any_input, tool_name=_tool_names)
def test_property_20_validate_contract_never_raises(payload, tool_name):
    """Validates: Requirements 5.9

    For any arbitrary/garbage input — malformed dicts, missing fields,
    wrong-typed fields, partial labels, deeply nested junk, or NON-dict values
    (None, lists, strings, ints, floats, bools) — ``validate_contract`` never
    raises; it always returns a dict instead (the payload itself when it
    conforms or carries an honest Unavailable_Marker, or a structured error dict
    otherwise). Contract failures are data, not exceptions.
    """
    sentinel = object()
    returned = sentinel
    try:
        returned = validate_contract(tool_name, payload)
    except Exception as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"validate_contract({tool_name!r}, ...) raised {exc!r} on input "
            f"{payload!r}; contract validation must never raise (R5.9)"
        )

    # A value must have been returned (the call completed without raising).
    assert returned is not sentinel, "validate_contract did not return a value"

    # For an order-flow result specifically, the return is ALWAYS a dict: either
    # the pass-through payload (conforming label / honest Unavailable_Marker) or
    # a structured error dict (Requirement 5.9). (Other tool branches — e.g.
    # get_candles — legitimately pass through non-dict payloads such as lists,
    # so the dict guarantee is asserted only for get_order_flow.)
    if tool_name == "get_order_flow":
        assert isinstance(returned, dict), (
            f"validate_contract('get_order_flow', ...) returned a non-dict "
            f"{type(returned).__name__} for input {payload!r}"
        )
