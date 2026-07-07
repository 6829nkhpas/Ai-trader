# Feature: earnings-event-risk-gate, Property 21: A through-event directional trade records the held-through-event statement
"""Property-based test for the held-through-event statement
(graph.py, task 5.5).

Feature: earnings-event-risk-gate

This module implements design **Property 21: A through-event directional trade
records the held-through-event statement**:

    For any decision whose most recent event Event_Risk is ``through_event`` and
    whose committed action is BUY or SELL, the Defensibility_Record's event entry
    includes an explicit statement (``trade_held_through_event``) that the
    committed trade would be held through a scheduled event. For HOLD actions or
    for a non-``through_event`` risk (``clear`` / ``imminent``), no such statement
    is added, and the committed decision's action and execution levels are never
    modified.

Validates: Requirements 8.4.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the record whose ``"event"`` entry gains the
    ``trade_held_through_event`` key only when the most-recent
    ``get_event_risk`` Event_Risk is ``through_event`` AND the action is
    directional (BUY/SELL). The statement is also surfaced in the human-readable
    ``summary``.

The real LLM / Rust server / event source is never invoked. A real
``langchain_core`` ``ToolMessage`` (``type == "tool"`` with ``.name`` and
``.content``) carries the serialized event result — exactly the shape the record
code reads. Results are serialized both as JSON and as Python dict-repr strings,
since both quoting styles flow through the stack.
"""

import datetime
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

EVENT_TOOL = "get_event_risk"

EVENT_RISK_STATES = ["clear", "imminent", "through_event"]
EVENT_RECOMMENDATIONS = ["proceed", "size_down", "shorten_horizon", "stand_aside"]
HOLDING_HORIZONS = ["intraday", "multi_session"]


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python dict-repr: single quotes, True/None tokens


def _tool_message(payload, style):
    """Wrap a serialized event result in a real langchain ToolMessage."""
    return ToolMessage(
        content=_serialize(payload, style),
        name=EVENT_TOOL,
        tool_call_id="call_event_1",
    )


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol restricted to tokens that can never contain the "error" or "unavailable"
# substrings, so the result is classified purely by its structure.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_event_risk = st.sampled_from(EVENT_RISK_STATES)
_event_recommendation = st.sampled_from(EVENT_RECOMMENDATIONS)
_holding_horizon = st.sampled_from(HOLDING_HORIZONS)
_serialization_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# days_until_event: a finite non-negative number or null.
_days = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=365.0, allow_nan=False, allow_infinity=False),
)

# An ISO-like event_date string (must be a string for the assessment to be usable).
_event_date = st.dates(
    min_value=datetime.date(2020, 1, 1),
    max_value=datetime.date(2030, 12, 31),
).map(lambda d: d.isoformat())


@st.composite
def _event_assessment(draw, event_risk=None):
    """Draw a conforming, usable Event_Assessment, optionally pinning event_risk."""
    return {
        "event_risk": draw(_event_risk) if event_risk is None else event_risk,
        "event_recommendation": draw(_event_recommendation),
        "days_until_event": draw(_days),
        "event_date": draw(_event_date),
        "symbol": draw(_symbol),
        "holding_horizon": draw(_holding_horizon),
    }


# Committed execution levels the record echoes back verbatim (used to prove the
# event context never modifies the committed decision's levels).
_LEVELS = {"entry": 100.0, "stop_loss": 95.0, "take_profit": 115.0}


def _decision(action):
    """A committed decision carrying structured execution levels."""
    return {
        "action": action,
        "source": "declare_trade",
        "entry": _LEVELS["entry"],
        "stop_loss": _LEVELS["stop_loss"],
        "take_profit": _LEVELS["take_profit"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 21: a through-event directional trade records the held-through-event
# statement; HOLD / non-through_event risk do not — and the committed action and
# levels are never modified.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 21: A through-event directional trade records the held-through-event statement
@settings(max_examples=25, deadline=None)
@given(
    assessment=_event_assessment(),
    style=_serialization_style,
    action=_action,
)
def test_property_21_through_event_directional_records_held_statement(assessment, style, action):
    """Validates: Requirements 8.4

    The ``trade_held_through_event`` statement is present in the event entry
    exactly when the most-recent Event_Risk is ``through_event`` AND the committed
    action is directional (BUY or SELL); it is absent for a HOLD action or for a
    ``clear`` / ``imminent`` risk. Building the record never raises, a present
    statement names the scheduled event and is surfaced in the record summary,
    and the committed action and execution levels are never modified.
    """
    messages = [_tool_message(assessment, style)]
    decision = _decision(action)

    record = build_defensibility_record(messages, decision, mode="FIND")
    event = record["event"]

    # The event assessment is usable, so the entry mirrors it (available is True).
    assert event.get("available") is True

    should_warn = assessment["event_risk"] == "through_event" and action in ("BUY", "SELL")

    if should_warn:
        statement = event.get("trade_held_through_event")
        # Present and a non-empty string (R8.4).
        assert isinstance(statement, str) and statement.strip(), (
            f"expected a non-empty held-through-event statement for action={action}, "
            f"event_risk={assessment['event_risk']}, got {statement!r}"
        )
        # It explicitly states the trade is held THROUGH a scheduled event (R8.4).
        assert "held THROUGH a scheduled" in statement
        assert "event_risk=through_event" in statement
        assert f"event_date={assessment['event_date']}" in statement
        # It is surfaced in the human-readable summary too.
        assert statement in record["summary"]
    else:
        # Statement absent for HOLD or for a clear/imminent risk (R8.4).
        assert "trade_held_through_event" not in event, (
            f"held-through-event statement must be absent for action={action}, "
            f"event_risk={assessment['event_risk']}, got "
            f"{event.get('trade_held_through_event')!r}"
        )

    # The event context is a defensibility surface only: the committed action and
    # execution levels are echoed back verbatim, never modified (R12.3-R12.5).
    assert record["action"] == action
    assert record["levels"] == _LEVELS


# Feature: earnings-event-risk-gate, Property 21: A through-event directional trade records the held-through-event statement
@settings(max_examples=25, deadline=None)
@given(
    assessment=_event_assessment(event_risk="through_event"),
    style=_serialization_style,
    action=st.sampled_from(["BUY", "SELL"]),
)
def test_property_21_through_event_buy_sell_always_records_statement(assessment, style, action):
    """Validates: Requirements 8.4

    Focused control: every through_event + directional (BUY/SELL) decision records
    the held-through-event statement, regardless of the recommendation, day count,
    or serialization — and the committed levels are never modified.
    """
    messages = [_tool_message(assessment, style)]
    decision = _decision(action)

    record = build_defensibility_record(messages, decision, mode="FIND")
    statement = record["event"].get("trade_held_through_event")

    assert isinstance(statement, str) and "held THROUGH a scheduled" in statement
    assert action in statement
    assert record["action"] == action
    assert record["levels"] == _LEVELS


# Feature: earnings-event-risk-gate, Property 21: A through-event directional trade records the held-through-event statement
@settings(max_examples=25, deadline=None)
@given(
    assessment=_event_assessment(event_risk="through_event"),
    style=_serialization_style,
)
def test_property_21_through_event_hold_never_records_statement(assessment, style):
    """Validates: Requirements 8.4

    Focused control: a through_event risk with a HOLD action never records the
    held-through-event statement (the statement is reserved for directional trades).
    """
    messages = [_tool_message(assessment, style)]
    decision = _decision("HOLD")

    record = build_defensibility_record(messages, decision, mode="FIND")

    assert "trade_held_through_event" not in record["event"]
    assert record["action"] == "HOLD"
