# Feature: earnings-event-risk-gate, Integration: non-blocking degradation of an unavailable event result
"""Integration test for non-blocking event-risk degradation (graph.py, task 10.3).

Feature: earnings-event-risk-gate

This module is an integration-flavoured test that an UNAVAILABLE
``get_event_risk`` result is a benign, non-blocking missing input across the
two live-path consumers that decide whether a trade can proceed:

  1. the market-data gate (``_market_data_seen``), and
  2. the defensibility record builder (``build_defensibility_record``).

Validates: Requirements 5.2

    When an event-risk result is unavailable, the Deep_Quant_Agent treats it as
    a missing optional input, proceeds with the remaining analysis, and does NOT
    abort, fail, or block the decision solely because the event risk is
    unavailable.

Concretely the test asserts:

  1. A ``get_event_risk`` Unavailable_Marker ToolMessage does NOT, on its own,
     satisfy the market-data gate — ``_market_data_seen([unavailable])`` is
     ``False`` — so an unavailable event result never sets ``market_data_seen``
     (R6.5). The tool IS a member of ``MARKET_DATA_TOOL_NAMES``, so this proves
     the gate distinguishes a usable assessment from an unavailable marker.
  2. An unavailable event result does not abort/block a committed decision:
     ``build_defensibility_record`` returns a record (no exception raised), the
     committed decision's action and execution levels are unchanged (the event
     context never modifies the decision), and ``record['event']['available']``
     is ``False`` with NO fabricated ``event_risk`` / ``event_recommendation``.
  3. (Integration flavour) When OTHER market-data tools returned usable data, an
     unavailable event result does not flip ``market_data_seen`` back to
     ``False`` — the gate stays satisfied (monotone) across a mixed message
     sequence, so the missing event input never subtracts from the decision.

The real LLM / Rust server is never invoked: the test feeds in-memory
``ToolMessage`` objects exactly as the gate and the record builder read them.

The sys.path / import pattern mirrors
``tests/test_session_nonblocking_gate_integration.py``: the service directory
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
    _market_data_attempted,
    _market_data_seen,
    build_defensibility_record,
)

EVENT_TOOL = "get_event_risk"


def _tool_message(name, payload):
    """Build a LangChain ToolMessage exactly as the gate / record code reads it."""
    return ToolMessage(
        content=json.dumps(payload),
        name=name,
        tool_call_id=f"call_{name}",
    )


def _unavailable_event_payload():
    """An Unavailable_Marker for the event tool.

    Per AD-3 / R5.1 the marker omits ``event_risk`` and ``event_recommendation``
    rather than fabricating them.
    """
    return {
        "symbol": "RELIANCE",
        "holding_horizon": "multi_session",
        "unavailable": True,
        "reason": "no event source configured",
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
# 1. An unavailable event result does NOT satisfy the market-data gate.
# ─────────────────────────────────────────────────────────────────────────────
def test_unavailable_event_does_not_set_market_data_seen():
    """Validates: Requirements 5.2 (gate facet)

    An Unavailable_Marker ``get_event_risk`` result does NOT, on its own, set
    ``market_data_seen`` — it is a missing input, not usable market data — even
    though ``get_event_risk`` participates in the market-data gate.
    """
    assert EVENT_TOOL in MARKET_DATA_TOOL_NAMES

    unavailable_event_msg = _tool_message(EVENT_TOOL, _unavailable_event_payload())

    # The unavailable event result alone never satisfies the gate.
    assert _market_data_seen([unavailable_event_msg]) is False

    # The classifying predicate confirms this is an explicit unavailable marker.
    assert graph._tool_result_is_unavailable(unavailable_event_msg.content) is True

    # It IS, however, a market-data *attempt*: the tool was called but yielded no
    # usable data, so it is treated as a sought-but-unavailable optional input
    # (non-blocking), distinct from "never attempted".
    assert _market_data_attempted([unavailable_event_msg]) is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. An unavailable event result does NOT abort or block a committed decision.
# ─────────────────────────────────────────────────────────────────────────────
def test_unavailable_event_does_not_block_committed_decision():
    """Validates: Requirements 5.2 (decision facet)

    With only an unavailable event result in history, building the defensibility
    record for a committed BUY decision:
      * does not raise (the decision is not aborted/failed), and
      * records the event entry as unavailable (no fabricated risk), while
        leaving the committed action and execution levels unchanged.
    """
    decision = _committed_buy_decision()
    decision_snapshot = dict(decision)

    messages = [_tool_message(EVENT_TOOL, _unavailable_event_payload())]

    # Must not raise — an unavailable event context never aborts the build.
    record = build_defensibility_record(messages, decision, mode="FIND")

    assert isinstance(record, dict)

    # The event entry is recorded as unavailable, with no fabricated values.
    assert record["event"]["available"] is False
    assert "event_risk" not in record["event"]
    assert "event_recommendation" not in record["event"]
    assert "days_until_event" not in record["event"]
    assert "event_date" not in record["event"]

    # The committed decision's action and execution levels are unchanged: the
    # event context is a filter/defensibility surface, never a gate (R12.4/5).
    assert record["action"] == "BUY"
    assert record["levels"] == {"entry": 100.0, "stop_loss": 97.0, "take_profit": 106.0}

    # No "held through a scheduled event" block statement is produced for an
    # unavailable event — absence is benign, never a fabricated block.
    assert "trade_held_through_event" not in record["event"]

    # The decision dict itself is not mutated by record assembly.
    assert decision == decision_snapshot


# ─────────────────────────────────────────────────────────────────────────────
# 3. (Integration) An unavailable event does not flip the gate back to False
#    once OTHER market-data tools have returned usable data (monotonicity).
# ─────────────────────────────────────────────────────────────────────────────
def test_unavailable_event_does_not_unset_gate_after_usable_market_data():
    """Validates: Requirements 5.2 (non-blocking, monotone facet)

    When another market-data tool (here ``get_consensus_report``) has returned
    usable data, an unavailable event result anywhere in the sequence does NOT
    flip ``market_data_seen`` back to False — degradation is non-blocking.
    """
    usable_consensus = _tool_message(
        "get_consensus_report",
        {"symbol": "RELIANCE", "timeframe": "15m", "atr_14": 2.5, "consensus": "bullish"},
    )
    unavailable_event = _tool_message(EVENT_TOOL, _unavailable_event_payload())

    # Usable market data alone satisfies the gate.
    assert _market_data_seen([usable_consensus]) is True

    # A mixed sequence — usable data plus an unavailable event result, in either
    # order — still satisfies the gate (the unavailable event does not subtract
    # from it).
    assert _market_data_seen([usable_consensus, unavailable_event]) is True
    assert _market_data_seen([unavailable_event, usable_consensus]) is True

    # And the unavailable event, by itself, still never satisfies the gate.
    assert _market_data_seen([unavailable_event]) is False
