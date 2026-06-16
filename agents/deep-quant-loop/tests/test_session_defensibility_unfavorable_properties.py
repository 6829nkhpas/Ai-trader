# Feature: session-expiry-awareness, Property 19: An unfavorable directional trade records the unfavorable-window statement
"""Property-based test for the session unfavorable-window statement
(graph.py, task 5.5).

Feature: session-expiry-awareness

This module implements design **Property 19: An unfavorable directional trade
records the unfavorable-window statement**:

    For any decision whose most recent session Time_Favorability is
    ``unfavorable`` and whose committed action is BUY or SELL, the
    Defensibility_Record's session entry includes an explicit statement
    (``trade_in_unfavorable_window``) that the committed trade is taken in an
    unfavorable time window. For HOLD actions or for a non-``unfavorable``
    favorability (``favorable`` / ``neutral``), no such statement is added.

Validates: Requirements 8.4.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the record whose ``"session"`` entry gains the
    ``trade_in_unfavorable_window`` key only when the most-recent
    ``get_session_context`` Time_Favorability is ``unfavorable`` AND the action
    is directional (BUY/SELL). The statement is also surfaced in the
    human-readable ``summary``.

The real LLM / Rust server is never invoked. A real ``langchain_core`` 
``ToolMessage`` (``type == "tool"`` with ``.name`` and ``.content``) carries the
serialized session result — exactly the shape the record code reads. Results are
serialized both as JSON and as Python dict-repr strings, since both quoting
styles flow through the stack.
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

from graph import build_defensibility_record  # noqa: E402

SESSION_TOOL = "get_session_context"

SESSION_PHASES = [
    "pre_open", "opening", "morning", "midday", "afternoon", "closing", "post_close",
]
TIME_FAVORABILITY = ["favorable", "unfavorable", "neutral"]


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python dict-repr: single quotes, True/None tokens


def _tool_message(payload, style):
    """Wrap a serialized session result in a real langchain ToolMessage."""
    return ToolMessage(
        content=_serialize(payload, style),
        name=SESSION_TOOL,
        tool_call_id="call_session_1",
    )


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol/timeframe restricted to tokens that can never contain the "error" or
# "unavailable" substrings, so the result is classified purely by its structure.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_phase = st.sampled_from(SESSION_PHASES)
_favorability = st.sampled_from(TIME_FAVORABILITY)
_serialization_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# minutes-since-open / minutes-until-close: a finite non-negative number or null.
_minutes = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=1440.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _session_label(draw, favorability=None):
    """Draw a conforming, usable Session_Label, optionally pinning favorability."""
    is_expiry_day = draw(st.booleans())
    return {
        "session_phase": draw(_phase),
        "minutes_since_open": draw(_minutes),
        "minutes_until_close": draw(_minutes),
        "expiry_context": {
            "is_expiry_day": is_expiry_day,
            "days_until_expiry": 0 if is_expiry_day else draw(st.integers(min_value=1, max_value=6)),
        },
        "time_favorability": draw(_favorability) if favorability is None else favorability,
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 19: an unfavorable directional trade records the unfavorable-window
# statement; HOLD / non-unfavorable favorability do not.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 19: An unfavorable directional trade records the unfavorable-window statement
@settings(max_examples=200, deadline=None)
@given(
    label=_session_label(),
    style=_serialization_style,
    action=_action,
)
def test_property_19_unfavorable_directional_records_window_statement(label, style, action):
    """Validates: Requirements 8.4

    The ``trade_in_unfavorable_window`` statement is present in the session
    entry exactly when the most-recent Time_Favorability is ``unfavorable`` AND
    the committed action is directional (BUY or SELL); it is absent for a HOLD
    action or for a ``favorable`` / ``neutral`` favorability. Building the record
    never raises, and a present statement names the unfavorable window and is
    surfaced in the record summary.
    """
    messages = [_tool_message(label, style)]
    decision = {"action": action, "source": "declare_trade"}

    record = build_defensibility_record(messages, decision, mode="FIND")
    session = record["session"]

    # The session label is usable, so the entry mirrors it (available is True).
    assert session.get("available") is True

    should_warn = label["time_favorability"] == "unfavorable" and action in ("BUY", "SELL")

    if should_warn:
        statement = session.get("trade_in_unfavorable_window")
        # Present and a non-empty string (R8.4).
        assert isinstance(statement, str) and statement.strip(), (
            f"expected a non-empty unfavorable-window statement for action={action}, "
            f"time_favorability={label['time_favorability']}, got {statement!r}"
        )
        # It explicitly names the unfavorable time window and the session phase (R8.4).
        assert "unfavorable time window" in statement
        assert f"session_phase={label['session_phase']}" in statement
        assert "time_favorability=unfavorable" in statement
        # It is surfaced in the human-readable summary too.
        assert statement in record["summary"]
    else:
        # Statement absent for HOLD or for favorable/neutral favorability (R8.4).
        assert "trade_in_unfavorable_window" not in session, (
            f"unfavorable-window statement must be absent for action={action}, "
            f"time_favorability={label['time_favorability']}, got "
            f"{session.get('trade_in_unfavorable_window')!r}"
        )


# Feature: session-expiry-awareness, Property 19: An unfavorable directional trade records the unfavorable-window statement
@settings(max_examples=100, deadline=None)
@given(
    label=_session_label(favorability="unfavorable"),
    style=_serialization_style,
    action=st.sampled_from(["BUY", "SELL"]),
)
def test_property_19_unfavorable_buy_sell_always_records_statement(label, style, action):
    """Validates: Requirements 8.4

    Focused control: every unfavorable + directional (BUY/SELL) decision records
    the unfavorable-window statement, regardless of session phase or serialization.
    """
    messages = [_tool_message(label, style)]
    decision = {"action": action, "source": "declare_trade"}

    record = build_defensibility_record(messages, decision, mode="FIND")
    statement = record["session"].get("trade_in_unfavorable_window")

    assert isinstance(statement, str) and "unfavorable time window" in statement
    assert action in statement


# Feature: session-expiry-awareness, Property 19: An unfavorable directional trade records the unfavorable-window statement
@settings(max_examples=100, deadline=None)
@given(
    label=_session_label(favorability="unfavorable"),
    style=_serialization_style,
)
def test_property_19_unfavorable_hold_never_records_statement(label, style):
    """Validates: Requirements 8.4

    Focused control: an unfavorable window with a HOLD action never records the
    unfavorable-window statement (the statement is reserved for directional trades).
    """
    messages = [_tool_message(label, style)]
    decision = {"action": "HOLD", "source": "declare_trade"}

    record = build_defensibility_record(messages, decision, mode="FIND")

    assert "trade_in_unfavorable_window" not in record["session"]
