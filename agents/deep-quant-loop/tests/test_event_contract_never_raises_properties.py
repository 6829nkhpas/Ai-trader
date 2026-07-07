"""Property-based test that validate_contract never raises (tools.py, task 4.10).

Feature: earnings-event-risk-gate

This Hypothesis property exercises ``validate_contract`` from ``tools.py`` for the
``get_event_risk`` Tool_Result_Contract. It covers design Property 17:

  * Property 17 (4.9) — ``validate_contract`` never raises on an event result,
    including when the result is malformed, missing fields, not an object, or
    otherwise arbitrary garbage.

Mirroring AD-3 ("contract failures are data, not exceptions"), calling
``validate_contract("get_event_risk", result)`` with ANY input must never
propagate an exception. It must instead always return a result — either the
pass-through payload (conforming assessment / honest marker) or a structured
``{"error", "contract_violation"}`` dict for everything else.

The generator deliberately spans the full junk space:

  * NON-dict inputs: ``None``, lists, strings, ints, floats (incl. NaN/inf),
    bools.
  * Dicts with arbitrary/random keys and deeply nested junk.
  * Dicts that look like an Event_Assessment but are missing fields or carry
    wrong-typed fields (event_risk/event_recommendation that are not valid
    enum members, days_until_event that is non-numeric, event_date that is not
    a string, etc.).

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
    EVENT_RECOMMENDATIONS,
    EVENT_RISK_STATES,
    validate_contract,
)

# ── Generators ──────────────────────────────────────────────────────────────

# Arbitrary leaf scalars covering every non-dict primitive type the validator
# might be handed (bools are deliberately included alongside ints/floats, and
# NaN/inf are included among the floats).
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

# Values used to fill event-shaped fields with plausible-but-possibly-wrong
# content (so we hit the "missing fields / wrong-typed fields" cases too).
_maybe_risk = st.one_of(
    st.sampled_from(sorted(EVENT_RISK_STATES)),
    st.sampled_from(sorted(EVENT_RECOMMENDATIONS)),
    st.text(max_size=12),
    _scalars,
)

_maybe_days = st.one_of(
    st.none(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=8),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
)

_maybe_event_date = st.one_of(
    st.text(max_size=12),
    _scalars,
    st.lists(st.text(max_size=4), max_size=3),
)


@st.composite
def _event_shaped_junk(draw):
    """A dict that resembles an event result but may be missing/wrong-typed.

    Each event-relevant key is independently included-or-omitted, and when
    present is filled with a possibly-wrong-typed value. This exercises the
    assessment branch of ``validate_contract`` across malformed and
    missing-field inputs without ever guaranteeing conformance.
    """
    payload = {}
    if draw(st.booleans()):
        payload["event_risk"] = draw(_maybe_risk)
    if draw(st.booleans()):
        payload["event_recommendation"] = draw(_maybe_risk)
    if draw(st.booleans()):
        payload["days_until_event"] = draw(_maybe_days)
    if draw(st.booleans()):
        payload["event_date"] = draw(_maybe_event_date)
    # Sometimes carry an 'unavailable' key with arbitrary (possibly non-bool)
    # value so we exercise the honest-marker pass-through path too.
    if draw(st.booleans()):
        payload["unavailable"] = draw(st.one_of(st.booleans(), _scalars))
    # Sprinkle in arbitrary extra keys.
    for _ in range(draw(st.integers(min_value=0, max_value=3))):
        payload[draw(st.text(max_size=6))] = draw(_json_like)
    return payload


# The full garbage space: arbitrary JSON-like values (incl. non-dicts) PLUS
# event-shaped-but-malformed dicts.
_any_input = st.one_of(_json_like, _event_shaped_junk())

# Tool names: the event tool plus a couple of others and random strings, so the
# property holds regardless of which contract branch (if any) is taken.
_tool_names = st.one_of(
    st.just("get_event_risk"),
    st.sampled_from(
        ["get_candles", "get_prediction", "get_consensus_report", "declare_trade"]
    ),
    st.text(max_size=10),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 17: validate_contract never raises on an event result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 17: validate_contract never raises on an event result
@settings(max_examples=25, deadline=None)
@given(payload=_any_input, tool_name=_tool_names)
def test_property_17_validate_contract_never_raises(payload, tool_name):
    """Validates: Requirements 4.9

    For any arbitrary/garbage input — malformed dicts, missing fields,
    wrong-typed fields, deeply nested junk, or NON-dict values (None, lists,
    strings, ints, floats including NaN/inf, bools) — ``validate_contract``
    never raises; it always returns a result instead (contract failures are
    data, not exceptions).
    """
    sentinel = object()
    returned = sentinel
    try:
        returned = validate_contract(tool_name, payload)
    except Exception as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"validate_contract({tool_name!r}, ...) raised {exc!r} on input "
            f"{payload!r}; contract validation must never raise (R4.9)"
        )

    # A value must have been returned (the call completed without raising).
    assert returned is not sentinel, "validate_contract did not return a value"
