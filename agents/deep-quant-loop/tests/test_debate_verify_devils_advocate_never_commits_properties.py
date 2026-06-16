"""Property-based test that the VERIFY devil's advocate never commits or blocks
(graph.py, task 10.2).

Feature: multi-agent-debate

This module implements design **Property 32: VERIFY devil's-advocate never
commits or blocks**:

    The VERIFY-mode Bear devil's advocate never itself commits or blocks a trade
    — it only surfaces a stance in the verification reasoning; the existing
    VERIFY verdict path remains the decision authority.

Validates: Requirements 11.3.

Three layers of guarantee are exercised:

1. STRUCTURAL (binding-level, no LLM): the devil's advocate is bound via
   ``get_role_llm("bear")`` to the READ-ONLY tool set (``graph.readonly_tools``),
   which EXCLUDES the committing/suspending tools
   (``DEBATE_READONLY_EXCLUDED_TOOLS == {"declare_trade",
   "watch_price_condition"}``). This reuses the structural check from task 7.2's
   test pattern: a devil's advocate LLM can never even emit an executable
   ``declare_trade`` / ``watch_price_condition`` call.

2. BEHAVIOURAL (node-level, stubbed LLM): ``run_verify_devils_advocate`` is
   invoked with a stub Bear LLM (``graph.get_role_llm`` monkeypatched) whose
   ``.invoke(...)`` returns a fake ``AIMessage`` whose ``.content`` is drawn from
   a hypothesis strategy — including content that actively tries to look like a
   ``declare_trade`` / ``watch_price_condition`` call or that embeds a
   ``decision`` field. For EVERY generated content:
     * the function returns a message object (an ``AIMessage``), NOT a decision —
       the returned value is never a "decision" mapping and carries no
       ``"decision"`` key, and
     * the returned message has no (or empty) ``tool_calls`` — it cannot execute
       ``declare_trade`` / ``watch_price_condition``, and
     * the function never mutates ``state["decision"]`` (it stays whatever it was).

3. ROUTING (``_should_run_verify_devils_advocate``): the devil's advocate runs
   ONLY for ``mode == "VERIFY"`` (with the latch unset and market data seen) and
   never for FIND / QA / DEBATE — so outside VERIFY it never runs and therefore
   can never commit or block.

The sys.path / import pattern mirrors the sibling
``test_debate_only_judge_commits_properties.py``. Importing ``graph`` constructs
LLM client objects at import time but performs no network I/O, and the stubbed
``get_role_llm`` ensures no real LLM/network call happens during the test.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st
from langchain_core.messages import AIMessage, ToolMessage

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    DEBATE_READONLY_EXCLUDED_TOOLS,
    readonly_tools,
    run_verify_devils_advocate,
    _should_run_verify_devils_advocate,
)


# The committing / suspending tools the devil's advocate must NEVER be able to
# execute. ``declare_trade`` finalizes a decision; ``watch_price_condition``
# suspends the run. The verdict path — not the devil's advocate — decides (R11.3).
_COMMITTING_TOOLS = {"declare_trade", "watch_price_condition"}


class _StubResponse:
    """Minimal stand-in for an ``AIMessage`` response from the Bear role LLM.

    ``run_verify_devils_advocate`` only reads ``response.content`` (via
    ``getattr(response, "content", "")``). We expose ``content`` plus an empty
    ``tool_calls`` so nothing downstream that probes for tool calls crashes.
    """

    def __init__(self, content):
        self.content = content
        self.tool_calls = []
        self.additional_kwargs = {}


class _StubRoleLLM:
    """Stub returned by the monkeypatched ``get_role_llm``.

    ``.invoke(messages)`` ignores the messages entirely and returns a
    ``_StubResponse`` carrying the hypothesis-generated content, so NO real LLM /
    network call ever happens.
    """

    def __init__(self, content):
        self._content = content

    def invoke(self, messages):
        return _StubResponse(self._content)


# Hypothesis content strategy: arbitrary text, JSON-looking stances, fake
# tool-call text, and outright ``declare_trade`` / ``watch_price_condition`` JSON
# plus embedded ``decision`` payloads — i.e. content that actively TRIES to look
# like a commit / block attempt. None of it should ever let the devil's advocate
# finalize, block, or commit anything.
_FAKE_TOOL_TEXT = st.sampled_from(
    [
        '{"tool": "declare_trade", "args": {"action": "BUY", "entry": 100}}',
        '{"name": "declare_trade", "arguments": {"action": "SELL"}}',
        'declare_trade(action="BUY", entry=100, stop_loss=98, take_profit=104)',
        '{"tool_call": {"name": "watch_price_condition", "price_level": 105}}',
        'watch_price_condition(price_level=105, direction="above")',
        '{"lean": "long", "strength": 90, "decision": {"action": "BUY"}}',
        '{"action": "BLOCK", "verdict": "REJECT", "decision": "block the trade"}',
        '{"action": "BUY", "conviction_score": 95, "entry": 100}',
        '{"lean": "short", "strength": 80, "arguments": ["bearish"], "biggest_risk": "squeeze"}',
        '{"lean": "neutral", "strength": 10, "arguments": [], "biggest_risk": "noise"}',
    ]
)

_STANCE_JSON = st.fixed_dictionaries(
    {
        "lean": st.sampled_from(["long", "short", "neutral"]),
        "strength": st.integers(min_value=0, max_value=100),
    }
).map(
    lambda d: '{{"lean": "{lean}", "strength": {strength}, '
    '"arguments": ["evidence point"], "biggest_risk": "the risk"}}'.format(**d)
)

_CONTENT_STRATEGY = st.one_of(
    st.text(max_size=200),
    _FAKE_TOOL_TEXT,
    _STANCE_JSON,
    st.none(),  # exercise the role-failure / unavailable-stance path
)

# Evidence ToolMessage names drawn from the read-only analysis tools so the
# shared-evidence collection has realistic content to render.
_EVIDENCE_NAMES = st.sampled_from(
    [
        "get_candles",
        "get_consensus_report",
        "get_multi_tf_trend",
        "get_chart_patterns",
        "get_support_resistance",
        "get_market_regime",
    ]
)

_SIDE = st.sampled_from(["BUY", "SELL", "long", "short"])


def _build_verify_state(evidence, side, sentinel_decision):
    """Build a minimal VERIFY state with a manual trade and ToolMessage evidence.

    ``sentinel_decision`` is placed under ``state["decision"]`` so the test can
    assert the devil's advocate NEVER mutates it.
    """
    messages = [AIMessage(content="verification research complete", tool_calls=[])]
    for i, (name, content) in enumerate(evidence):
        messages.append(ToolMessage(content=content, name=name, tool_call_id=f"ev_{i}"))
    return {
        "messages": messages,
        "mode": "VERIFY",
        "symbol": "AAPL",
        "market_data_seen": True,
        "manual_trade": {
            "side": side,
            "entry": 100.0,
            "stop_loss": 98.0,
            "take_profit": 105.0,
            "user_analysis": "I think this breaks out.",
        },
        "verify_devils_advocate_done": None,
        "decision": sentinel_decision,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Structural guarantee (no LLM): the Bear devil's advocate binds the read-only
# tool set, which excludes the committing / suspending tools (reuses 7.2 check).
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 32: VERIFY devil's-advocate never commits or blocks
def test_property_32_devils_advocate_binds_readonly_tools():
    """Validates: Requirements 11.3

    The devil's advocate is bound via ``get_role_llm("bear")`` to the read-only
    tool set, whose exclusion set is exactly the committing/suspending tools and
    which therefore contains neither ``declare_trade`` nor
    ``watch_price_condition`` — so it cannot even emit an executable commit/block
    call.
    """
    assert DEBATE_READONLY_EXCLUDED_TOOLS == _COMMITTING_TOOLS, (
        "The debate read-only exclusion set must be exactly "
        f"{_COMMITTING_TOOLS!r}, got {DEBATE_READONLY_EXCLUDED_TOOLS!r}"
    )

    readonly_names = {getattr(t, "name", None) for t in readonly_tools}
    leaked = readonly_names & _COMMITTING_TOOLS
    assert not leaked, (
        "The Bear devil's-advocate read-only tool set must EXCLUDE every "
        f"committing/suspending tool, but these leaked through: {leaked!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Behavioural guarantee (stubbed LLM): the devil's advocate returns a message,
# never a decision, with no tool calls, and never mutates state["decision"].
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 32: VERIFY devil's-advocate never commits or blocks
@settings(max_examples=100, deadline=None)
@given(
    content=_CONTENT_STRATEGY,
    evidence=st.lists(st.tuples(_EVIDENCE_NAMES, st.text(max_size=80)), max_size=5),
    side=_SIDE,
    sentinel_decision=st.sampled_from([None, {"action": "HOLD", "from": "verdict_path"}]),
)
def test_property_32_devils_advocate_never_commits_or_blocks(
    content, evidence, side, sentinel_decision
):
    """Validates: Requirements 11.3

    For ANY devil's-advocate LLM content (including content that tries to look
    like a ``declare_trade`` / ``watch_price_condition`` call or that embeds a
    ``decision`` / verdict field), ``run_verify_devils_advocate``:
      * returns a message object (an ``AIMessage``) — NOT a decision; the returned
        value is never a mapping with a ``"decision"`` key, and
      * the returned message carries no (or empty) ``tool_calls`` — it cannot
        execute ``declare_trade`` / ``watch_price_condition``, and
      * never mutates ``state["decision"]`` (it remains the sentinel the verdict
        path owns).
    """
    # Stub the role LLM so no real LLM/network call happens and the content is
    # fully controlled by hypothesis. Save/restore explicitly (try/finally)
    # because the monkeypatch fixture is not reset between hypothesis examples.
    original_get_role_llm = graph.get_role_llm
    graph.get_role_llm = lambda role: _StubRoleLLM(content)
    try:
        state = _build_verify_state(evidence, side, sentinel_decision)
        # Capture the decision identity so we can prove it is untouched.
        decision_before = state["decision"]

        result = run_verify_devils_advocate(state, state["messages"])

        # 1. It returns a message object (AIMessage), NOT a decision.
        assert isinstance(result, AIMessage), (
            "run_verify_devils_advocate must return an AIMessage (a surfaced "
            f"stance), not a decision/other type, got {type(result)!r}"
        )

        # 2. The returned value is never a decision mapping / never carries a
        #    'decision' key (it only surfaces reasoning, it does not decide).
        assert not isinstance(result, dict), (
            "run_verify_devils_advocate must not return a decision mapping"
        )
        assert "decision" not in getattr(result, "additional_kwargs", {}), (
            "The surfaced message must not carry a committed 'decision' payload"
        )

        # 3. The returned message has no (or empty) tool_calls — it cannot execute
        #    declare_trade / watch_price_condition.
        tool_calls = getattr(result, "tool_calls", None) or []
        assert len(tool_calls) == 0, (
            "The devil's-advocate message must carry NO tool calls (it cannot "
            f"commit or block), but got tool_calls={tool_calls!r}"
        )

        # 4. It NEVER mutates state["decision"] — the verdict path stays authority.
        assert state["decision"] is decision_before, (
            "run_verify_devils_advocate must NEVER mutate state['decision']; the "
            f"VERIFY verdict path is the sole decision authority. before="
            f"{decision_before!r} after={state['decision']!r}"
        )
    finally:
        graph.get_role_llm = original_get_role_llm


# ─────────────────────────────────────────────────────────────────────────────
# Routing guarantee: the devil's advocate runs ONLY for VERIFY (latch unset,
# market data seen) and never for FIND / QA / DEBATE — so outside VERIFY it can
# never run and therefore never commit or block.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 32: VERIFY devil's-advocate never commits or blocks
@settings(max_examples=100, deadline=None)
@given(
    mode=st.sampled_from(["VERIFY", "FIND", "QA", "DEBATE", "verify", "find", "", "  "]),
    latched=st.booleans(),
    market_data_seen=st.booleans(),
)
def test_property_32_should_run_only_for_verify(mode, latched, market_data_seen):
    """Validates: Requirements 11.3

    ``_should_run_verify_devils_advocate`` returns True ONLY for a VERIFY run with
    the latch unset and market data seen, and False for FIND / QA / DEBATE — so
    outside VERIFY the devil's advocate never runs (and thus never commits or
    blocks).
    """
    state = {
        "market_data_seen": market_data_seen,
        "verify_devils_advocate_done": True if latched else None,
        "messages": [],
    }
    result = _should_run_verify_devils_advocate(state, mode, state["messages"])

    is_verify = (mode or "").strip().upper() == "VERIFY"

    if not is_verify:
        # Never runs outside VERIFY (FIND / QA / DEBATE / blank are unchanged).
        assert result is False, (
            f"Devil's advocate must NEVER run for non-VERIFY mode {mode!r}, "
            f"got {result!r}"
        )
    else:
        # In VERIFY it runs exactly when the latch is unset AND evidence is seen.
        expected = (not latched) and market_data_seen
        assert result == expected, (
            f"VERIFY gating mismatch: latched={latched} "
            f"market_data_seen={market_data_seen} -> expected {expected}, "
            f"got {result!r}"
        )
