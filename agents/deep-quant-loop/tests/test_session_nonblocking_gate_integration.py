"""Integration test for non-blocking session degradation (graph.py, task 10.3).

Feature: session-expiry-awareness

This is a plain pytest integration test for **Requirement 5.3**:

    When a session result is unavailable, the Deep_Quant_Agent SHALL treat it as
    a missing optional input, SHALL proceed with the remaining analysis, and
    SHALL NOT abort, fail, or block the decision solely because the session
    context is unavailable.

It exercises the two real wiring points in ``graph.py`` end-to-end using genuine
``langchain_core.messages.ToolMessage`` objects (no mocks, no LLM, no Rust
server):

  1. The market-data gate (``_market_data_seen`` / ``MARKET_DATA_TOOL_NAMES``):
     a ``get_session_context`` Unavailable_Marker does NOT, on its own, satisfy
     the gate — neither alone nor alongside other unavailable/error results —
     while a genuinely usable non-session market-data tool result still does
     (so session-unavailability never suppresses real data).

  2. The defensibility record (``build_defensibility_record`` / ``_session_entry``):
     building the record for a committed BUY/SELL decision when the session
     result is unavailable (or entirely absent) does NOT raise and does NOT
     block or alter the committed decision — the decision's action and execution
     levels remain exactly as committed, and ``record['session']['available']``
     is ``False``.

The sys.path / import pattern mirrors
``tests/test_session_market_data_gate_properties.py``.
"""

import copy
import json
import os
import sys

import pytest
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


# ── Helpers ──────────────────────────────────────────────────────────────────
def _tool_message(name, payload):
    """A real LangChain ToolMessage carrying a JSON-serialized tool result."""
    return ToolMessage(
        content=json.dumps(payload),
        name=name,
        tool_call_id=f"call_{name}",
    )


def _unavailable_session_payload():
    """An honest get_session_context Unavailable_Marker.

    Per AD-5 / R5.2 it omits ``session_phase`` and ``time_favorability``.
    """
    return {
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "unavailable": True,
        "reason": "invalid timestamp: expected a finite epoch-millisecond number, got None",
    }


def _error_session_payload():
    """An error result for the session tool."""
    return {
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "error": "Failed to retrieve candles from Rust server: timeout",
    }


def _usable_candles_payload():
    """A usable (non-session) market-data tool result — genuine directional data."""
    return {
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "candles": [
            {"timestamp_ms": 1700000000000, "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5},
            {"timestamp_ms": 1700000900000, "open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5},
        ],
    }


def _committed_buy_decision():
    """A committed BUY decision with structured execution levels."""
    return {
        "action": "BUY",
        "entry": 100.5,
        "stop_loss": 98.0,
        "take_profit": 106.0,
        "conviction": 7,
        "setup_validation": "Breakout over resistance with volume confirmation.",
        "execution_plan": "Enter 100.5, stop 98.0, target 106.0.",
    }


def _committed_sell_decision():
    """A committed SELL decision with structured execution levels."""
    return {
        "action": "SELL",
        "entry": 200.0,
        "stop_loss": 204.0,
        "take_profit": 190.0,
        "conviction": 6,
        "setup_validation": "Breakdown below support.",
        "execution_plan": "Short 200.0, stop 204.0, target 190.0.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: an unavailable session result does NOT satisfy the market-data gate.
# ─────────────────────────────────────────────────────────────────────────────
def test_session_tool_participates_in_the_market_data_gate():
    """Precondition: get_session_context is a recognized market-data tool."""
    assert SESSION_TOOL in MARKET_DATA_TOOL_NAMES


def test_unavailable_session_does_not_satisfy_gate_on_its_own():
    """R5.3 / R6.5: an Unavailable_Marker session result never sets the gate.

    Neither alone nor alongside other unavailable/error session results.
    """
    unavailable_msg = _tool_message(SESSION_TOOL, _unavailable_session_payload())
    error_msg = _tool_message(SESSION_TOOL, _error_session_payload())

    # Unavailable session result, on its own, does NOT satisfy the gate.
    assert _market_data_seen([unavailable_msg]) is False

    # Alongside other unavailable / error results, still does NOT satisfy it
    # (no usable directional data is present).
    assert _market_data_seen([unavailable_msg, error_msg]) is False
    assert _market_data_seen([error_msg, unavailable_msg]) is False


def test_unavailable_session_does_not_suppress_real_market_data():
    """R5.3: session-unavailability must not block genuine market data.

    A usable non-session market-data tool result still satisfies the gate even
    when a session Unavailable_Marker is also present, in any order.
    """
    unavailable_session = _tool_message(SESSION_TOOL, _unavailable_session_payload())
    usable_candles = _tool_message("get_candles", _usable_candles_payload())

    # The usable data alone satisfies the gate.
    assert _market_data_seen([usable_candles]) is True

    # An accompanying session Unavailable_Marker does not suppress it.
    assert _market_data_seen([unavailable_session, usable_candles]) is True
    assert _market_data_seen([usable_candles, unavailable_session]) is True


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: an unavailable / absent session result does NOT block or alter a
#         committed decision when building the defensibility record.
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "decision_factory",
    [_committed_buy_decision, _committed_sell_decision],
    ids=["BUY", "SELL"],
)
def test_unavailable_session_does_not_block_or_alter_committed_decision(decision_factory):
    """R5.3 / R13.4 / R13.5: an unavailable session is a non-blocking missing input.

    Building the defensibility record for a committed directional decision with
    an unavailable session result must not raise and must not alter the
    committed action or execution levels.
    """
    decision = decision_factory()
    committed_before = copy.deepcopy(decision)

    messages = [_tool_message(SESSION_TOOL, _unavailable_session_payload())]

    # Must not raise.
    record = build_defensibility_record(messages, decision, mode="FIND")

    # The committed decision's action and levels are unchanged (not blocked).
    assert record["action"] == committed_before["action"]
    assert record["levels"] == {
        "entry": committed_before["entry"],
        "stop_loss": committed_before["stop_loss"],
        "take_profit": committed_before["take_profit"],
    }

    # The session is surfaced as unavailable — never fabricated.
    assert record["session"]["available"] is False
    assert "session_phase" not in record["session"]
    assert "time_favorability" not in record["session"]

    # The session entry never injects an unfavorable-window block/override.
    assert "trade_in_unfavorable_window" not in record["session"]

    # The original decision dict was not mutated by record assembly.
    assert decision["action"] == committed_before["action"]
    assert decision["entry"] == committed_before["entry"]
    assert decision["stop_loss"] == committed_before["stop_loss"]
    assert decision["take_profit"] == committed_before["take_profit"]


@pytest.mark.parametrize(
    "decision_factory",
    [_committed_buy_decision, _committed_sell_decision],
    ids=["BUY", "SELL"],
)
def test_absent_session_does_not_block_or_alter_committed_decision(decision_factory):
    """R5.3 / R8.3: an entirely absent session result is recorded as unavailable.

    With no get_session_context result anywhere in history, the record still
    builds without raising, the committed decision is untouched, and the session
    entry is marked unavailable.
    """
    decision = decision_factory()
    committed_before = copy.deepcopy(decision)

    # No session message at all; an unrelated usable market-data result present.
    messages = [_tool_message("get_candles", _usable_candles_payload())]

    record = build_defensibility_record(messages, decision, mode="FIND")

    # Committed action / levels remain exactly as declared.
    assert record["action"] == committed_before["action"]
    assert record["levels"] == {
        "entry": committed_before["entry"],
        "stop_loss": committed_before["stop_loss"],
        "take_profit": committed_before["take_profit"],
    }

    # Absent session context → recorded as unavailable, nothing fabricated.
    assert record["session"]["available"] is False
    assert "session_phase" not in record["session"]
    assert "time_favorability" not in record["session"]
