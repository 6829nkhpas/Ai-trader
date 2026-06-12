"""Property-based test that validate_contract never raises (tools.py, task 5.8).

Feature: regime-detection-gate

This Hypothesis property exercises ``validate_contract`` from ``tools.py`` for the
``get_market_regime`` Tool_Result_Contract. It covers design Property 14:

  * Property 14 (3.8) — ``validate_contract`` never raises on a regime result,
    including when the result is malformed, missing fields, not an object, or
    otherwise arbitrary garbage.

Mirroring AD-3 ("contract failures are data, not exceptions"), calling
``validate_contract("get_market_regime", result)`` with ANY input must never
propagate an exception. It must instead always return a result — either the
pass-through payload (conforming label / honest marker) or a structured
``{"error", "contract_violation"}`` dict for everything else.

The generator deliberately spans the full junk space:

  * NON-dict inputs: ``None``, lists, strings, ints, floats, bools.
  * Dicts with arbitrary/random keys and deeply nested junk.
  * Dicts that look like a regime label but are missing fields or carry
    wrong-typed fields (states that are not strings, ``measures`` that is not an
    object, measures that are non-numeric, etc.).

The single property assertion is essentially "calling it does not raise": the
call is wrapped in try/except and the test fails on any exception. As a sanity
check it also asserts a value was actually returned.
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
    REGIME_FAVORABILITY,
    REGIME_TREND_STATES,
    REGIME_VOLATILITY_STATES,
    _REGIME_MEASURE_FIELDS,
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

# Values used to fill regime-shaped fields with plausible-but-possibly-wrong
# content (so we hit the "missing fields / wrong-typed fields" cases too).
_maybe_state = st.one_of(
    st.sampled_from(sorted(REGIME_TREND_STATES)),
    st.sampled_from(sorted(REGIME_VOLATILITY_STATES)),
    st.sampled_from(sorted(REGIME_FAVORABILITY)),
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


@st.composite
def _regime_shaped_junk(draw):
    """A dict that resembles a regime result but may be missing/wrong-typed.

    Each regime-relevant key is independently included-or-omitted, and when
    present is filled with a possibly-wrong-typed value. This exercises the
    label branch of ``validate_contract`` across malformed and missing-field
    inputs without ever guaranteeing conformance.
    """
    payload = {}
    if draw(st.booleans()):
        payload["trend_state"] = draw(_maybe_state)
    if draw(st.booleans()):
        payload["volatility_state"] = draw(_maybe_state)
    if draw(st.booleans()):
        payload["favorability"] = draw(_maybe_state)
    if draw(st.booleans()):
        # 'measures' is sometimes a proper dict, sometimes wrong-typed junk.
        if draw(st.booleans()):
            measures = {}
            for field in _REGIME_MEASURE_FIELDS:
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
# regime-shaped-but-malformed dicts.
_any_input = st.one_of(_json_like, _regime_shaped_junk())

# Tool names: the regime tool plus a couple of others and random strings, so the
# property holds regardless of which contract branch (if any) is taken.
_tool_names = st.one_of(
    st.just("get_market_regime"),
    st.sampled_from(
        ["get_candles", "get_prediction", "get_consensus_report", "declare_trade"]
    ),
    st.text(max_size=10),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 14: validate_contract never raises on a regime result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 14
@settings(max_examples=300, deadline=None)
@given(payload=_any_input, tool_name=_tool_names)
def test_property_14_validate_contract_never_raises(payload, tool_name):
    """Validates: Requirements 3.8

    For any arbitrary/garbage input — malformed dicts, missing fields,
    wrong-typed fields, deeply nested junk, or NON-dict values (None, lists,
    strings, ints, floats, bools) — ``validate_contract`` never raises; it
    always returns a result instead (contract failures are data, not
    exceptions).
    """
    sentinel = object()
    returned = sentinel
    try:
        returned = validate_contract(tool_name, payload)
    except Exception as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"validate_contract({tool_name!r}, ...) raised {exc!r} on input "
            f"{payload!r}; contract validation must never raise (R3.8)"
        )

    # A value must have been returned (the call completed without raising).
    assert returned is not sentinel, "validate_contract did not return a value"
