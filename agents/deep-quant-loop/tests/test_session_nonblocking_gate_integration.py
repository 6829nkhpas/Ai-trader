# Feature: session-expiry-awareness, Integration: non-blocking degradation of an unavailable session result
"""Integration test for non-blocking session degradation (graph.py, task 10.3).

Feature: session-expiry-awareness

This module is an integration-flavoured test that an UNAVAILABLE
``get_session_context`` result is a benign, non-blocking missing input across the
two live-path consumers that decide whether a trade can proceed:

  1. the market-data gate (``_market_data_seen``), and
  2. the defensibility record builder (``build_defensibility_record``).

Validates: Requirements 5.3

    When a session result is unavailable, the Deep_Quant_Agent treats it as a
    missing optional input, proceeds with the remaining analysis, and does NOT
    abort, fail, or block the decision solely because the session context is
    unavailable.

Concretely the test asserts:

  1. A ``get_session_context`` Unavailable_Marker ToolMessage does NOT, on its
     own, satisfy the market-data gate — ``_market_data_seen([unavailable])`` is
     ``False`` — so an unavailable session result never sets ``market_data_seen``.
  2. An unavailable session result does not abort/block a committed decision:
     ``build_defensibility_record`` returns a record (no exception raised), the
     committed decision's action and execution levels are unchanged (the session
     context never modifies the decision), and ``record['session']['available']``
     is ``False``.
  3. (Integration flavour) When OTHER market-data tools returned usable data, an
     unavailable session result does not flip ``market_data_seen`` back to
     ``False`` — the gate stays satisfied (monotone) across a mixed message
     sequence.

The real LLM / Rust server is never invoked: the test feeds in-memory
``ToolMessage`` objects exactly as the gate and the record builder read them.

The sys.path / import pattern mirrors
``tests/test_session_market_data_gate_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``graph`` is importable when
pytest is run from anywhere.
"""

import json
import os
import sys

from langchain_core.messages import ToolMessage

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    MARKET_DATA_TOOL_NAMES,
    _market_data_seen,
    build_defensibility_record,
)

SESSION_TOOL = "get_session_context"


def _tool_message(name, payload):
    """Build a LangChain ToolMessage exactly as the gate / record code reads it."""
    return ToolMessage(
        content=json.dumps(payload),
        name=name,
        tool_call_id=f"call_{name}",
    )


def _unavailable_session_payload():
    """An Unavailable_Marker for the session tool.

    Per AD-5 / R5.2 the marker omits ``session_phase`` and ``time_favorability``
    rather than fabricating them.
    """
    return {
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "unavailable": True,
        "reason": "invalid timestamp: expected a finite epoch-millisecond number, got None",
    }


def _committed_buy_decision():
    """A committed directional (BUY) decision with structured execution levels."""
    return {
        "action": "BUY",
        "entry": 100.0,
        "stop_loss": 97.0,
        "take_profit": 106.0,
        "conviction_score": 7,
        "setup_validation": "Long off the 15m demand zone.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. An unavailable session result does NOT satisfy the market-data gate.
# ─────────────────────────────────────────────────────────────────────────────
def test_unavailable_session_does_not_set_market_data_seen():
    """Validates: Requirements 5.3 (gate facet)

    An Unavailable_Marker ``get_session_context`` result does NOT, on its own,
    set ``market_data_seen`` — it is a missing input, not usable market data.
    """
    assert SESSION_TOOL in MARKET_DATA_TOOL_NAMES

    unavailable_session_msg = _tool_message(SESSION_TOOL, _unavailable_session_payload())

    # The unavailable session result alone never satisfies the gate.
    assert _market_data_seen([unavailable_session_msg]) is False

    # The classifying predicates back this up: it is an explicit unavailable
    # marker (not usable data) and not an error.
    content = unavailable_session_msg.content
    assert graph._tool_result_is_unavailable(content) is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. An unavailable session result does NOT abort or block a committed decision.
# ─────────────────────────────────────────────────────────────────────────────
def test_unavailable_session_does_not_block_committed_decision():
    """Validates: Requirements 5.3 (decision facet)

    With only an unavailable session result in history, building the
    defensibility record for a committed BUY decision:
      * does not raise (the decision is not aborted/failed), and
      * records the session entry as unavailable (no fabricated favorability),
        while leaving the committed action and execution levels unchanged.
    """
    decision = _committed_buy_decision()
    decision_snapshot = dict(decision)

    messages = [_tool_message(SESSION_TOOL, _unavailable_session_payload())]

    # Must not raise — an unavailable session context never aborts the build.
    record = build_defensibility_record(messages, decision, mode="FIND")

    assert isinstance(record, dict)

    # The session entry is recorded as unavailable, with no fabricated label.
    assert record["session"]["available"] is False
    assert "session_phase" not in record["session"]
    assert "time_favorability" not in record["session"]

    # The committed decision's action and execution levels are unchanged: the
    # session context is a filter/defensibility surface, never a gate (R13.4/5).
    assert record["action"] == "BUY"
    assert record["levels"] == {"entry": 100.0, "stop_loss": 97.0, "take_profit": 106.0}

    # No "unfavorable time window" block statement is produced for an unavailable
    # session — absence is benign, never a fabricated block.
    assert "trade_in_unfavorable_window" not in record["session"]

    # The decision dict itself is not mutated by record assembly.
    assert decision == decision_snapshot


# ─────────────────────────────────────────────────────────────────────────────
# 3. (Integration) An unavailable session does not flip the gate back to False
#    once OTHER market-data tools have returned usable data (monotonicity).
# ─────────────────────────────────────────────────────────────────────────────
def test_unavailable_session_does_not_unset_gate_after_usable_market_data():
    """Validates: Requirements 5.3 (non-blocking, monotone facet)

    When another market-data tool (here ``get_consensus_report``) has returned
    usable data, an unavailable session result anywhere in the sequence does NOT
    flip ``market_data_seen`` back to False — degradation is non-blocking.
    """
    usable_consensus = _tool_message(
        "get_consensus_report",
        {"symbol": "RELIANCE", "timeframe": "15m", "atr_14": 2.5, "consensus": "bullish"},
    )
    unavailable_session = _tool_message(SESSION_TOOL, _unavailable_session_payload())

    # Usable market data alone satisfies the gate.
    assert _market_data_seen([usable_consensus]) is True

    # A mixed sequence — usable data plus an unavailable session result, in
    # either order — still satisfies the gate (the unavailable session does not
    # subtract from it).
    assert _market_data_seen([usable_consensus, unavailable_session]) is True
    assert _market_data_seen([unavailable_session, usable_consensus]) is True

    # And the unavailable session, by itself, still never satisfies the gate.
    assert _market_data_seen([unavailable_session]) is False
