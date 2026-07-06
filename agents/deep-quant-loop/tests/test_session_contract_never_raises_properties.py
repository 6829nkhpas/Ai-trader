# Feature: session-expiry-awareness, Property 14: validate_contract never raises on a session result
"""Property-based test that validate_contract never raises (tools.py, task 4.8).

Feature: session-expiry-awareness

This Hypothesis property exercises ``validate_contract`` from ``tools.py`` for
the ``get_session_context`` Tool_Result_Contract. It covers design Property 14:

  * Property 14 (R4.9) — ``validate_contract`` never raises on a session result,
    including when the result is malformed, missing fields, not an object, or
    otherwise arbitrary garbage.

Mirroring AD-4 ("contract failures are data, not exceptions"), calling
``validate_contract("get_session_context", result)`` with ANY input must never
propagate an exception. It must instead always return a value — either the
pass-through payload (a conforming Session_Label / an honest Unavailable_Marker)
or a structured ``{"error", "contract_violation"}`` dict for everything else.

The generator deliberately spans the full junk space:

  * Well-formed Session_Labels (every phase / favorability / expiry combination).
  * Honest Unavailable_Markers.
  * NON-dict inputs: ``None``, lists, strings, ints, floats, bools.
  * Dicts with arbitrary/random keys and deeply nested junk.
  * Dicts that look like a session label but are missing fields or carry
    wrong-typed fields (a non-enum ``session_phase``, non-numeric minutes, an
    ``expiry_context`` that is not an object, a non-bool ``is_expiry_day``, a
    non-numeric ``days_until_expiry``, a non-enum ``time_favorability``, partial
    labels, etc.).

The sys.path / import pattern mirrors
``tests/test_of_contract_never_raises_properties.py``.

Validates: Requirements 4.9
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
    SESSION_PHASES,
    TIME_FAVORABILITY,
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

# Values used to fill session-shaped fields with plausible-but-possibly-wrong
# content (so we hit the "missing fields / wrong-typed fields" cases, including
# partial labels, too).
_maybe_phase = st.one_of(
    st.sampled_from(sorted(SESSION_PHASES)),
    st.text(max_size=12),
    _scalars,
)

_maybe_favorability = st.one_of(
    st.sampled_from(sorted(TIME_FAVORABILITY)),
    st.text(max_size=12),
    _scalars,
)

_maybe_minutes = st.one_of(
    st.none(),
    st.integers(min_value=-1_000, max_value=1_000),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=6),
    st.booleans(),
)

_maybe_is_expiry_day = st.one_of(
    st.booleans(),
    st.none(),
    st.text(max_size=6),
    st.integers(min_value=-3, max_value=3),
)

_maybe_days_until_expiry = st.one_of(
    st.none(),
    st.integers(min_value=-10, max_value=10),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=6),
    st.booleans(),
)


@st.composite
def _well_formed_label(draw):
    """A fully-conforming Session_Label (should pass through unchanged)."""
    minutes_value = st.one_of(
        st.none(),
        st.floats(min_value=0.0, max_value=400.0,
                  allow_nan=False, allow_infinity=False),
        st.integers(min_value=0, max_value=400),
    )
    return {
        "session_phase": draw(st.sampled_from(sorted(SESSION_PHASES))),
        "minutes_since_open": draw(minutes_value),
        "minutes_until_close": draw(minutes_value),
        "expiry_context": {
            "is_expiry_day": draw(st.booleans()),
            "days_until_expiry": draw(st.integers(min_value=0, max_value=6)),
        },
        "time_favorability": draw(st.sampled_from(sorted(TIME_FAVORABILITY))),
        "symbol": draw(st.text(max_size=8)),
        "timeframe": draw(st.sampled_from(["1m", "5m", "15m", "1h", "1d"])),
    }


@st.composite
def _session_shaped_junk(draw):
    """A dict that resembles a session result but may be malformed.

    Each session-relevant key is independently included-or-omitted, and when
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
        payload["session_phase"] = draw(_maybe_phase)
    if draw(st.booleans()):
        payload["minutes_since_open"] = draw(_maybe_minutes)
    if draw(st.booleans()):
        payload["minutes_until_close"] = draw(_maybe_minutes)
    if draw(st.booleans()):
        payload["time_favorability"] = draw(_maybe_favorability)
    if draw(st.booleans()):
        # 'expiry_context' is sometimes a proper-ish dict, sometimes wrong-typed.
        if draw(st.booleans()):
            expiry_context = {}
            if draw(st.booleans()):
                expiry_context["is_expiry_day"] = draw(_maybe_is_expiry_day)
            if draw(st.booleans()):
                expiry_context["days_until_expiry"] = draw(_maybe_days_until_expiry)
            payload["expiry_context"] = expiry_context
        else:
            payload["expiry_context"] = draw(
                st.one_of(_scalars, st.lists(_scalars, max_size=3))
            )
    # Sprinkle in arbitrary extra keys.
    for _ in range(draw(st.integers(min_value=0, max_value=3))):
        payload[draw(st.text(max_size=6))] = draw(_json_like)
    return payload


# The full garbage space: arbitrary JSON-like values (incl. non-dicts), PLUS
# session-shaped-but-malformed dicts, PLUS fully well-formed labels.
_any_input = st.one_of(_json_like, _session_shaped_junk(), _well_formed_label())

# Tool names: the session tool plus a couple of others and random strings, so
# the property holds regardless of which contract branch is taken.
_tool_names = st.one_of(
    st.just("get_session_context"),
    st.sampled_from(
        ["get_candles", "get_market_regime", "get_relative_strength", "declare_trade"]
    ),
    st.text(max_size=10),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 14: validate_contract never raises on a session result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 14: validate_contract never raises on a session result
@settings(max_examples=200, deadline=None)
@given(payload=_any_input, tool_name=_tool_names)
def test_property_14_validate_contract_never_raises(payload, tool_name):
    """Validates: Requirements 4.9

    For any arbitrary/garbage input — well-formed labels, honest markers,
    malformed dicts, missing fields, wrong-typed fields, partial labels, deeply
    nested junk, or NON-dict values (None, lists, strings, ints, floats, bools)
    — ``validate_contract`` never raises; it always returns a value instead (the
    payload itself when it conforms or carries an honest Unavailable_Marker, or a
    structured error dict otherwise). Contract failures are data, not exceptions.
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

    # For a session result specifically, the return is ALWAYS a dict: either the
    # pass-through payload (conforming label / honest Unavailable_Marker) or a
    # structured error dict (Requirement 4.9). (Other tool branches — e.g.
    # get_candles — legitimately pass through non-dict payloads such as lists,
    # so the dict guarantee is asserted only for get_session_context.)
    if tool_name == "get_session_context":
        assert isinstance(returned, dict), (
            f"validate_contract('get_session_context', ...) returned a non-dict "
            f"{type(returned).__name__} for input {payload!r}"
        )
