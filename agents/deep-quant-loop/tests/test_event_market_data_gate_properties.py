# Feature: earnings-event-risk-gate, Property 18: The market-data gate classifies event results correctly and stays monotone
"""Property-based test for the market-data gate over `get_event_risk` (graph.py, task 5.2).

Feature: earnings-event-risk-gate

This module implements design **Property 18: The market-data gate classifies
event results correctly and stays monotone**:

    A usable ``get_event_risk`` assessment (a full Event_Assessment — neither an
    error result nor an explicit Unavailable_Marker) sets the
    ``market_data_seen`` flag; an error result or an Unavailable_Marker does NOT
    set the flag on its own; and once the flag has latched true within a run it
    stays true regardless of any subsequent error / unavailable event results
    (monotonicity: adding a usable result can only turn the flag on, never off;
    a non-usable result never turns it on by itself).

Validates: Requirements 6.4, 6.5.

The implementation under test lives in ``graph.py``:
  - ``MARKET_DATA_TOOL_NAMES`` (must contain ``get_event_risk`` — added in task 5.1)
  - ``_market_data_seen(messages)`` — the classifier used to maintain the flag
  - ``_tool_result_is_error`` / ``_tool_result_is_unavailable`` — the predicates

The latch itself is the expression maintained in ``call_model``:
``market_data_seen = bool(state.get("market_data_seen")) or _market_data_seen(messages)``.
The monotonicity property below models that latch directly.

The real LLM / Rust server / Event_Source is never invoked. The LangChain
``ToolMessage`` (``type == "tool"`` with ``.name`` and ``.content``) is exactly
the shape the gate code reads. Event tool results are serialized both as JSON
(``{"...": ...}``) and as Python dict-repr (``{'...': ...}``) strings, since both
quoting styles flow through the stack.

The sys.path / import pattern mirrors ``tests/test_session_market_data_gate_properties.py``:
the service directory (one level up) is prepended to ``sys.path`` so ``graph`` is
importable when pytest is run from anywhere.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st
from langchain_core.messages import ToolMessage

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import MARKET_DATA_TOOL_NAMES, _market_data_seen  # noqa: E402

EVENT_TOOL = "get_event_risk"


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python dict-repr: single quotes, True/None tokens


def _tool_message(content):
    """Build a LangChain ToolMessage exactly as the gate code reads it."""
    return ToolMessage(content=content, name=EVENT_TOOL, tool_call_id="call_event")


def _latch(prior, messages):
    """Model the flag latch maintained in ``call_model`` (graph.py).

    ``market_data_seen = bool(state.get('market_data_seen')) or
    _market_data_seen(messages)``.
    """
    return bool(prior) or _market_data_seen(messages)


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol restricted to tokens that can never contain the "error" or "unavailable"
# substrings, so the classification of a usable assessment is decided purely by
# its structure (not by incidental text in free-form fields).
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_event_risk = st.sampled_from(["clear", "imminent", "through_event"])
_event_recommendation = st.sampled_from(
    ["proceed", "size_down", "shorten_horizon", "stand_aside"]
)
_holding_horizon = st.sampled_from(["intraday", "multi_session"])
_serialization_style = st.sampled_from(["json", "repr"])

# days_until_event: a finite non-negative number or null.
_days_value = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=365),
    st.floats(min_value=0.0, max_value=365.0, allow_nan=False, allow_infinity=False),
)

# An ISO-like event_date string (never contains "error"/"unavailable").
_event_date = st.sampled_from(
    ["2025-01-15", "2025-03-31", "2025-07-04", "2025-11-20", "2026-02-01"]
)


@st.composite
def _usable_event_content(draw):
    """A full Event_Assessment string — neither an error nor an Unavailable_Marker."""
    payload = {
        "days_until_event": draw(_days_value),
        "event_risk": draw(_event_risk),
        "event_recommendation": draw(_event_recommendation),
        "holding_horizon": draw(_holding_horizon),
        "event_date": draw(_event_date),
        "symbol": draw(_symbol),
    }
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _error_event_content(draw):
    """An error result string for the event tool (carries an ``error`` key)."""
    payload = {
        "symbol": draw(_symbol),
        "holding_horizon": draw(_holding_horizon),
        "error": draw(
            st.sampled_from(
                [
                    "symbol must be a non-empty string",
                    "connection refused",
                    "contract_violation",
                    "no data",
                ]
            )
        ),
    }
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _unavailable_event_content(draw):
    """An Unavailable_Marker result string for the event tool.

    Per AD-3 / R5.1, ``event_risk`` and ``event_recommendation`` are omitted.
    """
    payload = {
        "symbol": draw(_symbol),
        "holding_horizon": draw(_holding_horizon),
        "unavailable": True,
        "reason": draw(
            st.sampled_from(
                [
                    "no event source configured",
                    "no upcoming event known for symbol",
                    "event source unreachable: timeout",
                    "event gate disabled",
                    "invalid timestamp: expected a finite epoch-millisecond number, got None",
                ]
            )
        ),
    }
    return _serialize(payload, draw(_serialization_style))


# ─────────────────────────────────────────────────────────────────────────────
# Property 18: market-data gate classification and monotonicity
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 18: The market-data gate classifies event results correctly and stays monotone
@settings(max_examples=200, deadline=None)
@given(
    usable=_usable_event_content(),
    error=_error_event_content(),
    unavailable=_unavailable_event_content(),
    prior_seen=st.booleans(),
    trailing=st.lists(
        st.one_of(_error_event_content(), _unavailable_event_content()),
        min_size=0,
        max_size=5,
    ),
)
def test_property_18_event_market_data_gate_classification_and_monotonicity(
    usable, error, unavailable, prior_seen, trailing
):
    """Validates: Requirements 6.4, 6.5

    (6.4) A usable ``get_event_risk`` result (a full Event_Assessment) sets
          ``market_data_seen``.
    (6.5) An error-only or unavailable-only event result does NOT set it. A
          usable assessment counts; an unavailable/error event result does NOT
          by itself satisfy the gate. Once the flag has latched true it stays
          true regardless of subsequent error / unavailable event results
          (monotonicity).
    """
    # Precondition: the event tool participates in the gate at all (task 5.1).
    assert EVENT_TOOL in MARKET_DATA_TOOL_NAMES

    usable_msg = _tool_message(usable)
    error_msg = _tool_message(error)
    unavailable_msg = _tool_message(unavailable)

    # ── R6.4: a usable event assessment, on its own, satisfies the gate ──────
    assert _market_data_seen([usable_msg]) is True

    # ── R6.5: an error-only or unavailable-only event result does NOT ────────
    assert _market_data_seen([error_msg]) is False
    assert _market_data_seen([unavailable_msg]) is False
    # Even both together (still no usable data) leave the flag unset.
    assert _market_data_seen([error_msg, unavailable_msg]) is False

    # The classifying predicates back this up directly.
    assert graph._tool_result_is_error(error) is True
    assert graph._tool_result_is_unavailable(unavailable) is True
    assert graph._tool_result_is_error(usable) is False
    assert graph._tool_result_is_unavailable(usable) is False

    # ── R6.5: monotonicity of the latch ──────────────────────────────────────
    # Build a trailing run of error/unavailable event messages (no usable data).
    trailing_msgs = [_tool_message(c) for c in trailing]

    # The trailing messages alone never satisfy the gate (no usable data).
    assert _market_data_seen(trailing_msgs) is False

    # A non-usable event result never turns the flag on by itself: starting from
    # False, an error/unavailable trailing run leaves it False.
    assert _latch(False, trailing_msgs) is False

    # Once the flag is already true (prior_seen=True), a non-usable event result
    # leaves it True (never flips off).
    if prior_seen:
        assert _latch(prior_seen, trailing_msgs) is True
        assert _latch(prior_seen, [error_msg, unavailable_msg] + trailing_msgs) is True

    # Starting from False, a usable result flips the latch True, and it remains
    # true through any number of subsequent error/unavailable event results.
    latched = _latch(False, [usable_msg])
    assert latched is True
    assert _latch(latched, trailing_msgs) is True
    assert _latch(latched, [error_msg, unavailable_msg] + trailing_msgs) is True

    # iff direction: the gate is True iff at least one usable Event_Assessment is
    # present — a usable result anywhere in a mixed sequence satisfies it,
    # regardless of position.
    assert _market_data_seen(trailing_msgs + [usable_msg]) is True
    assert _market_data_seen([error_msg, usable_msg, unavailable_msg]) is True
