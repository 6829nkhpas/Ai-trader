"""Property-based test for the session scope boundary (graph.py, task 5.6).

Feature: session-expiry-awareness

This module implements design **Property 30: The session context never modifies
or blocks a committed decision**:

    For any committed Deep_Quant_Agent decision (arbitrary BUY/SELL/HOLD action
    and arbitrary execution levels — entry / stop_loss / take_profit — plus a
    conviction score) and ANY ``get_session_context`` result present in message
    history — a conforming Session_Label of any Time_Favorability
    (favorable / unfavorable / neutral), an Unavailable_Marker, or NO session
    result at all (absent) — assembling the defensibility record via
    ``build_defensibility_record`` leaves the committed decision's action and
    execution levels byte-for-byte UNCHANGED, and the record reports the
    committed action verbatim (the session context never flips or blocks it).
    Even when the time window is ``unfavorable``, the trade is NOT blocked: the
    record is still produced and the committed action stands. The session
    context's effect is limited to prompt guidance and defensibility surfacing
    (R13.4, R13.5).

Validates: Requirements 13.4, 13.5.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` reads
    the most-recent ``get_session_context`` result and attaches a session entry
    (with a ``trade_in_unfavorable_window`` statement when ``unfavorable`` + a
    directional action) WITHOUT touching the decision's action or levels and
    WITHOUT blocking the trade.

The real LLM / Rust server is never invoked. Tool results are delivered as
``langchain_core.messages.ToolMessage`` objects — the genuine message shape the
record code reads — serialized both as JSON and as Python dict-repr strings,
since both quoting styles flow through the stack.

The sys.path / import pattern mirrors the other session defensibility property
tests: the service directory (one level up) is prepended to ``sys.path`` so
``graph`` is importable when pytest is run from anywhere.
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

SESSION_TOOL = "get_session_context"

# The committed-decision fields the session context must never touch. The first
# three are the execution levels; ``conviction_score`` is the agent's committed
# conviction (the session context biases conviction only via prompt guidance,
# never by mutating a committed decision).
_EXECUTION_LEVEL_FIELDS = ("entry", "stop_loss", "take_profit")
_PROTECTED_FIELDS = _EXECUTION_LEVEL_FIELDS + ("conviction_score",)

_SESSION_PHASES = (
    "pre_open", "opening", "morning", "midday", "afternoon", "closing", "post_close",
)


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python dict-repr: single quotes, True/None tokens


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol/timeframe restricted to tokens that can never contain the "error" or
# "unavailable" substrings, so a label is classified purely by its structure.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_session_phase = st.sampled_from(_SESSION_PHASES)
# All three favorabilities are exercised (favorable / unfavorable / neutral),
# plus the unavailable and absent cases below.
_favorability = st.sampled_from(["favorable", "unfavorable", "neutral"])
_serialization_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# minutes_since_open / minutes_until_close are each a finite non-negative number
# or null (None), per the session contract.
_minutes = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=375.0, allow_nan=False, allow_infinity=False),
)
_days_until_expiry = st.integers(min_value=0, max_value=6)
# Finite execution levels and a unit-interval conviction score.
_level = st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False)
_conviction = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@st.composite
def _session_label(draw):
    """Draw a conforming Session_Label of any Time_Favorability."""
    return {
        "session_phase": draw(_session_phase),
        "minutes_since_open": draw(_minutes),
        "minutes_until_close": draw(_minutes),
        "expiry_context": {
            "is_expiry_day": draw(st.booleans()),
            "days_until_expiry": draw(_days_until_expiry),
        },
        "time_favorability": draw(_favorability),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }


@st.composite
def _session_unavailable(draw):
    """Draw an Unavailable_Marker (no fabricated session fields)."""
    return {
        "unavailable": True,
        "reason": draw(st.sampled_from([
            "invalid timestamp: expected a finite epoch-millisecond number, got None",
            "candle retrieval failed",
            "empty candle result",
        ])),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }


# A get_session_context result is EITHER a usable label, an unavailable marker,
# OR entirely absent (no session result in message history at all).
_ABSENT = "__absent__"
_session_result = st.one_of(_session_label(), _session_unavailable(), st.just(_ABSENT))


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
# Property 30: the session context never modifies or blocks a committed decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 30: The session context never modifies or blocks a committed decision
@settings(max_examples=200, deadline=None)
@given(
    decision=_committed_decision(),
    result=_session_result,
    style=_serialization_style,
)
def test_property_30_session_never_modifies_or_blocks_decision(decision, result, style):
    """Validates: Requirements 13.4, 13.5

    For ANY committed decision (BUY/SELL/HOLD + execution levels + conviction)
    and ANY ``get_session_context`` result (a Session_Label of any favorability
    — favorable / unfavorable / neutral — an Unavailable_Marker, or absent):

      * ``build_defensibility_record`` NEVER raises;
      * the committed decision's action, execution levels
        (entry / stop_loss / take_profit), and conviction_score are
        byte-for-byte unchanged (``==`` the deep-copied snapshot taken before
        the build) — R13.4;
      * the record's committed action equals the input action (the session
        context did not flip or block it) — R13.4;
      * even when the time window is ``unfavorable``, the trade is NOT blocked:
        the record is still produced and the committed action stands — R13.5.
    """
    # Build message history: a single get_session_context ToolMessage carrying
    # the result, OR no session message at all (the absent case).
    if result == _ABSENT:
        messages = []
    else:
        messages = [
            ToolMessage(
                content=_serialize(result, style),
                name=SESSION_TOOL,
                tool_call_id="session-call-1",
            )
        ]

    # Snapshot the committed decision BEFORE building the record.
    action_before = decision["action"]
    decision_before = copy.deepcopy(decision)

    # Build never raises, for any session result / committed action combo.
    record = build_defensibility_record(messages, decision, mode="FIND")

    # ── The committed decision is byte-for-byte unchanged (R13.4) ────────────
    # Action unchanged.
    assert decision["action"] == action_before
    # Every protected field (execution levels + conviction) unchanged.
    for field in _PROTECTED_FIELDS:
        assert decision[field] == decision_before[field], (
            f"session record-building must not alter committed field {field!r}: "
            f"before={decision_before[field]!r} after={decision[field]!r}"
        )
    # The whole committed decision object is untouched (no field added/removed).
    assert decision == decision_before, (
        "record-building must not modify the committed decision; "
        f"before={decision_before!r} after={decision!r}"
    )

    # ── The record surfaces, never overrides or blocks, the decision ─────────
    # The record reports the committed action verbatim — the session context did
    # not flip it to a different action and did not block it (R13.4). A record is
    # always returned (the trade is never blocked, R13.5).
    assert isinstance(record, dict)
    assert record["action"] == action_before

    # A session entry is present as defensibility surfacing only.
    assert "session" in record
    session = record["session"]

    is_label = isinstance(result, dict) and result.get("unavailable") is not True
    if is_label and result["time_favorability"] == "unfavorable":
        # An unfavorable window against a directional trade adds an explicit
        # unfavorable-window STATEMENT, but that statement is surfacing only —
        # it never blocks the trade nor alters the committed action/levels.
        if action_before in ("BUY", "SELL"):
            assert isinstance(session.get("trade_in_unfavorable_window"), str)
        # The committed action still stands despite the unfavorable window
        # (R13.5) — the record was produced and nothing was blocked.
        assert record["action"] == action_before
