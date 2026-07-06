"""Unit tests for the watch_price_condition registration-failure HOLD path.

Feature: deep-quant-analysis-hardening (task 10.5)

Requirement 14.3:
    IF watcher registration with the Tool_Server fails after the configured
    retry attempts, THEN THE Deep_Quant_Agent SHALL declare a HOLD decision and
    SHALL NOT output a trade.

These are example-based unit tests (no live LLM, no live Rust server, no
Hypothesis). They exercise two things:

  (a) ``watch_price_condition`` retries registration exactly the configured
      number of times and, on exhausting the budget, returns a STRUCTURED
      failure result that the ReAct loop recognizes as a non-fatal tool error.

  (b) Given that non-fatal failure result, the bounded ReAct loop ultimately
      yields a HOLD decision with NO trade committed — driven directly through
      the graph routing/force-hold helpers rather than a live model/server.
"""

import json
import os
import sys
from unittest import mock

# Make the service package importable (tools.py / graph.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
import graph  # noqa: E402
from graph import (  # noqa: E402
    should_continue,
    force_hold,
    route_after_tools,
    MAX_REASONING_TURNS,
)


# ── Lightweight stub messages (mirror test_loop_routing.py conventions) ──────
class StubAIMessage:
    """Stand-in for an assistant message (reasoning only, no tool calls)."""

    def __init__(self, content="", tool_calls=None, extraction_status=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.type = "ai"
        self.additional_kwargs = {"_extraction_status": extraction_status or {}}


class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _call_watch_tool(**kwargs):
    """Invoke the underlying watch_price_condition function past the @tool wrapper.

    The failure path returns BEFORE ``interrupt()`` is reached, so calling the
    raw function directly is safe (no LangGraph runtime is required).
    """
    raw = getattr(tools.watch_price_condition, "func", tools.watch_price_condition)
    return raw(**kwargs)


# ── (a) Retry budget + structured non-fatal error result ─────────────────────
def test_watch_registration_retries_configured_attempts_then_structured_error():
    """Validates: Requirements 14.3

    When registration always fails, the tool retries exactly
    ``WATCH_REGISTRATION_MAX_ATTEMPTS`` times and returns a structured failure
    result that the loop treats as a non-fatal tool error (not a watcher
    suspension, not a committed trade).
    """
    config = {"configurable": {"thread_id": "t-fail"}}

    # Avoid real sleeps between retries.
    with mock.patch.object(tools, "WATCH_REGISTRATION_RETRY_DELAY_S", 0), \
         mock.patch("time.sleep", return_value=None), \
         mock.patch.object(tools.httpx, "post", side_effect=ConnectionError("server down")) as mock_post:
        result = _call_watch_tool(
            symbol="RELIANCE",
            timeframe="15m",
            price_level=2450.0,
            direction="above",
            volume_multiplier=1.5,
            config=config,
        )

    # (1) Registration was attempted exactly the configured number of times.
    assert mock_post.call_count == tools.WATCH_REGISTRATION_MAX_ATTEMPTS

    # (2) The result is a STRUCTURED failure (dict) carrying an explicit error
    #     marker and a HOLD / no-trade intent — not a watcher suspension.
    assert isinstance(result, dict)
    assert "error" in result
    assert result.get("status") == "watch_registration_failed"
    assert result.get("action") == "HOLD"
    assert result.get("trade") is None

    # (3) The graph recognizes this as a non-fatal tool error so the run is not
    #     aborted and the bounded loop can fall through to a HOLD.
    serialized = json.dumps(result)
    assert graph._tool_result_is_error(serialized) is True
    # watch_price_condition is a control tool, never market data, so a failed
    # registration must not satisfy the first-turn data-acquisition gate.
    failed_msg = StubToolMessage(content=serialized, name="watch_price_condition")
    assert graph._market_data_seen([failed_msg]) is False


def test_watch_registration_success_does_not_take_failure_path():
    """A single successful registration attempt does not retry or return an error.

    The success path proceeds to ``interrupt()``; we patch ``interrupt`` so the
    raw function returns deterministically without a LangGraph runtime.
    """
    config = {"configurable": {"thread_id": "t-ok"}}

    ok_response = mock.Mock()
    ok_response.raise_for_status = mock.Mock(return_value=None)

    with mock.patch.object(tools.httpx, "post", return_value=ok_response) as mock_post, \
         mock.patch.object(tools, "interrupt", return_value={"close": 2451.0, "volume": 100000}):
        result = _call_watch_tool(
            symbol="RELIANCE",
            timeframe="15m",
            price_level=2450.0,
            direction="above",
            volume_multiplier=1.5,
            config=config,
        )

    # Registered on the first attempt; no structured error returned.
    assert mock_post.call_count == 1
    assert not (isinstance(result, dict) and "error" in result)


# ── (b) Failure → bounded loop yields HOLD with no trade ─────────────────────
def test_registration_failure_run_ends_in_hold_with_no_trade():
    """Validates: Requirements 14.3

    After a failed watch registration (a non-fatal tool error), the run produces
    no committed declare_trade decision. The bounded reasoning loop then forces a
    HOLD: ``should_continue`` routes to ``force_hold`` once the reasoning budget
    is exhausted, and ``force_hold`` injects a HOLD decision with no trade.
    """
    failure_result = _call_watch_tool_failure()
    failed_msg = StubToolMessage(
        content=json.dumps(failure_result), name="watch_price_condition"
    )

    # The failed registration alone does not commit a decision → loop continues.
    state_after_tools = {"messages": [failed_msg], "decision": None}
    assert route_after_tools(state_after_tools) == "agent"

    # The agent reasons but the watcher could not be staged; once the reasoning
    # budget is exhausted with no decision and no pending calls, the loop forces
    # a HOLD (R2.5 / R14.3) rather than emitting a trade.
    exhausted_state = {
        "messages": [failed_msg, StubAIMessage(content="Cannot stage the watch; standing aside.")],
        "decision": None,
        "reasoning_turns": MAX_REASONING_TURNS,
        "market_data_seen": False,
    }
    assert should_continue(exhausted_state) == "force_hold"

    update = force_hold(exhausted_state)
    decision = update["decision"]

    # A HOLD decision is declared, and NO trade is output.
    assert decision["action"] == "HOLD"
    assert decision["conviction_score"] == 0
    # No BUY/SELL execution levels are present anywhere in the decision.
    assert "BUY" not in decision["execution_plan"].upper()
    assert "SELL" not in decision["execution_plan"].upper()


def _call_watch_tool_failure():
    """Helper: produce a real registration-failure result via always-failing post."""
    config = {"configurable": {"thread_id": "t-hold"}}
    with mock.patch.object(tools, "WATCH_REGISTRATION_RETRY_DELAY_S", 0), \
         mock.patch.object(tools.httpx, "post", side_effect=ConnectionError("server down")), \
         mock.patch("time.sleep", return_value=None):
        return _call_watch_tool(
            symbol="RELIANCE",
            timeframe="15m",
            price_level=2450.0,
            direction="above",
            volume_multiplier=1.5,
            config=config,
        )
