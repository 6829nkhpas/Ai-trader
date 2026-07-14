"""Bug 2 regression — Trade Q&A ALWAYS ends on a natural-language answer.

Feature: deep-quant-decision-reliability (bugfix)

Bug 2 (APPLIED — regression coverage only): when the Q&A tool-fetch budget
(``MAX_QA_TURNS = 3``) was exhausted while a tool call was still pending, the
graph ended on a tool-call message with NO answer text; the UI rendered a lone
``> get_candles…`` line and froze.

Applied fix under test — ``graph.qa_node``: on the FINAL permitted turn
(``is_final_qa_turn = incoming_qa_turns >= MAX_QA_TURNS - 1``) it invokes the
BASE ``llm`` with NO tools bound plus an explicit synthesize-now
``SystemMessage``, guaranteeing the last turn produces natural-language text and
no further tool call. The node never returns a ``decision`` update, so the
committed Declared_Trade stays immutable.

    Property 3 (Bug Condition): a Q&A run whose model keeps issuing tool calls
    until ``MAX_QA_TURNS`` is exhausted STILL ends with a non-empty
    natural-language answer, no trailing pending tool call, and no ``decision``
    mutation. On the final turn the model is invoked WITHOUT tools bound.

    Property 4 (Preservation): a normal in-budget Q&A still streams tool calls +
    a final answer and permits up to ``MAX_QA_TURNS`` tool-fetch turns, leaving
    the committed decision immutable.

    Validates: Requirements 2.4, 2.5, 3.3, 3.4.

This test never touches the network: the module-level ``llm`` (base, no tools)
and ``llm_with_tools`` (tool-bound) are patched with in-memory fakes that RECORD
their invocations, and the real ``_base_tool_node`` is patched with a recording
fake — mirroring the doubles in ``tests/test_trade_qa.py`` and
``tests/test_heartbeat_cap_precedence_properties.py``. ``qa_node`` /
``qa_should_continue`` / ``qa_tool_node`` are driven directly (no full graph).
"""

import json
import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` resolves exactly as every sibling test expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    qa_node,
    qa_should_continue,
    qa_tool_node,
    QA_MODE,
    MAX_QA_TURNS,
)


# ── Test doubles ─────────────────────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a tool result. ``graph`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


class ToolCallingLLM:
    """Fake that ALWAYS returns a read-only tool call and records invocations.

    Stands in for ``llm_with_tools`` (the tool-bound binding). Each ``invoke``
    yields an ``AIMessage`` carrying a native ``get_candles`` tool call, so the
    model "keeps issuing tool calls" — the Bug 2 condition — unless the node
    routes it to the base (no-tools) ``llm`` on the final turn.
    """

    def __init__(self):
        self.invocations = 0

    def invoke(self, _messages):
        self.invocations += 1
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "get_candles",
                    "args": {"symbol": "RELIANCE", "timeframe": "15m"},
                    "id": f"call_gc_{self.invocations}",
                    "type": "tool_call",
                }
            ],
        )


class AnsweringLLM:
    """Fake base ``llm`` (NO tools bound) that returns a text answer.

    Records invocations so a test can assert the final turn was routed to the
    tool-free binding.
    """

    def __init__(self, content="Based on the data gathered, the 1D bias is neutral; standing aside."):
        self._content = content
        self.invocations = 0

    def invoke(self, _messages):
        self.invocations += 1
        return AIMessage(content=self._content, tool_calls=[])


class ScriptedLLM:
    """Fake that returns a queued sequence of (content, tool_calls) responses.

    Used for the in-budget preservation case: first a tool call, then a final
    answer — all within the tool-bound binding (never reaching the final-turn
    no-tools path).
    """

    def __init__(self, script):
        self._script = list(script)
        self.invocations = 0

    def invoke(self, _messages):
        content, tool_calls = self._script[min(self.invocations, len(self._script) - 1)]
        self.invocations += 1
        return AIMessage(content=content, tool_calls=[dict(tc) for tc in (tool_calls or [])])


class RecordingToolNode:
    """In-memory replacement for the real ToolNode that records dispatches."""

    def __init__(self):
        self.dispatched = []

    def invoke(self, payload):
        msgs = payload.get("messages") or []
        calls = (getattr(msgs[0], "tool_calls", None) or []) if msgs else []
        self.dispatched.extend(c.get("name") for c in calls)
        return {
            "messages": [
                StubToolMessage(content=json.dumps({"tool": c.get("name"), "ok": True}), name=c.get("name"))
                for c in calls
            ]
        }


def _make_qa_state(question="Walk me through the setup.", prior_results=None, decision=None):
    """A Q&A-mode AgentState: a user question, optional prior tool results, and
    an optional persisted decision (None → no declared trade)."""
    messages = [HumanMessage(content=question)]
    for name, payload in (prior_results or []):
        messages.insert(
            0, ToolMessage(content=json.dumps(payload), name=name, tool_call_id=f"tc_{name}")
        )
    return {
        "messages": messages,
        "mode": QA_MODE,
        "symbol": "RELIANCE",
        "decision": decision,
        "qa_turns": 0,
    }


def _run_qa_loop(state, base_llm, tool_llm, tool_node, max_iterations=12):
    """Drive the Q&A sub-loop (qa_node → qa_should_continue → qa_tool_node) to a
    terminal answer with all LLM/tool I/O replaced by in-memory fakes.

    Returns (final_state, routes) where ``routes`` is the qa_should_continue
    decision after each qa_node turn.
    """
    routes = []
    with mock.patch.object(graph, "llm", base_llm), \
            mock.patch.object(graph, "llm_with_tools", tool_llm), \
            mock.patch.object(graph, "_base_tool_node", tool_node):
        for _ in range(max_iterations):
            update = qa_node(state)
            # A Q&A turn NEVER commits/mutates a decision (R18.6 / preservation).
            assert "decision" not in update, "qa_node returned a decision update"
            state = {
                **state,
                "messages": [*state["messages"], *update["messages"]],
                "qa_turns": update["qa_turns"],
            }
            route = qa_should_continue(state)
            routes.append(route)
            if route == "end":
                return state, routes
            tool_update = qa_tool_node(state)
            assert "decision" not in tool_update, "qa_tool_node returned a decision update"
            state = {**state, "messages": [*state["messages"], *tool_update["messages"]]}
    raise AssertionError("Q&A loop did not terminate within the tool-fetch budget")


def _last_ai_message(state):
    return state["messages"][-1]


# ══════════════════════════════════════════════════════════════════════════════
# Property 3 (Bug Condition) — the Q&A run ALWAYS ends on a real answer.
# Feature: deep-quant-decision-reliability, Bug 2.
# Validates: Requirements 2.4, 2.5.
# ══════════════════════════════════════════════════════════════════════════════
def test_qa_budget_exhausted_still_ends_with_answer():
    """A model that keeps requesting tools until MAX_QA_TURNS is exhausted still
    ends with a non-empty natural-language answer and no trailing tool call — and
    the final turn is routed to the base (no-tools) ``llm``."""
    base_llm = AnsweringLLM()
    tool_llm = ToolCallingLLM()
    recorder = RecordingToolNode()

    state, routes = _run_qa_loop(_make_qa_state(), base_llm, tool_llm, recorder)

    final = _last_ai_message(state)
    # (1) Ends with a NON-EMPTY natural-language answer (no frozen empty bubble).
    assert (final.content or "").strip(), "Q&A ended with empty answer content (Bug 2 regression)"
    # (2) No trailing PENDING tool call on the terminal message.
    assert not (getattr(final, "tool_calls", None) or []), "terminal Q&A message still carries a pending tool call"
    # (3) The loop actually reached the end via qa_should_continue.
    assert routes[-1] == "end"
    # (4) The FINAL permitted turn was invoked WITHOUT tools bound: the base llm
    #     answered exactly once, and it was the last model call.
    assert base_llm.invocations == 1, "final turn was not routed to the tool-free base llm"
    # The tool-bound binding was used for the earlier turns (budget - 1 of them).
    assert tool_llm.invocations == MAX_QA_TURNS - 1
    # (5) No decision was ever committed — the persisted trade stays immutable.
    assert state.get("decision") is None


def test_final_turn_invokes_base_llm_without_tools():
    """Directly exercise the final-turn seam: with qa_turns already at the budget
    boundary, ``qa_node`` must invoke the base (no-tools) ``llm`` and return a
    non-empty answer with no pending tool call — never the tool-bound binding."""
    base_llm = AnsweringLLM(content="Here is the synthesized answer from what was gathered.")
    tool_llm = ToolCallingLLM()
    recorder = RecordingToolNode()

    state = _make_qa_state()
    state["qa_turns"] = MAX_QA_TURNS - 1  # the final permitted turn

    with mock.patch.object(graph, "llm", base_llm), \
            mock.patch.object(graph, "llm_with_tools", tool_llm), \
            mock.patch.object(graph, "_base_tool_node", recorder):
        update = qa_node(state)

    msg = update["messages"][0]
    assert base_llm.invocations == 1, "final turn did not use the tool-free base llm"
    assert tool_llm.invocations == 0, "final turn incorrectly used the tool-bound binding"
    assert (msg.content or "").strip(), "final turn produced empty answer content"
    assert not (getattr(msg, "tool_calls", None) or []), "final turn produced a pending tool call"
    assert "decision" not in update


@settings(max_examples=50, deadline=None)
@given(
    # The model tries to issue this many tool calls before it would otherwise
    # stop; even when this exceeds the budget, the run must still end on a real
    # answer (the fix caps the loop and forces a synthesis turn).
    greedy_turns=st.integers(min_value=MAX_QA_TURNS, max_value=MAX_QA_TURNS + 5),
    answer=st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=40),
)
def test_property_qa_always_answers_when_budget_exhausted(greedy_turns, answer):
    """Property: for any run where the model would keep calling tools past the
    budget, the Q&A ALWAYS terminates with a non-empty answer and no pending tool
    call, and the final turn is the tool-free synthesis turn."""
    base_llm = AnsweringLLM(content=answer)
    tool_llm = ToolCallingLLM()
    recorder = RecordingToolNode()

    state, routes = _run_qa_loop(_make_qa_state(), base_llm, tool_llm, recorder)

    final = _last_ai_message(state)
    assert (final.content or "").strip()
    assert not (getattr(final, "tool_calls", None) or [])
    assert routes[-1] == "end"
    # Exactly one tool-free synthesis turn closed the run.
    assert base_llm.invocations == 1
    # The budget bounded the tool-fetch turns: the tool-bound binding ran exactly
    # MAX_QA_TURNS - 1 times regardless of how greedy the model was.
    assert tool_llm.invocations == MAX_QA_TURNS - 1
    assert state.get("decision") is None


# ══════════════════════════════════════════════════════════════════════════════
# Property 4 (Preservation) — normal in-budget Q&A still streams tools + answer.
# Feature: deep-quant-decision-reliability, Bug 2.
# Validates: Requirements 3.3, 3.4.
# ══════════════════════════════════════════════════════════════════════════════
def test_in_budget_qa_streams_tool_then_final_answer():
    """A normal Q&A that fetches one read-only tool within budget still streams
    the tool call, executes it, and ends on a final answer — with the committed
    decision left immutable."""
    base_llm = AnsweringLLM()  # should NOT be needed within budget
    tool_llm = ScriptedLLM(
        script=[
            # Turn 1: request a read-only fetch.
            ("", [{"name": "get_candles", "args": {"symbol": "RELIANCE", "timeframe": "15m"}, "id": "c1", "type": "tool_call"}]),
            # Turn 2: answer from the fetched data (no more tool calls).
            ("The 15m candles confirm a neutral consolidation; here is the read.", []),
        ]
    )
    recorder = RecordingToolNode()

    committed = {"action": "HOLD", "source": "forced_hold", "conviction_score": 0}
    state = _make_qa_state(decision=committed)
    state, routes = _run_qa_loop(state, base_llm, tool_llm, recorder)

    final = _last_ai_message(state)
    # Final answer is non-empty with no pending tool call.
    assert (final.content or "").strip()
    assert not (getattr(final, "tool_calls", None) or [])
    # The read-only tool WAS streamed/dispatched (preservation of tool use).
    assert "get_candles" in recorder.dispatched
    # It resolved within budget — the tool-free final-turn synthesis was NOT needed.
    assert base_llm.invocations == 0
    assert routes == ["tools", "end"]
    # The committed decision is untouched by the Q&A turn (immutable).
    assert state["decision"] == committed


def test_qa_should_continue_permits_up_to_max_qa_turns():
    """Preservation of the tool-fetch budget: qa_should_continue routes pending
    calls to the tools node for every turn strictly under MAX_QA_TURNS, and ends
    once the budget is reached — so up to MAX_QA_TURNS tool-fetch turns are
    permitted."""
    pending = AIMessage(
        content="",
        tool_calls=[{"name": "get_consensus_report", "args": {}, "id": "c0", "type": "tool_call"}],
    )
    for turns in range(MAX_QA_TURNS):
        assert qa_should_continue({"messages": [pending], "qa_turns": turns}) == "tools", (
            f"expected a tool-fetch turn to be permitted at qa_turns={turns}"
        )
    # At the budget, even a pending call ends the run.
    assert qa_should_continue({"messages": [pending], "qa_turns": MAX_QA_TURNS}) == "end"
    # A turn that produced a plain answer ends the run regardless of budget.
    assert qa_should_continue({"messages": [AIMessage(content="done")], "qa_turns": 0}) == "end"
