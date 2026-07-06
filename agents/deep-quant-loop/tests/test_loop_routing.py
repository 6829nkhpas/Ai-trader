"""Property-based tests for the ReAct loop control and termination (graph.py, task 3).

Feature: deep-quant-analysis-hardening

These tests exercise the loop-routing and data-gating correctness properties
(design Properties 6-13, 17, 19, 38; Requirements 2.1-2.7, 3.1-3.3, 5.1, 5.3,
10.4) using Hypothesis with ``max_examples=100``.

The implementation under test lives in ``graph.py``:
  - ``should_continue`` — deterministic routing precedence
  - ``tool_node`` — first-turn data-acquisition gating + declare commit
  - ``force_hold`` — forced-HOLD injection
  - ``route_after_tools`` — post-tools routing
  - helpers ``_market_data_seen``, ``_market_data_attempted``,
    ``_decision_from_declare``, ``_tool_result_is_unavailable``,
    ``_tool_result_is_error``
  - constants ``MAX_REASONING_TURNS``, ``MARKET_DATA_TOOL_NAMES``

The real LLM is never invoked. Lightweight stub message objects stand in for
LangChain ``AIMessage``/``ToolMessage`` (the routing/gating code only reads
``.tool_calls``, ``.additional_kwargs``, ``.content``, ``.type``, ``.name``).
When a code path would dispatch to the real ``ToolNode`` (which performs network
calls), the node is patched with an in-memory fake so no network I/O occurs.
"""

import json
import os
import sys
from unittest import mock

from hypothesis import given, settings, strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    should_continue,
    tool_node,
    force_hold,
    route_after_tools,
    MAX_REASONING_TURNS,
    MARKET_DATA_TOOL_NAMES,
)


# ── Lightweight stub message objects ─────────────────────────────────────────
class StubAIMessage:
    """Stand-in for an assistant message carrying (possibly classified) calls.

    ``should_continue``/``tool_node`` read ``.tool_calls`` and
    ``.additional_kwargs`` (for ``_extraction_status`` / ``_synthetic_results``)
    and ``.content``. ``.type`` keeps it out of the tool-message scanners.
    """

    def __init__(self, content="", tool_calls=None, extraction_status=None, synthetic=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.type = "ai"
        self.additional_kwargs = {
            "_extraction_status": extraction_status or {},
            "_synthetic_results": synthetic or {},
        }


class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


class FakeToolNode:
    """In-memory replacement for the real ToolNode (no network I/O).

    Returns one synthetic ToolMessage per pending call so ``tool_node`` can build
    its output without dispatching to live tools.
    """

    def invoke(self, payload):
        msgs = payload.get("messages") or []
        calls = getattr(msgs[0], "tool_calls", None) or [] if msgs else []
        return {"messages": [StubToolMessage(content="ok", name=c.get("name")) for c in calls]}


# ── Shared strategies ────────────────────────────────────────────────────────
MARKET_DATA_NAMES = sorted(MARKET_DATA_TOOL_NAMES)

# Registered tool names that route to execution and are NOT the watch tool.
non_watch_names = st.sampled_from(MARKET_DATA_NAMES + ["declare_trade"])

action_strategy = st.sampled_from(["BUY", "SELL", "HOLD"])
conviction_strategy = st.integers(min_value=0, max_value=100)
short_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")), max_size=20
)

# Usable (non-error, non-unavailable) market-data tool result content.
usable_market_content = st.builds(
    lambda ts, px: json.dumps({"trend_score": ts, "current_price": px, "rsi_14": 50.0}),
    st.integers(min_value=-100, max_value=100),
    st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
)

# Error-marked tool result content (recognized by ``_tool_result_is_error``).
error_content = st.sampled_from(
    [
        '{"error": "Failed to retrieve candles from Rust server: timeout"}',
        "{'error': 'connection refused'}",
        "error: upstream unavailable",
        '{"symbol": "X", "error": "no data"}',
    ]
)

# Graceful-degradation "unavailable" sentiment markers (matched by
# ``_tool_result_is_unavailable``).
unavailable_content = st.sampled_from(
    [
        '{"sentiment_summary": "Unavailable"}',
        "{'sentiment_summary': 'Unavailable'}",
        '{"symbol": "X", "headlines": [], "sentiment_summary": "Unavailable", "error": "no feed"}',
        '{"status": "unavailable"}',
        '{"unavailable": true}',
    ]
)


def make_ai_with_calls(names, statuses=None):
    """Build a StubAIMessage whose ``tool_calls`` carry the given names.

    By default every call is classified ``ok`` in ``_extraction_status``.
    """
    tool_calls = [
        {"id": f"c{i}", "name": n, "args": {}} for i, n in enumerate(names)
    ]
    if statuses is None:
        ext = {tc["id"]: "ok" for tc in tool_calls}
    else:
        ext = {tc["id"]: s for tc, s in zip(tool_calls, statuses)}
    return StubAIMessage(content="", tool_calls=tool_calls, extraction_status=ext)


def make_declare_message(action, conviction, setup, plan, call_id="declare_1"):
    """Build a StubAIMessage carrying a single ``ok`` declare_trade call."""
    args = {
        "action": action,
        "conviction_score": conviction,
        "setup_validation": setup,
        "execution_plan": plan,
    }
    return StubAIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": "declare_trade", "args": args}],
        extraction_status={call_id: "ok"},
    )


# ── Property 6 (task 3.3) ────────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 6: Pending tool calls route to
# execution
@settings(max_examples=100, deadline=None)
@given(
    names=st.lists(non_watch_names, min_size=1, max_size=4),
    reasoning_turns=st.integers(min_value=0, max_value=MAX_REASONING_TURNS + 2),
    decision_set=st.booleans(),
)
def test_property_6_pending_tool_calls_route_to_execution(names, reasoning_turns, decision_set):
    """Validates: Requirements 2.1

    A loop state whose last message has one or more ``ok`` pending tool calls
    (none being the watch tool) routes to tool execution (``continue``),
    regardless of the reasoning counter or any decision field.
    """
    last = make_ai_with_calls(names)
    state = {
        "messages": [last],
        "decision": {"action": "BUY"} if decision_set else None,
        "reasoning_turns": reasoning_turns,
        "market_data_seen": True,
    }
    assert should_continue(state) == "continue"


# ── Property 7 (task 3.4) ────────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 7: A finalized decision
# terminates the run
@settings(max_examples=100, deadline=None)
@given(
    action=action_strategy,
    reasoning_turns=st.integers(min_value=0, max_value=MAX_REASONING_TURNS + 2),
    prose=short_text,
)
def test_property_7_finalized_decision_terminates(action, reasoning_turns, prose):
    """Validates: Requirements 2.2

    A loop state with ``decision`` set and no pending tool calls terminates the
    run (``end``), independent of the reasoning counter or any prose content.
    """
    last = StubAIMessage(content=prose, tool_calls=[])
    state = {
        "messages": [last],
        "decision": {"action": action, "source": "declare_trade"},
        "reasoning_turns": reasoning_turns,
        "market_data_seen": True,
    }
    assert should_continue(state) == "end"


# ── Property 8 (task 3.5) ────────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 8: Bounded reasoning is allowed
# before forcing termination
@settings(max_examples=100, deadline=None)
@given(
    reasoning_turns=st.integers(min_value=0, max_value=MAX_REASONING_TURNS - 1),
    prose=short_text,
)
def test_property_8_bounded_reasoning_loops(reasoning_turns, prose):
    """Validates: Requirements 2.3

    With no pending tool calls and no decision, while
    ``reasoning_turns < MAX_REASONING_TURNS`` routing returns to continued
    reasoning (``loop_agent``).
    """
    last = StubAIMessage(content=prose, tool_calls=[])
    state = {
        "messages": [last],
        "decision": None,
        "reasoning_turns": reasoning_turns,
        "market_data_seen": True,
    }
    assert should_continue(state) == "loop_agent"


# ── Property 9 (task 3.6) ────────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 9: Pending work takes
# precedence over the reasoning cap
@settings(max_examples=100, deadline=None)
@given(
    reasoning_turns=st.integers(min_value=MAX_REASONING_TURNS, max_value=MAX_REASONING_TURNS + 5),
    have_calls=st.booleans(),
    names=st.lists(non_watch_names, min_size=1, max_size=3),
    decision_set=st.booleans(),
)
def test_property_9_pending_work_precedence(reasoning_turns, have_calls, names, decision_set):
    """Validates: Requirements 2.4

    With the reasoning cap reached or exceeded, a state that still has pending
    ``ok`` tool calls or a set ``decision`` processes that work (tools or
    decision) and never takes the forced-HOLD path.
    """
    if have_calls:
        last = make_ai_with_calls(names)
        decision = {"action": "BUY"} if decision_set else None
    else:
        # No pending calls: a decision must be present to exercise precedence.
        last = StubAIMessage(content="", tool_calls=[])
        decision = {"action": "SELL", "source": "declare_trade"}

    state = {
        "messages": [last],
        "decision": decision,
        "reasoning_turns": reasoning_turns,
        "market_data_seen": True,
    }
    result = should_continue(state)
    assert result != "force_hold"
    if have_calls:
        # Pending tool calls are processed (no watch tool present → continue).
        assert result == "continue"
    else:
        # The finalized decision terminates the run.
        assert result == "end"


# ── Property 10 (task 3.7) ───────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 10: Exhausted reasoning yields
# a HOLD with no-decision-reached
@settings(max_examples=100, deadline=None)
@given(
    reasoning_turns=st.integers(min_value=MAX_REASONING_TURNS, max_value=MAX_REASONING_TURNS + 5),
    prose=short_text,
)
def test_property_10_exhausted_reasoning_forces_hold(reasoning_turns, prose):
    """Validates: Requirements 2.5

    With the reasoning cap reached, no pending tool calls, and no decision,
    routing takes the forced-HOLD path, and ``force_hold`` injects a HOLD
    decision whose stated reason is ``no-decision-reached``.
    """
    last = StubAIMessage(content=prose, tool_calls=[])
    state = {
        "messages": [last],
        "decision": None,
        "reasoning_turns": reasoning_turns,
        "market_data_seen": True,
    }
    assert should_continue(state) == "force_hold"

    update = force_hold(state)
    decision = update["decision"]
    assert decision["action"] == "HOLD"
    assert decision["reason"] == "no-decision-reached"


# ── Property 11 (task 3.8) ───────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 11: A registered price watch
# suspends rather than terminates
@settings(max_examples=100, deadline=None)
@given(
    extra=st.lists(st.sampled_from(MARKET_DATA_NAMES), max_size=3),
    watch_position=st.integers(min_value=0, max_value=3),
    reasoning_turns=st.integers(min_value=0, max_value=MAX_REASONING_TURNS + 2),
    decision_set=st.booleans(),
)
def test_property_11_watch_suspends(extra, watch_position, reasoning_turns, decision_set):
    """Validates: Requirements 2.6

    A loop state with an ``ok`` ``watch_price_condition`` call suspends the run
    (``suspend``) rather than terminating it (never ``end``/``force_hold``).
    """
    names = list(extra)
    pos = min(watch_position, len(names))
    names.insert(pos, "watch_price_condition")
    last = make_ai_with_calls(names)
    state = {
        "messages": [last],
        "decision": {"action": "BUY"} if decision_set else None,
        "reasoning_turns": reasoning_turns,
        "market_data_seen": True,
    }
    result = should_continue(state)
    assert result == "suspend"
    assert result not in ("end", "force_hold")


# ── Property 12 (task 3.9) ───────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 12: Termination ignores
# decision-like keywords in prose
@settings(max_examples=100, deadline=None)
@given(
    keyword=st.sampled_from(
        [
            "conviction_score",
            '{"conviction_score": 80, "execution_plan": "BUY now"}',
            "My final decision is BUY with conviction_score 90",
            "HOLD / SELL / setup_validation complete",
        ]
    ),
    filler=short_text,
    reasoning_turns=st.integers(min_value=0, max_value=MAX_REASONING_TURNS - 1),
)
def test_property_12_keywords_in_prose_do_not_terminate(keyword, filler, reasoning_turns):
    """Validates: Requirements 2.7

    Decision-like keywords in reasoning prose, with ``decision`` unset and the
    reasoning budget remaining, never terminate the run; routing continues
    reasoning (``loop_agent``) instead of ``end``.
    """
    content = f"{filler} {keyword} {filler}"
    last = StubAIMessage(content=content, tool_calls=[])
    state = {
        "messages": [last],
        "decision": None,
        "reasoning_turns": reasoning_turns,
        "market_data_seen": True,
    }
    result = should_continue(state)
    assert result == "loop_agent"
    assert result != "end"


# ── Property 13 (task 3.10) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 13: No decision before market
# data is seen
@settings(max_examples=100, deadline=None)
@given(
    action=action_strategy,
    conviction=conviction_strategy,
    setup=short_text,
    plan=short_text,
    prior_prose=short_text,
)
def test_property_13_no_decision_before_market_data(action, conviction, setup, plan, prior_prose):
    """Validates: Requirements 3.1, 3.2, 3.3

    While no market-data Analysis_Tool has returned data (``market_data_seen``
    false) and none has even been attempted, a declare_trade finalize attempt is
    blocked: ``tool_node`` commits no decision and the loop continues.
    """
    declare = make_declare_message(action, conviction, setup, plan)
    # Prior messages contain NO market-data tool results → nothing attempted.
    state = {
        "messages": [StubAIMessage(content=prior_prose), declare],
        "decision": None,
        "reasoning_turns": 0,
        "market_data_seen": False,
    }
    update = tool_node(state)
    # The trade is not committed; no decision is produced, so the loop continues.
    assert "decision" not in update or update.get("decision") is None


# ── Property 17 (task 3.11) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 17: Tool errors are recorded
# without aborting the run
@settings(max_examples=100, deadline=None)
@given(
    errors=st.lists(
        st.tuples(st.sampled_from(MARKET_DATA_NAMES), error_content),
        min_size=1,
        max_size=4,
    ),
)
def test_property_17_tool_errors_do_not_abort(errors):
    """Validates: Requirements 5.1

    An Analysis_Tool error result is recognized as an error (recorded) and, on
    its own, never forces termination: with no committed decision,
    ``route_after_tools`` returns the loop to the agent.
    """
    messages = [StubToolMessage(content=content, name=name) for name, content in errors]
    # Each error is recognized as such (recorded as a failure, not usable data).
    assert all(graph._tool_result_is_error(content) for _name, content in errors)
    # No market data was usably seen from pure error results.
    assert graph._market_data_seen(messages) is False

    state = {"messages": messages, "decision": None}
    # The error alone does not abort: control returns to the agent.
    assert route_after_tools(state) == "agent"


# ── Property 19 (task 3.12) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 19: Missing directional data
# forces a HOLD with a stated limitation
@settings(max_examples=100, deadline=None)
@given(
    action=action_strategy,
    conviction=conviction_strategy,
    setup=short_text,
    plan=short_text,
    attempted=st.lists(
        st.tuples(
            st.sampled_from(MARKET_DATA_NAMES),
            st.one_of(error_content, unavailable_content),
        ),
        min_size=1,
        max_size=4,
    ),
)
def test_property_19_missing_directional_data_forces_hold(action, conviction, setup, plan, attempted):
    """Validates: Requirements 5.3

    When market-data tools were attempted but returned no usable directional
    data (errors/unavailable), a declare_trade finalize attempt yields a HOLD
    decision carrying an explicit data-limitation reason.
    """
    prior = [StubToolMessage(content=content, name=name) for name, content in attempted]
    # Sanity: nothing usable was seen, but tools WERE attempted.
    assert graph._market_data_seen(prior) is False
    assert graph._market_data_attempted(prior) is True

    declare = make_declare_message(action, conviction, setup, plan)
    state = {
        "messages": prior + [declare],
        "decision": None,
        "reasoning_turns": 0,
        "market_data_seen": False,
    }
    update = tool_node(state)
    decision = update.get("decision")
    assert decision is not None
    assert decision["action"] == "HOLD"
    assert decision.get("reason") == "directional-data-unavailable"
    # The HOLD states the data limitation rather than fabricating a setup.
    assert decision.get("setup_validation")
    assert "unavailable" in decision["setup_validation"].lower()


# ── Property 38 (task 3.13) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 38: Unavailable sentiment does
# not block a decision
@settings(max_examples=100, deadline=None)
@given(
    action=action_strategy,
    conviction=conviction_strategy,
    setup=short_text,
    plan=short_text,
    usable=usable_market_content,
    unavailable=unavailable_content,
)
def test_property_38_unavailable_sentiment_does_not_block(action, conviction, setup, plan, usable, unavailable):
    """Validates: Requirements 10.4

    An ``Unavailable`` sentiment result is a missing — but non-blocking — input:
    it does not, by itself, satisfy ``market_data_seen``, yet when usable market
    data is also present a decision still commits despite the unavailable
    sentiment.
    """
    consensus = StubToolMessage(content=usable, name="get_consensus_report")
    news = StubToolMessage(content=unavailable, name="get_news_context")

    # The unavailable marker is recognized, and sentiment alone is not "seen".
    assert graph._tool_result_is_unavailable(unavailable) is True
    assert graph._market_data_seen([news]) is False
    # Usable market data alongside unavailable sentiment IS seen (not blocked).
    market_seen = graph._market_data_seen([consensus, news])
    assert market_seen is True

    declare = make_declare_message(action, conviction, setup, plan)
    state = {
        "messages": [consensus, news, declare],
        "decision": None,
        "reasoning_turns": 0,
        "market_data_seen": market_seen,
    }
    # Patch the real ToolNode so no network I/O occurs while committing.
    with mock.patch.object(graph, "_base_tool_node", FakeToolNode()):
        update = tool_node(state)

    decision = update.get("decision")
    assert decision is not None
    # The declared decision commits (it is not forced to HOLD by the gate).
    assert decision.get("source") == "declare_trade"
    assert decision["action"] == action
