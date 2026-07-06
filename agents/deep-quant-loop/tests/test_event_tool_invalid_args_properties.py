# Feature: earnings-event-risk-gate, Property 11: The tool rejects an empty or whitespace symbol without raising
"""Property-based test for get_event_risk empty/whitespace-symbol rejection
(tools.py, task 4.4).

Feature: earnings-event-risk-gate

This module implements design **Property 11: The tool rejects an empty or
whitespace symbol without raising**:

    For any empty, whitespace-only, or non-string ``symbol``, ``get_event_risk``
    returns a STRUCTURED error result (a dict carrying an ``"error"`` key) and
    never raises an exception (Requirement 4.3).

The ``get_event_risk`` tool (``tools.py``) checks the master enable flag FIRST
and, when the gate is enabled, validates its arguments up front: an empty /
whitespace-only / non-string ``symbol`` is turned into a structured error
result — a dict with an ``"error"`` key — and the call never raises. That
argument-rejection short-circuits BEFORE any Event_Source retrieval, so this
property is fully self-contained and requires no calendar file, calendar API, or
network access.

To make the property robust regardless of ambient environment:
  * ``EVENT_GATE_ENABLED`` is forced on (``"1"``) so the enable-flag short-circuit
    does not pre-empt the symbol check (a disabled gate returns an
    Unavailable_Marker, not an argument error).
  * the pluggable source reader ``tools._load_event_candidates`` is patched to
    fail loudly if it is ever reached, proving the symbol rejection short-circuits
    before any source retrieval / I/O occurs.

Because ``get_event_risk`` is a LangChain ``@tool``-decorated object, it is
invoked here via the undecorated function behind it (``.func``). The sys.path /
``_raw`` @tool-unwrap pattern mirrors
``tests/test_session_tool_invalid_args_properties.py``.

Validates: Requirements 4.3
"""

import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402


def _raw(tool_obj):
    """Return the undecorated function behind a LangChain @tool object."""
    return getattr(tool_obj, "func", tool_obj)


def _no_source(*args, **kwargs):
    """Stand-in for the source reader that fails if any retrieval is attempted.

    An empty/whitespace/non-string symbol must be rejected BEFORE any
    Event_Source retrieval; if this is ever called on the rejection path the
    property has failed.
    """
    raise AssertionError(
        "get_event_risk consulted the Event_Source on an invalid symbol; "
        "argument validation must short-circuit before any source retrieval"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Generators: ONLY invalid symbols (empty, whitespace-only, or non-string), each
# paired with an arbitrary holding_horizon so the horizon never rescues an
# invalid symbol.
# ─────────────────────────────────────────────────────────────────────────────

# Empty / whitespace-only string symbols (``.strip()`` -> "").
_EMPTY_OR_WS_SYMBOL = st.one_of(
    st.just(""),
    st.text(alphabet=" \t\n\r\v\f", min_size=1, max_size=10),
    st.sampled_from(["", " ", "   ", "\t", "\n", "\r\n", " \t \n ", "\u00a0"]),
)

# Non-string symbol values — the ``isinstance(symbol, str)`` guard must reject
# these without raising.
_NON_STRING_SYMBOL = st.one_of(
    st.none(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.booleans(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
    st.tuples(st.integers()),
)

_INVALID_SYMBOL = st.one_of(_EMPTY_OR_WS_SYMBOL, _NON_STRING_SYMBOL)

# Any holding_horizon — recognized, unrecognized, empty, or (defensively) a
# non-string — none of which should turn an invalid symbol into a success.
_ANY_HORIZON = st.one_of(
    st.sampled_from(["", "intraday", "multi_session", "swing", "positional", "  "]),
    st.text(max_size=12),
    st.none(),
    st.integers(),
)


def _symbol_is_invalid(symbol) -> bool:
    """Mirror the tool's own argument guard: non-string or blank is invalid."""
    return not isinstance(symbol, str) or not symbol.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Property 11: an empty/whitespace/non-string symbol -> structured error,
#              never raises, no fabricated event fields, no source retrieval.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 11: The tool rejects an empty or whitespace symbol without raising
@settings(max_examples=200, deadline=None)
@given(symbol=_INVALID_SYMBOL, holding_horizon=_ANY_HORIZON)
def test_property_11_event_tool_rejects_empty_symbol_without_raising(symbol, holding_horizon):
    """Feature: earnings-event-risk-gate, Property 11: The tool rejects an empty
    or whitespace symbol without raising.

    For any empty / whitespace-only / non-string ``symbol`` (with the gate
    enabled), ``get_event_risk`` returns a structured error result (a dict with
    an ``"error"`` key) and never raises. The rejection short-circuits before any
    Event_Source retrieval, so the source reader is patched to fail loudly if
    reached, and no ``event_risk`` / ``event_recommendation`` field is fabricated.

    Validates: Requirements 4.3
    """
    # Sanity: the generated symbol really is invalid (a structured error — not a
    # classification — is the expected outcome).
    assert _symbol_is_invalid(symbol)

    # Force the gate ENABLED so the enable-flag short-circuit does not pre-empt
    # the symbol check, and guard against any source retrieval on this path.
    with mock.patch.dict(os.environ, {"EVENT_GATE_ENABLED": "1"}), \
            mock.patch.object(tools, "_load_event_candidates", side_effect=_no_source):
        # The call must not raise — any exception escaping here fails the property.
        result = _raw(tools.get_event_risk)(symbol=symbol, holding_horizon=holding_horizon)

    # The result is a structured error dict carrying a non-empty "error" string.
    assert isinstance(result, dict), f"expected a dict result, got {type(result)!r}"
    assert "error" in result, f"expected an 'error' key in {result!r}"
    assert isinstance(result["error"], str) and result["error"].strip()

    # A pure argument-rejection is NOT an Unavailable_Marker (that marker is
    # reserved for retrieval/processing degradation, not bad input), and it never
    # fabricates the classification fields (Requirement 5.1 / AD-3).
    assert "unavailable" not in result, f"argument error must not be a marker: {result!r}"
    assert "event_risk" not in result, f"fabricated event_risk in {result!r}"
    assert "event_recommendation" not in result, f"fabricated event_recommendation in {result!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Companion check: a VALID symbol with a mocked (empty) source does NOT produce a
# structured argument error — confirming the rejection above is specific to
# invalid symbols and not a blanket failure. A valid symbol with no candidates
# degrades to an honest Unavailable_Marker, never an "error" result.
# ─────────────────────────────────────────────────────────────────────────────

_EMPTY_SOURCE = {
    "source_configured": True,
    "candidates": [],
    "retrieval_failed": False,
    "failure_reason": None,
}


# Feature: earnings-event-risk-gate, Property 11: The tool rejects an empty or whitespace symbol without raising
@settings(max_examples=100, deadline=None)
@given(
    symbol=st.sampled_from(["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"]),
    holding_horizon=st.sampled_from(["", "intraday", "multi_session", "swing"]),
)
def test_property_11_valid_symbol_with_mocked_source_is_not_arg_error(symbol, holding_horizon):
    """A valid symbol with a mocked configured-but-empty Event_Source does NOT
    return a structured argument error, confirming the rejection is specific to
    invalid symbols (Requirement 4.3). With no upcoming event it degrades to an
    honest Unavailable_Marker rather than an ``"error"`` result.
    """
    with mock.patch.dict(os.environ, {"EVENT_GATE_ENABLED": "1"}), \
            mock.patch.object(tools, "_load_event_candidates", return_value=dict(_EMPTY_SOURCE)):
        result = _raw(tools.get_event_risk)(symbol=symbol, holding_horizon=holding_horizon)

    assert isinstance(result, dict)
    # The valid path is not an argument-rejection error result.
    assert "error" not in result, f"valid symbol produced an argument error: {result!r}"
    # A configured-but-empty source is honestly unavailable, not fabricated.
    assert result.get("unavailable") is True, f"expected an Unavailable_Marker, got {result!r}"
