# Feature: session-expiry-awareness, Property 16: The market-data gate classifies session results correctly and stays monotone
"""Property-based test for the market-data gate over `get_session_context` (graph.py, task 5.2).

Feature: session-expiry-awareness

This module implements design **Property 16: The market-data gate classifies
session results correctly and stays monotone**:

    A usable ``get_session_context`` result (a full Session_Label — neither an
    error result nor an explicit Unavailable_Marker) sets the
    ``market_data_seen`` flag; an error result or an Unavailable_Marker does NOT
    set the flag on its own; and once the flag has latched true within a run it
    stays true regardless of any subsequent error / unavailable session results.

Validates: Requirements 6.4, 6.5.

The implementation under test lives in ``graph.py``:
  - ``MARKET_DATA_TOOL_NAMES`` (must contain ``get_session_context``)
  - ``_market_data_seen(messages)`` — the classifier used to maintain the flag
  - ``_tool_result_is_error`` / ``_tool_result_is_unavailable`` — the predicates

The latch itself is the expression maintained in ``call_model``:
``market_data_seen = bool(state.get("market_data_seen")) or _market_data_seen(messages)``.
The monotonicity property below models that latch directly.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the gate code reads. Session tool results are
serialized both as JSON (``{"...": ...}``) and as Python dict-repr
(``{'...': ...}``) strings, since both quoting styles flow through the stack.

The sys.path / import pattern mirrors ``tests/test_rs_market_data_gate_properties.py``:
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

SESSION_TOOL = "get_session_context"


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python dict-repr: single quotes, True/None tokens


def _tool_message(content):
    """Build a LangChain ToolMessage exactly as the gate code reads it."""
    return ToolMessage(content=content, name=SESSION_TOOL, tool_call_id="call_session")


def _latch(prior, messages):
    """Model the flag latch maintained in ``call_model`` (graph.py).

    ``market_data_seen = bool(state.get('market_data_seen')) or
    _market_data_seen(messages)``.
    """
    return bool(prior) or _market_data_seen(messages)


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol/timeframe restricted to tokens that can never contain the "error" or
# "unavailable" substrings, so the classification of a usable label is decided
# purely by its structure (not by incidental text in free-form fields).
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_session_phase = st.sampled_from(
    ["pre_open", "opening", "morning", "midday", "afternoon", "closing", "post_close"]
)
_time_favorability = st.sampled_from(["favorable", "unfavorable", "neutral"])
_serialization_style = st.sampled_from(["json", "repr"])

# minutes_since_open / minutes_until_close: a finite non-negative number or null.
_minutes_value = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=400.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _usable_session_content(draw):
    """A full Session_Label string — neither an error nor an Unavailable_Marker."""
    payload = {
        "session_phase": draw(_session_phase),
        "minutes_since_open": draw(_minutes_value),
        "minutes_until_close": draw(_minutes_value),
        "expiry_context": {
            "is_expiry_day": draw(st.booleans()),
            "days_until_expiry": draw(st.integers(min_value=0, max_value=6)),
        },
        "time_favorability": draw(_time_favorability),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _error_session_content(draw):
    """An error result string for the session tool (carries an ``error`` key)."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "error": draw(
            st.sampled_from(
                [
                    "Failed to retrieve candles from Rust server: timeout",
                    "connection refused",
                    "contract_violation",
                    "no data",
                ]
            )
        ),
    }
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _unavailable_session_content(draw):
    """An Unavailable_Marker result string for the session tool.

    Per AD-5 / R5.2, ``session_phase`` and ``time_favorability`` are omitted.
    """
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "unavailable": True,
        "reason": draw(
            st.sampled_from(
                [
                    "invalid timestamp: expected a finite epoch-millisecond number, got None",
                    "retrieval timeout",
                    "no reference candle available",
                    "candle fetch failed",
                ]
            )
        ),
    }
    return _serialize(payload, draw(_serialization_style))


# ─────────────────────────────────────────────────────────────────────────────
# Property 16: market-data gate classification and monotonicity
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 16: The market-data gate classifies session results correctly and stays monotone
@settings(max_examples=200, deadline=None)
@given(
    usable=_usable_session_content(),
    error=_error_session_content(),
    unavailable=_unavailable_session_content(),
    prior_seen=st.booleans(),
    trailing=st.lists(
        st.one_of(_error_session_content(), _unavailable_session_content()),
        min_size=0,
        max_size=5,
    ),
)
def test_property_16_session_market_data_gate_classification_and_monotonicity(
    usable, error, unavailable, prior_seen, trailing
):
    """Validates: Requirements 6.4, 6.5

    (6.4) A usable ``get_session_context`` result (a full Session_Label) sets
          ``market_data_seen``.
    (6.5) An error-only or unavailable-only session result does NOT set it. A
          usable session label counts; an unavailable/error session result does
          NOT by itself satisfy the gate. Once the flag has latched true it
          stays true regardless of subsequent error / unavailable session
          results (monotonicity).
    """
    # Precondition: the session tool participates in the gate at all.
    assert SESSION_TOOL in MARKET_DATA_TOOL_NAMES

    usable_msg = _tool_message(usable)
    error_msg = _tool_message(error)
    unavailable_msg = _tool_message(unavailable)

    # ── R6.4: a usable session label, on its own, satisfies the gate ─────────
    assert _market_data_seen([usable_msg]) is True

    # ── R6.5: an error-only or unavailable-only session result does NOT ──────
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
    # Build a trailing run of error/unavailable session messages (no usable data).
    trailing_msgs = [_tool_message(c) for c in trailing]

    # The trailing messages alone never satisfy the gate (no usable data).
    assert _market_data_seen(trailing_msgs) is False

    # Once the flag is already true (prior_seen=True), it stays true regardless
    # of subsequent error/unavailable results.
    if prior_seen:
        assert _latch(prior_seen, trailing_msgs) is True

    # A usable result latches the flag true, and it remains true through any
    # number of subsequent error/unavailable session results.
    latched = _latch(False, [usable_msg])
    assert latched is True
    assert _latch(latched, trailing_msgs) is True
    assert _latch(latched, [error_msg, unavailable_msg] + trailing_msgs) is True

    # iff direction: the gate is True iff at least one usable Session_Label is
    # present — a usable result anywhere in a mixed sequence satisfies it,
    # regardless of position.
    assert _market_data_seen(trailing_msgs + [usable_msg]) is True
    assert _market_data_seen([error_msg, usable_msg, unavailable_msg]) is True
