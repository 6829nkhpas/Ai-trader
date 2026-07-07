"""Property-based test for the event scope boundary (graph.py, task 5.6).

Feature: earnings-event-risk-gate

This module implements design **Property 28: The event context never modifies
or blocks a committed decision**:

    For any committed Deep_Quant_Agent decision (arbitrary BUY/SELL/HOLD action
    and arbitrary execution levels — entry / stop_loss / take_profit — plus a
    conviction score) and ANY ``get_event_risk`` result present in message
    history — a conforming Event_Assessment of any Event_Risk_State
    (clear / imminent / through_event) with any Event_Recommendation, an
    Unavailable_Marker, or NO event result at all (absent) — assembling the
    defensibility record via ``build_defensibility_record`` leaves the committed
    decision's action, conviction, and execution levels byte-for-byte UNCHANGED,
    and the record reports the committed action verbatim (the event context
    never flips or blocks it). Even when ``event_risk == "through_event"``, the
    trade is NOT blocked: the record is still produced and the committed action
    stands. The event context's effect is limited to prompt guidance and
    defensibility surfacing (R12.4, R12.5).

Validates: Requirements 12.4, 12.5.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` reads
    the most-recent ``get_event_risk`` result and attaches an event entry (with
    a ``trade_held_through_event`` statement when ``through_event`` + a
    directional action) WITHOUT touching the decision's action or levels and
    WITHOUT blocking the trade.

The real LLM / Rust server is never invoked. Tool results are delivered as
``langchain_core.messages.ToolMessage`` objects — the genuine message shape the
record code reads — serialized both as JSON and as Python dict-repr strings,
since both quoting styles flow through the stack.

The sys.path / import pattern mirrors the sibling ``session`` / ``forecast``
defensibility property tests: the service directory (one level up) is prepended
to ``sys.path`` so ``graph`` is importable when pytest is run from anywhere.
"""

import copy
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

# The committed-decision fields the event context must never touch. The first
# three are the execution levels; ``conviction_score`` is the agent's committed
# conviction (the event context biases conviction only via prompt guidance,
# never by mutating a committed decision).
_EXECUTION_LEVEL_FIELDS = ("entry", "stop_loss", "take_profit")
_PROTECTED_FIELDS = _EXECUTION_LEVEL_FIELDS + ("conviction_score",)

# Fixed enums mirrored from tools.py (EVENT_RISK_STATES / EVENT_RECOMMENDATIONS).
_EVENT_RISK_STATES = ("clear", "imminent", "through_event")
_EVENT_RECOMMENDATIONS = ("proceed", "size_down", "shorten_horizon", "stand_aside")
_HOLDING_HORIZONS = ("intraday", "multi_session")


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python dict-repr: single quotes, True/None tokens


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol restricted to tokens that can never contain the "error" or
# "unavailable" substrings, so a result is classified purely by its structure.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_event_risk = st.sampled_from(_EVENT_RISK_STATES)
_event_recommendation = st.sampled_from(_EVENT_RECOMMENDATIONS)
_holding_horizon = st.sampled_from(_HOLDING_HORIZONS)
_serialization_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# days_until_event is a finite non-negative number or null (None), per the
# event contract.
_days_until_event = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=365),
    st.floats(min_value=0.0, max_value=365.0, allow_nan=False, allow_infinity=False),
)
# An event_date string identifying the reference Scheduled_Event.
_event_date = st.sampled_from([
    "2024-01-15", "2024-06-30", "2025-03-10", "2025-12-31", "2024-11-05",
])
# Finite execution levels and a unit-interval conviction score.
_level = st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False)
_conviction = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@st.composite
def _event_assessment(draw):
    """Draw a conforming Event_Assessment of any Event_Risk_State."""
    return {
        "event_risk": draw(_event_risk),
        "event_recommendation": draw(_event_recommendation),
        "days_until_event": draw(_days_until_event),
        "event_date": draw(_event_date),
        "symbol": draw(_symbol),
        "holding_horizon": draw(_holding_horizon),
    }


@st.composite
def _event_unavailable(draw):
    """Draw an Unavailable_Marker (no fabricated event fields)."""
    return {
        "unavailable": True,
        "reason": draw(st.sampled_from([
            "invalid timestamp: expected a finite epoch-millisecond number, got None",
            "no event source configured",
            "no upcoming event for the symbol",
            "event calendar retrieval failed",
            "gate disabled",
        ])),
        "symbol": draw(_symbol),
        "holding_horizon": draw(_holding_horizon),
    }


# A get_event_risk result is EITHER a usable assessment, an unavailable marker,
# OR entirely absent (no event result in message history at all).
_ABSENT = "__absent__"
_event_result = st.one_of(_event_assessment(), _event_unavailable(), st.just(_ABSENT))


@st.composite
def _committed_decision(draw):
    """Draw a committed decision with an arbitrary action, levels, conviction."""
    decision = {
        "action": draw(_action),
        "source": "declare_trade",
        "conviction_score": draw(_conviction),
    }
    for field in _EXECUTION_LEVEL_FIELDS:
        decision[field] = draw(_level)
    return decision


# ─────────────────────────────────────────────────────────────────────────────
# Property 28: the event context never modifies or blocks a committed decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 28: The event context never modifies or blocks a committed decision
@settings(max_examples=25, deadline=None)
@given(
    decision=_committed_decision(),
    result=_event_result,
    style=_serialization_style,
)
def test_property_28_event_never_modifies_or_blocks_decision(decision, result, style):
    """Validates: Requirements 12.4, 12.5

    For ANY committed decision (BUY/SELL/HOLD + execution levels + conviction)
    and ANY ``get_event_risk`` result (an Event_Assessment of any Event_Risk_State
    — clear / imminent / through_event — an Unavailable_Marker, or absent):

      * ``build_defensibility_record`` NEVER raises;
      * the committed decision's action, execution levels
        (entry / stop_loss / take_profit), and conviction_score are
        byte-for-byte unchanged (``==`` the deep-copied snapshot taken before
        the build) — R12.4;
      * the record's committed action equals the input action (the event
        context did not flip or block it) — R12.4;
      * even when ``event_risk == "through_event"``, the trade is NOT blocked:
        the record is still produced and the committed action stands — R12.5.
    """
    # Build message history: a single get_event_risk ToolMessage carrying the
    # result, OR no event message at all (the absent case).
    if result == _ABSENT:
        messages = []
    else:
        messages = [
            ToolMessage(
                content=_serialize(result, style),
                name=EVENT_TOOL,
                tool_call_id="event-call-1",
            )
        ]

    # Snapshot the committed decision BEFORE building the record.
    action_before = decision["action"]
    decision_before = copy.deepcopy(decision)

    # Build never raises, for any event result / committed action combo.
    record = build_defensibility_record(messages, decision, mode="FIND")

    # ── The committed decision is byte-for-byte unchanged (R12.4) ────────────
    # Action unchanged.
    assert decision["action"] == action_before
    # Every protected field (execution levels + conviction) unchanged.
    for field in _PROTECTED_FIELDS:
        assert decision[field] == decision_before[field], (
            f"event record-building must not alter committed field {field!r}: "
            f"before={decision_before[field]!r} after={decision[field]!r}"
        )
    # The whole committed decision object is untouched (no field added/removed).
    assert decision == decision_before, (
        "record-building must not modify the committed decision; "
        f"before={decision_before!r} after={decision!r}"
    )

    # ── The record surfaces, never overrides or blocks, the decision ─────────
    # The record reports the committed action verbatim — the event context did
    # not flip it to a different action and did not block it (R12.4). A record is
    # always returned (the trade is never blocked, R12.5).
    assert isinstance(record, dict)
    assert record["action"] == action_before

    # An event entry is present as defensibility surfacing only.
    assert "event" in record
    event = record["event"]

    is_assessment = isinstance(result, dict) and result.get("unavailable") is not True
    if is_assessment and result["event_risk"] == "through_event":
        # A through-event risk against a directional trade adds an explicit
        # held-through-event STATEMENT, but that statement is surfacing only —
        # it never blocks the trade nor alters the committed action/levels.
        if action_before in ("BUY", "SELL"):
            assert isinstance(event.get("trade_held_through_event"), str)
        # The committed action still stands despite the through-event risk
        # (R12.5) — the record was produced and nothing was blocked.
        assert record["action"] == action_before
