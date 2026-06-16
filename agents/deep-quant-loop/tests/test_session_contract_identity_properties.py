"""Property-based test for contract identity on conforming results/markers (tools.py, task 4.6).

Feature: session-expiry-awareness

This Hypothesis property exercises ``validate_contract``'s ``get_session_context``
branch:

  * Property 12 (4.6, 4.8) — ``validate_contract`` is the identity on conforming
    results and markers: for any generated conforming ``get_session_context``
    Session_Label, and for any Unavailable_Marker, ``validate_contract(
    "get_session_context", result)`` returns that result unchanged.

A conforming Session_Label carries a ``session_phase`` drawn from the fixed
SESSION_PHASES enum, a ``minutes_since_open`` and a ``minutes_until_close`` each a
finite number or ``null`` (null outside the session), an ``expiry_context``
object carrying a boolean ``is_expiry_day`` and a finite-number
``days_until_expiry``, and a ``time_favorability`` drawn from the fixed
TIME_FAVORABILITY enum. An Unavailable_Marker carries ``{"unavailable": true,
"reason": ...}`` and (per AD-5) omits ``session_phase`` / ``time_favorability``.

The test asserts the call never raises and returns the *same object* unchanged
(both object identity and deep equality), pinning the contract's pass-through
behavior across the full conforming input space.
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

# ── Generators ────────────────────────────────────────────────────────────────

# A finite number or null — exactly what minutes_since_open and minutes_until_close
# are allowed to be in a conforming label (R4.5). Bools are excluded because the
# contract's ``_is_number`` rejects them. The documented domain is null-or-finite-
# non-negative, but the contract only requires finite-number-or-null; generate the
# broader finite-or-null space so the identity holds across every conforming value.
_finite_number = st.floats(allow_nan=False, allow_infinity=False)
_minutes_value = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=1_000_000),
    st.floats(min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False),
    _finite_number,
)

# days_until_expiry must be a finite number (the contract uses _is_number); the
# documented domain is the integer range [0, 6].
_days_until_expiry = st.one_of(
    st.integers(min_value=0, max_value=6),
    st.integers(min_value=-1_000, max_value=1_000),
    _finite_number,
)

_timeframe = st.sampled_from(sorted({"1m", "5m", "10m", "15m", "1h", "4h", "1d"}))
_symbol = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90), min_size=1, max_size=8
)


@st.composite
def _conforming_label(draw):
    """A conforming ``get_session_context`` Session_Label.

    ``session_phase`` and ``time_favorability`` are drawn from their fixed enums,
    the minutes fields are each a finite number or null, and ``expiry_context``
    carries a boolean ``is_expiry_day`` and a finite-number ``days_until_expiry``,
    so the label satisfies the contract that ``validate_contract`` enforces.
    """
    label = {
        "session_phase": draw(st.sampled_from(sorted(SESSION_PHASES))),
        "minutes_since_open": draw(_minutes_value),
        "minutes_until_close": draw(_minutes_value),
        "expiry_context": {
            "is_expiry_day": draw(st.booleans()),
            "days_until_expiry": draw(_days_until_expiry),
        },
        "time_favorability": draw(st.sampled_from(sorted(TIME_FAVORABILITY))),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }
    return label


@st.composite
def _unavailable_marker(draw):
    """An Unavailable_Marker ({"unavailable": true, "reason": ...}).

    Per AD-5 the marker omits Session_Phase / Time_Favorability; it is recognized
    as an honest non-fatal result and must pass through unchanged.
    """
    marker = {
        "unavailable": True,
        "reason": draw(
            st.text(min_size=0, max_size=80)
            | st.sampled_from(
                [
                    "invalid timestamp: expected a finite epoch-millisecond number, got None",
                    "candle retrieval timed out",
                    "symbol candle retrieval failed",
                    "no reference candle available",
                ]
            )
        ),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }
    return marker


_conforming_result = st.one_of(_conforming_label(), _unavailable_marker())


# ─────────────────────────────────────────────────────────────────────────────
# Property 12: validate_contract is the identity on conforming results & markers
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 12: validate_contract is the identity on conforming results and markers
@settings(max_examples=200, deadline=None)
@given(result=_conforming_result)
def test_property_12_validate_contract_is_identity_on_conforming_session(result):
    """Validates: Requirements 4.6, 4.8

    For any conforming Session_Label or any Unavailable_Marker,
    ``validate_contract("get_session_context", result)`` returns that result
    unchanged (object identity + deep equality) and never raises.
    """
    # Snapshot for an after-the-fact equality check (defends against any
    # accidental mutation of the input by the validator).
    import copy

    snapshot = copy.deepcopy(result)

    try:
        returned = validate_contract("get_session_context", result)
    except Exception as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"validate_contract raised {exc!r} on a conforming result/marker"
        )

    # Identity: the exact same object is passed through (the branch returns
    # ``payload`` unchanged; the marker path returns it via _has_honest_marker).
    assert returned is result, "validate_contract did not return the same object"

    # It is not flagged as a violation.
    assert not (
        isinstance(returned, dict) and "contract_violation" in returned
    ), "conforming result/marker was incorrectly flagged as a contract violation"

    # Unchanged: the returned object equals the pre-call snapshot.
    assert returned == snapshot, "validate_contract altered the input result"
