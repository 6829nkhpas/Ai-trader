"""Tests for Trade Q&A mode (graph.py, task 16).

Feature: deep-quant-analysis-hardening

Covers tasks:
  * 16.3 → Property 65: Q&A preserves the session analysis context (R18.5)
  * 16.4 → Property 66: Q&A never mutates the committed trade (R18.6)
  * 16.5 → Property 67: Q&A answers follow the run-transparency stream
            conventions (R18.7)
  * 16.6 → unit tests for Q&A grounding behaviors (R18.1, 18.2, 18.3, 18.4)

The property tests use Hypothesis with ``max_examples=100``. The real LLM is
never invoked: the module-level ``llm_with_tools`` is patched with an in-memory
fake that returns a deterministic response, and the real ``_base_tool_node``
(which performs network I/O) is patched with a recording fake — mirroring the
``FakeToolNode``/mock approach in ``tests/test_loop_routing.py``.

Implementation under test (``graph.py``, task 16.1):
  ``build_qa_context``, ``build_qa_system_prompt``, ``qa_node``,
  ``qa_tool_node``, ``qa_should_continue``, ``QA_FORBIDDEN_TOOLS``,
  ``route_entry``.

The Q&A stream-convention property (16.5) additionally exercises the pure
event-expansion helpers in ``stream_events.py`` (``node_update_events`` /
``message_events``) — the same path ``main.py`` uses for run transparency.
"""

import json
import os
import sys
from contextlib import contextmanager
from unittest import mock

from hypothesis import given, settings, strategies as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    build_qa_context,
    build_qa_system_prompt,
    qa_node,
    qa_tool_node,
    qa_should_continue,
    route_entry,
    QA_FORBIDDEN_TOOLS,
    QA_MODE,
    MAX_QA_TURNS,
    _normalize_action,
)
import stream_events  # noqa: E402
from stream_events import (  # noqa: E402
    node_update_events,
    DECISION,
    VERIFICATION_STEP,
    REASONING,
    TOOL_CALL_START,
    TOOL_CALL_RESULT,
    TOOL_CALL_END,
)


# ── Test doubles ─────────────────────────────────────────────────────────────
READONLY_QA_TOOLS = [
    "get_consensus_report",
    "get_candles",
    "get_multi_tf_trend",
    "get_chart_patterns",
    "get_support_resistance",
    "get_news_context",
]


class StubToolMessage:
    """Stand-in for a tool result. ``stream_events`` matches ``ToolMessage`` in
    the class name and ``graph`` matches ``type == 'tool'``."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


class FakeLLM:
    """In-memory replacement for ``llm_with_tools`` (no network I/O).

    ``invoke`` ignores the prompt and returns a real ``AIMessage`` carrying the
    preconfigured content and native tool calls, so ``extract_tool_calls`` takes
    its native path and the response flows through the stream helpers exactly
    like a genuine model response would.
    """

    def __init__(self, content="", tool_calls=None):
        self._content = content
        self._tool_calls = tool_calls or []

    def invoke(self, _messages):
        return AIMessage(content=self._content, tool_calls=list(self._tool_calls))


class RecordingToolNode:
    """In-memory replacement for the real ToolNode that records dispatches.

    Returns one synthetic ToolMessage per pending call and remembers every tool
    name it was asked to execute, so a test can assert that forbidden tools were
    never dispatched.
    """

    def __init__(self):
        self.dispatched = []

    def invoke(self, payload):
        msgs = payload.get("messages") or []
        calls = (getattr(msgs[0], "tool_calls", None) or []) if msgs else []
        self.dispatched.extend(c.get("name") for c in calls)
        return {"messages": [StubToolMessage(content="ok", name=c.get("name")) for c in calls]}


@contextmanager
def patched_llm(content="", tool_calls=None):
    """Patch the module-level ``llm_with_tools`` with a :class:`FakeLLM`."""
    with mock.patch.object(graph, "llm_with_tools", FakeLLM(content, tool_calls)):
        yield


def make_native_tool_calls(names):
    """Build LangChain-native tool-call dicts for the given tool names."""
    return [
        {"name": n, "args": {}, "id": f"c{i}", "type": "tool_call"}
        for i, n in enumerate(names)
    ]


# ── Strategies ───────────────────────────────────────────────────────────────
_price = st.floats(min_value=1.0, max_value=100000.0, allow_nan=False, allow_infinity=False)
short_text = st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), max_size=24)

levels_strategy = st.fixed_dictionaries(
    {"entry": _price, "stop_loss": _price, "take_profit": _price}
)

pattern_strategy = st.fixed_dictionaries(
    {
        "pattern_type": st.sampled_from(["Inverse H&S", "Double Bottom", "Bull Flag"]),
        "sentiment": st.sampled_from(["Bullish", "Bearish", "Neutral"]),
        "confidence": st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        "description": short_text,
        "timeframe": st.sampled_from(["5m", "15m", "1h"]),
    }
)


@st.composite
def defensibility_records(draw):
    """A defensibility record like the one ``build_defensibility_record`` emits."""
    atr = draw(st.floats(min_value=0.1, max_value=500.0, allow_nan=False, allow_infinity=False))
    rr = draw(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False))
    return {
        "mode": "FIND",
        "multi_tf_bias": {"trend_1h": "Bullish", "trend_4h": "Bullish", "trend_1d": "Neutral"},
        "trend_1d": draw(st.sampled_from(["Bullish", "Bearish", "Neutral", None])),
        "support_resistance": {"pivot": draw(_price), "s1": draw(_price), "r1": draw(_price)},
        "volatility_basis": f"Stop sized against ATR(14)={atr:.4f}; floor 1.5x ATR.",
        "atr_14": atr,
        "levels": draw(levels_strategy),
        "risk_reward": round(rr, 4),
        "patterns": draw(st.lists(pattern_strategy, max_size=3)),
        "predictive_conflict": short_text,
        "macro_trend_conflict": short_text,
        "news_sentiment": draw(st.sampled_from(["Bullish", "Bearish", "Unavailable", None])),
        "summary": short_text,
    }


@st.composite
def committed_decisions(draw):
    """A persisted ``decision`` carrying a defensibility record."""
    source = draw(st.sampled_from(["declare_trade", "forced_hold", "data_gating"]))
    action = draw(st.sampled_from(["BUY", "SELL", "HOLD"]))
    return {
        "action": action,
        "conviction_score": draw(st.integers(min_value=0, max_value=100)),
        "setup_validation": draw(short_text),
        "execution_plan": draw(short_text),
        "source": source,
        "defensibility": draw(defensibility_records()),
    }


def make_qa_state(decision, question="Why did you choose this entry?", prior_results=None):
    """Build a Q&A-mode AgentState with a persisted decision + conversation."""
    messages = [HumanMessage(content=question)]
    for name, payload in (prior_results or []):
        messages.insert(0, ToolMessage(content=json.dumps(payload), name=name, tool_call_id=f"tc_{name}"))
    return {
        "messages": messages,
        "mode": QA_MODE,
        "symbol": "RELIANCE",
        "decision": decision,
        "qa_turns": 0,
    }


# ── Property 65 (task 16.3) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 65: Q&A preserves the session
# analysis context
@settings(max_examples=100, deadline=None)
@given(decision=committed_decisions(), answer=short_text)
def test_property_65_qa_preserves_session_context(decision, answer):
    """Validates: Requirements 18.5

    A Q&A turn only appends messages and never clears or overwrites the persisted
    decision/defensibility context: ``qa_node`` and ``qa_tool_node`` return no
    ``decision`` update, the persisted ``decision`` object is left untouched, and
    ``build_qa_context`` faithfully reflects the persisted state.
    """
    state = make_qa_state(decision)
    record = decision["defensibility"]

    # build_qa_context reflects the persisted decision + defensibility record.
    ctx = build_qa_context(state)
    expected_action = _normalize_action(decision["action"])
    expected_declared = decision["source"] == "declare_trade" and expected_action in ("BUY", "SELL")
    assert ctx["has_declared_trade"] is expected_declared
    assert ctx["action"] == expected_action
    assert ctx["conviction_score"] == decision["conviction_score"]
    assert ctx["levels"] == record["levels"]
    assert ctx["risk_reward"] == record["risk_reward"]
    assert ctx["atr_14"] == record["atr_14"]
    assert ctx["volatility_basis"] == record["volatility_basis"]
    assert ctx["support_resistance"] == record["support_resistance"]
    assert ctx["patterns"] == record["patterns"]

    # Snapshot the persisted context so we can prove the turn did not mutate it.
    before = json.dumps(ctx, sort_keys=True, default=str)
    persisted_decision_id = id(state["decision"])

    # A plain (no-tool) Q&A answer turn appends only messages + the qa_turns
    # bookkeeping counter — never a decision.
    with patched_llm(content=answer or "Here is the rationale."):
        update = qa_node(state)

    assert "decision" not in update
    assert set(update.keys()) <= {"messages", "qa_turns"}
    # The persisted decision object is the same untouched object.
    assert id(state["decision"]) == persisted_decision_id
    assert json.dumps(build_qa_context(state), sort_keys=True, default=str) == before

    # qa_tool_node likewise never returns a decision (context preserved).
    ai = update["messages"][0]
    tool_state = {**state, "messages": state["messages"] + [ai]}
    with mock.patch.object(graph, "_base_tool_node", RecordingToolNode()):
        tool_update = qa_tool_node(tool_state)
    assert "decision" not in tool_update


# ── Property 66 (task 16.4) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 66: Q&A never mutates the
# committed trade
@settings(max_examples=100, deadline=None)
@given(
    decision=committed_decisions(),
    readonly=st.lists(st.sampled_from(READONLY_QA_TOOLS), max_size=3),
    forbidden=st.lists(st.sampled_from(sorted(QA_FORBIDDEN_TOOLS)), min_size=1, max_size=2),
)
def test_property_66_qa_never_mutates_committed_trade(decision, readonly, forbidden):
    """Validates: Requirements 18.6

    For an arbitrary Q&A turn — including one where the model emits
    ``declare_trade`` / ``watch_price_condition`` — the node update contains NO
    ``decision`` key, the forbidden tools are reclassified (``qa_forbidden``)
    rather than executed, and ``qa_tool_node`` never dispatches them.
    """
    # Interleave forbidden + read-only calls so order is arbitrary.
    names = []
    for i in range(max(len(readonly), len(forbidden))):
        if i < len(forbidden):
            names.append(forbidden[i])
        if i < len(readonly):
            names.append(readonly[i])

    state = make_qa_state(decision, question="Change the stop to breakeven and re-declare.")

    with patched_llm(content="", tool_calls=make_native_tool_calls(names)):
        update = qa_node(state)

    # The committed trade is never altered by the Q&A turn.
    assert "decision" not in update

    ai = update["messages"][0]
    statuses = ai.additional_kwargs["_extraction_status"]
    synthetic = ai.additional_kwargs["_synthetic_results"]

    # Every forbidden tool call is reclassified and answered with a refusal.
    forbidden_ids = [c["id"] for c in ai.tool_calls if c["name"] in QA_FORBIDDEN_TOOLS]
    assert forbidden_ids, "expected at least one forbidden call to be present"
    for cid in forbidden_ids:
        assert statuses[cid] == "qa_forbidden"
        assert cid in synthetic
    # Read-only calls remain executable.
    for c in ai.tool_calls:
        if c["name"] in READONLY_QA_TOOLS:
            assert statuses[c["id"]] == "ok"

    # qa_tool_node must execute only the read-only calls; forbidden tools are
    # refused and NEVER dispatched, and no decision is produced.
    tool_state = {**state, "messages": state["messages"] + [ai]}
    recorder = RecordingToolNode()
    with mock.patch.object(graph, "_base_tool_node", recorder):
        tool_update = qa_tool_node(tool_state)

    assert "decision" not in tool_update
    for name in QA_FORBIDDEN_TOOLS:
        assert name not in recorder.dispatched
    # Read-only requests were the only ones dispatched.
    assert set(recorder.dispatched) <= set(READONLY_QA_TOOLS)


# ── Property 67 (task 16.5) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 67: Q&A answers follow the
# run-transparency stream conventions
@settings(max_examples=100, deadline=None)
@given(
    decision=committed_decisions(),
    answer=short_text,
    readonly=st.lists(st.sampled_from(READONLY_QA_TOOLS), max_size=3),
)
def test_property_67_qa_follows_stream_conventions(decision, answer, readonly):
    """Validates: Requirements 18.7

    A Q&A turn's messages flow through the same ``stream_events`` expansion used
    for run transparency (``node_update_events`` / ``message_events``), producing
    only REASONING / TOOL_CALL_* events and NEVER a DECISION (or VERIFICATION_STEP),
    because a Q&A turn commits no decision.
    """
    state = make_qa_state(decision, question="What is the macro bias?")

    with patched_llm(content=answer or "The 1D bias is neutral.", tool_calls=make_native_tool_calls(readonly)):
        qa_update = qa_node(state)

    ai = qa_update["messages"][0]
    tool_state = {**state, "messages": state["messages"] + [ai]}
    with mock.patch.object(graph, "_base_tool_node", RecordingToolNode()):
        tool_update = qa_tool_node(tool_state)

    allowed_events = {REASONING, TOOL_CALL_START, TOOL_CALL_RESULT, TOOL_CALL_END}

    # Expand both Q&A node updates exactly as main.py's stream would.
    emitted = []
    for node_data in (qa_update, tool_update):
        emitted.extend(name for name, _payload in node_update_events(node_data))

    # No decision/verification events leak from a Q&A turn (R18.7 + R18.6).
    assert DECISION not in emitted
    assert VERIFICATION_STEP not in emitted
    # Every emitted event uses a run-transparency event convention.
    assert set(emitted) <= allowed_events
    # A read-only fetch produces a START → RESULT → END triple per the run
    # conventions; with no fetches the answer still yields a REASONING event.
    if readonly:
        assert TOOL_CALL_START in emitted
        assert TOOL_CALL_RESULT in emitted
        assert TOOL_CALL_END in emitted
    else:
        assert REASONING in emitted


# ── Task 16.6: unit tests for Q&A grounding behaviors (R18.1-R18.4) ───────────

def _declared_decision():
    return {
        "action": "BUY",
        "conviction_score": 74,
        "setup_validation": "Entry at S1 with SL below S2.",
        "execution_plan": "BUY entry 2440, SL 2418, TP 2492",
        "source": "declare_trade",
        "defensibility": {
            "mode": "FIND",
            "multi_tf_bias": {"trend_1h": "Bullish", "trend_4h": "Bullish", "trend_1d": "Neutral"},
            "trend_1d": "Neutral",
            "support_resistance": {"pivot": 2445.0, "s1": 2440.0, "s2": 2418.0},
            "volatility_basis": "Stop sized against ATR(14)=18.5000; floor 1.5x ATR = 27.7500.",
            "atr_14": 18.5,
            "levels": {"entry": 2440.0, "stop_loss": 2418.0, "take_profit": 2492.0},
            "risk_reward": 2.36,
            "patterns": [{"pattern_type": "Inverse H&S", "confidence": 0.71}],
            "predictive_conflict": "No predictive conflict.",
            "macro_trend_conflict": "1D trend bias is Neutral.",
            "news_sentiment": "Bullish",
            "summary": "RR 2.36; bullish confluence.",
        },
    }


def _hold_decision():
    return {
        "action": "HOLD",
        "conviction_score": 0,
        "reason": "no-decision-reached",
        "setup_validation": "Holding to preserve capital.",
        "execution_plan": "HOLD — no trade taken.",
        "source": "forced_hold",
        "defensibility": {
            "mode": "FIND",
            "multi_tf_bias": {"trend_1d": "Bearish"},
            "trend_1d": "Bearish",
            "support_resistance": None,
            "volatility_basis": "ATR(14) unavailable.",
            "atr_14": None,
            "levels": None,
            "risk_reward": None,
            "patterns": [],
            "summary": "No A+ setup found.",
        },
    }


def test_qa_context_declared_trade_loads_recorded_levels():
    """R18.1/R18.2: a declared trade exposes recorded entry/SL/TP, RR and ATR."""
    ctx = build_qa_context(make_qa_state(_declared_decision()))
    assert ctx["has_declared_trade"] is True
    assert ctx["action"] == "BUY"
    assert ctx["levels"] == {"entry": 2440.0, "stop_loss": 2418.0, "take_profit": 2492.0}
    assert ctx["risk_reward"] == 2.36
    assert ctx["atr_14"] == 18.5
    assert "ATR(14)=18.5000" in ctx["volatility_basis"]


def test_qa_system_prompt_declared_trade_cites_levels_and_attaches_context():
    """R18.1/R18.2: the prompt attaches the context and instructs level citation."""
    ctx = build_qa_context(make_qa_state(_declared_decision()))
    prompt = build_qa_system_prompt(ctx)
    # The recorded context is attached into the system prompt (R18.1).
    assert "RECORDED SESSION ANALYSIS CONTEXT" in prompt
    assert "2440.0" in prompt and "2418.0" in prompt and "2492.0" in prompt
    # Declared-trade guardrail branch cites entry/SL/TP, RR, and ATR (R18.2).
    assert "A Declared_Trade EXISTS" in prompt
    assert "Risk_Reward_Ratio" in prompt
    assert "volatility basis" in prompt
    # Grounding + no-fabrication rules are present (R18.1/R18.4).
    #
    # This used to assert the literal "Answer ONLY from the recorded context", which was
    # the prompt's actual wording and also its defect: told to answer only from the
    # transcript, the model replied "get_options_analytics was not present in the recorded
    # results" to questions a live tool call would have answered in one hop. The rule the
    # prompt is supposed to encode is that recorded values are GROUND TRUTH FOR WHAT THIS
    # SESSION DECIDED — not that they are the only thing the model may say. So the
    # assertion now pins the surviving requirement (do not invent data) and the companion
    # test below pins the replacement (fetch it instead of declaring it missing).
    assert "recorded context" in prompt
    assert "NEVER fabricate" in prompt


def test_qa_context_no_declared_trade_when_hold():
    """R18.3: a HOLD decision is not treated as a declared trade."""
    ctx = build_qa_context(make_qa_state(_hold_decision()))
    assert ctx["has_declared_trade"] is False
    assert ctx["action"] == "HOLD"


def test_qa_context_no_declared_trade_when_absent():
    """R18.3: an absent decision means no trade has been declared."""
    state = {"messages": [HumanMessage(content="Any trade?")], "mode": QA_MODE, "decision": None}
    ctx = build_qa_context(state)
    assert ctx["has_declared_trade"] is False
    assert ctx["action"] is None


def test_qa_system_prompt_not_declared_states_no_trade():
    """R18.3: the not-declared guardrail branch states no trade is declared."""
    ctx = build_qa_context(make_qa_state(_hold_decision()))
    prompt = build_qa_system_prompt(ctx)
    assert "NO Declared_Trade exists" in prompt
    assert "no trade has been declared" in prompt


def test_qa_system_prompt_missing_data_fetch_or_unavailable():
    """R18.4: the prompt directs a live tool fetch for a gap rather than a
    'not recorded' non-answer, and still forbids fabrication and trade mutation."""
    ctx = build_qa_context(make_qa_state(_declared_decision()))
    prompt = build_qa_system_prompt(ctx)
    # Must instruct the model to FETCH what it lacks. The old wording ("you may call ONE
    # relevant read-only market-data tool") is what produced the reported bug: it read as
    # permission for a single grudging lookup, so the model preferred to report the datum
    # missing. The budget is enforced by MAX_QA_TURNS in code, which is where a limit
    # belongs — the prompt's job is to say that fetching is the expected behaviour.
    assert "CALL THEM" in prompt
    assert "not recorded in this session" in prompt  # named as the thing NOT to answer
    assert "unavailable" in prompt.lower()
    assert "NEVER fabricate" in prompt
    # Delegation is offered, and is advisory only.
    assert "run_debate" in prompt and "rerun_analysis" in prompt
    assert "ADVISORY" in prompt
    # The committed trade is immutable: declare/watch are disabled (R18.6).
    assert "declare_trade" in prompt and "watch_price_condition" in prompt
    assert "IMMUTABLE" in prompt


def test_qa_context_available_tool_results_reflects_history():
    """R18.1/R18.4: tools already returned data are listed so a gap can be filled."""
    prior = [
        ("get_consensus_report", {"trend_score": 42, "current_price": 2450.0}),
        ("get_multi_tf_trend", {"trend_1d": "Neutral"}),
    ]
    ctx = build_qa_context(make_qa_state(_declared_decision(), prior_results=prior))
    assert "get_consensus_report" in ctx["available_tool_results"]
    assert "get_multi_tf_trend" in ctx["available_tool_results"]


def test_route_entry_selects_qa_handler():
    """route_entry sends QA-mode requests to the Q&A handler, else the loop."""
    assert route_entry({"mode": QA_MODE}) == "qa_agent"
    assert route_entry({"mode": "qa"}) == "qa_agent"
    assert route_entry({"mode": "FIND"}) == "agent"
    assert route_entry({}) == "agent"


def test_qa_should_continue_routes_and_terminates():
    """qa_should_continue fetches read-only data when requested, else ends, and
    is bounded by MAX_QA_TURNS."""
    ai_with_calls = AIMessage(content="", tool_calls=make_native_tool_calls(["get_consensus_report"]))
    assert qa_should_continue({"messages": [ai_with_calls], "qa_turns": 0}) == "tools"
    # Budget exhausted → end even with pending calls.
    assert qa_should_continue({"messages": [ai_with_calls], "qa_turns": MAX_QA_TURNS}) == "end"
    # No pending calls → final answer ends the run.
    assert qa_should_continue({"messages": [AIMessage(content="done")], "qa_turns": 0}) == "end"


# ── Q&A sub-agent delegation ─────────────────────────────────────────────────


def _qa_state_with_calls(names):
    """A QA state whose last message issues `names` as accepted tool calls.

    Mirrors what `qa_node` leaves behind: the calls plus the `_extraction_status`
    bookkeeping `qa_tool_node` reads to decide what may run.
    """
    calls = make_native_tool_calls(names)
    last = AIMessage(content="", tool_calls=calls)
    last.additional_kwargs["_extraction_status"] = {c["id"]: "ok" for c in calls}
    last.additional_kwargs["_synthetic_results"] = {}
    return {"messages": [HumanMessage(content="is this still valid?"), last]}


def test_qa_subagent_calls_are_intercepted_not_dispatched():
    """`run_debate` / `rerun_analysis` run in `qa_tool_node`, never in the ToolNode.

    The interception IS the mechanism: a plain tool receives only its declared
    arguments, and a debate needs the thread's gathered evidence. If these ever reached
    `_base_tool_node` they would execute the placeholder bodies and return the "must be
    executed by the Q&A tool node" string as if it were an answer.
    """
    recorder = RecordingToolNode()
    state = _qa_state_with_calls(["run_debate", "get_candles", "rerun_analysis"])

    with mock.patch.object(graph, "_base_tool_node", recorder), mock.patch.object(
        graph, "_run_qa_subagent", return_value='{"consensus": "aligned"}'
    ) as sub:
        out = graph.qa_tool_node(state)

    # The read-only tool went to the executor; neither sub-agent did.
    assert recorder.dispatched == ["get_candles"]
    assert {c.args[0] for c in sub.call_args_list} == {"run_debate", "rerun_analysis"}
    # Every call is answered, so the loop cannot stall on an unresolved tool call.
    assert len(out["messages"]) == 3
    # A Q&A turn NEVER commits (R18.6).
    assert "decision" not in out


def test_qa_subagent_failure_returns_text_not_an_exception():
    """A sub-agent that blows up must still produce a ToolMessage.

    It runs inside a user's follow-up question: an exception here would abort the turn
    and leave the composer locked, which is strictly worse than an answer that says the
    second opinion was unavailable.
    """
    with mock.patch.object(graph, "_run_debate_role", side_effect=RuntimeError("boom")):
        content = graph._run_qa_subagent("run_debate", {}, {"messages": []})

    assert isinstance(content, str) and "boom" in content


def test_qa_subagent_tools_all_have_a_branch():
    """Every name in `QA_SUBAGENT_TOOLS` is a real bound tool.

    Membership in that set is what makes a call intercepted rather than executed, so a
    name added to one and not the other silently becomes a call that resolves to nothing.
    """
    bound = {graph.run_debate.name, graph.rerun_analysis.name}
    assert bound == graph.QA_SUBAGENT_TOOLS
    # And they are NOT in the analysis tool set, or FIND could delegate mid-loop.
    assert not (graph.QA_SUBAGENT_TOOLS & {t.name for t in graph.tools})
