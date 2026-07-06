"""Property-based test that only the Judge commits; Bull and Bear cannot
(graph.py, task 7.2).

Feature: multi-agent-debate

This module implements design **Property 10: Only the Judge commits; Bull and
Bear cannot**:

    For ANY set of tool calls a Bull_Agent or Bear_Agent node attempts (including
    ``declare_trade`` and ``watch_price_condition``), none of those committing /
    suspending tools execute and the node never sets, modifies, or blocks
    ``state["decision"]``; the only role whose validated ``declare_trade`` can
    finalize a decision is the Judge_Agent.

Validates: Requirements 3.5, 4.5, 12.1.

Two layers of guarantee are exercised:

1. STRUCTURAL (binding-level, no LLM): ``DEBATE_READONLY_EXCLUDED_TOOLS`` is
   exactly the committing/suspending tool set ``{"declare_trade",
   "watch_price_condition"}`` and the read-only tool set the Bull/Bear roles bind
   to (``graph.readonly_tools``) contains NONE of those tool names. This is the
   reason a Bull/Bear LLM can never even emit an executable ``declare_trade`` /
   ``watch_price_condition`` call.

2. BEHAVIOURAL (node-level, stubbed LLM): ``bull_node`` / ``bear_node`` are
   invoked with a stub role LLM (``graph.get_role_llm`` monkeypatched) whose
   ``.invoke(...)`` returns a fake response whose ``.content`` is drawn from a
   hypothesis strategy — arbitrary prose, JSON stances, fake tool-call-looking
   text, and ``declare_trade`` JSON. For EVERY generated content the returned
   state update:
     * never contains a ``"decision"`` key (Bull/Bear never commit), and
     * only touches the allowed bookkeeping keys (``bull_stance`` / ``bear_stance``,
       ``debate_turns``, ``phase``, ``debate_round``).

The sys.path / import pattern mirrors the sibling
``test_debate_research_never_commits_properties.py``. Importing ``graph``
constructs LLM client objects at import time but performs no network I/O, and the
stubbed ``get_role_llm`` ensures no real LLM/network call happens during the test.
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
    bull_node,
    bear_node,
)


# The committing / suspending tools that a Bull/Bear role must NEVER be able to
# execute. ``declare_trade`` finalizes a decision; ``watch_price_condition``
# suspends the run. Only the Judge may commit (R3.5, R4.5, R12.1).
_COMMITTING_TOOLS = {"declare_trade", "watch_price_condition"}

# The keys a Bull/Bear node update is permitted to set. Crucially this set
# excludes ``decision``: the role nodes are stance-emitters that never finalize,
# modify, or block a decision. They MAY emit a ``messages`` update carrying their
# role-tagged reasoning so the glass-box stream can surface it as a distinct
# role-tagged REASONING event (multi-agent-debate, R8.1) — but that message must
# be pure reasoning with NO executable committing/suspending tool calls (asserted
# separately below), so Property 10 (only the Judge commits) still holds.
_ALLOWED_UPDATE_KEYS = {
    "bull_stance",
    "bear_stance",
    "debate_turns",
    "phase",
    "debate_round",
    "messages",
}


class _StubResponse:
    """Minimal stand-in for an ``AIMessage`` response from a role LLM.

    ``_run_debate_role`` only reads ``response.content`` (via
    ``getattr(response, "content", "")``) and passes it to
    ``_extract_stance_payload`` -> ``parse_stance``. We expose ``content`` plus an
    empty ``tool_calls`` so nothing downstream that probes for tool calls crashes.
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
# — i.e. content that actively TRIES to look like a commit attempt. None of it
# should ever let a Bull/Bear node finalize a decision.
_FAKE_TOOL_TEXT = st.sampled_from(
    [
        '{"tool": "declare_trade", "args": {"action": "BUY", "entry": 100}}',
        '{"name": "declare_trade", "arguments": {"action": "SELL"}}',
        'declare_trade(action="BUY", entry=100, stop_loss=98, take_profit=104)',
        '{"tool_call": {"name": "watch_price_condition", "price_level": 105}}',
        'watch_price_condition(price_level=105, direction="above")',
        '{"lean": "long", "strength": 90, "decision": {"action": "BUY"}}',
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


def _build_state(evidence, debate_turns):
    """Build a minimal DEBATE state with some ToolMessage evidence."""
    messages = [AIMessage(content="research complete", tool_calls=[])]
    for i, (name, content) in enumerate(evidence):
        messages.append(ToolMessage(content=content, name=name, tool_call_id=f"ev_{i}"))
    return {
        "messages": messages,
        "mode": "DEBATE",
        "phase": "debate",
        "debate_turns": debate_turns,
        "debate_round": (debate_turns // 2) + 1,
        "bull_stance": None,
        "bear_stance": None,
        "decision": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Structural guarantee (no LLM): the read-only binding excludes the committing
# and suspending tools.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 10: Only the Judge commits; Bull and Bear cannot
def test_property_10_readonly_binding_excludes_committing_tools():
    """Validates: Requirements 3.5, 4.5, 12.1

    The exclusion set is exactly the committing/suspending tools, and the
    read-only tool set the Bull/Bear roles bind to contains none of them — so a
    Bull/Bear LLM cannot even emit an executable ``declare_trade`` /
    ``watch_price_condition`` call.
    """
    assert DEBATE_READONLY_EXCLUDED_TOOLS == _COMMITTING_TOOLS, (
        "The debate read-only exclusion set must be exactly "
        f"{_COMMITTING_TOOLS!r}, got {DEBATE_READONLY_EXCLUDED_TOOLS!r}"
    )

    readonly_names = {getattr(t, "name", None) for t in readonly_tools}
    leaked = readonly_names & _COMMITTING_TOOLS
    assert not leaked, (
        "The Bull/Bear read-only tool set must EXCLUDE every committing/suspending "
        f"tool, but these leaked through the binding: {leaked!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Behavioural guarantee (stubbed LLM): Bull/Bear nodes never commit a decision.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 10: Only the Judge commits; Bull and Bear cannot
@settings(max_examples=100, deadline=None)
@given(
    content=_CONTENT_STRATEGY,
    evidence=st.lists(st.tuples(_EVIDENCE_NAMES, st.text(max_size=80)), max_size=5),
    debate_turns=st.integers(min_value=0, max_value=6),
)
def test_property_10_bull_bear_never_commit(content, evidence, debate_turns):
    """Validates: Requirements 3.5, 4.5, 12.1

    For ANY role-LLM content (including content that tries to look like a
    ``declare_trade`` / ``watch_price_condition`` call or that embeds a
    ``decision`` field), neither ``bull_node`` nor ``bear_node`` may:
      * set, modify, or block ``state["decision"]`` (no ``"decision"`` key in the
        returned update), nor
      * touch any key beyond the allowed stance/bookkeeping set.
    """
    # Stub the role LLM so no real LLM/network call happens and the content is
    # fully controlled by hypothesis. Save/restore explicitly (try/finally)
    # instead of the monkeypatch fixture, which hypothesis does not reset between
    # generated inputs.
    original_get_role_llm = graph.get_role_llm
    graph.get_role_llm = lambda role: _StubRoleLLM(content)
    try:
        for node, stance_key in ((bull_node, "bull_stance"), (bear_node, "bear_stance")):
            state = _build_state(evidence, debate_turns)
            update = node(state)

            # 1. The node NEVER finalizes / modifies / blocks a decision.
            assert "decision" not in update, (
                f"{node.__name__} must NEVER set a 'decision' key, but returned "
                f"decision={update.get('decision')!r}"
            )

            # 2. The node only touches the allowed bookkeeping keys.
            extra = set(update.keys()) - _ALLOWED_UPDATE_KEYS
            assert not extra, (
                f"{node.__name__} update contains disallowed keys {extra!r}; only "
                f"{_ALLOWED_UPDATE_KEYS!r} are permitted"
            )

            # 2b. Any emitted reasoning messages must be PURE reasoning: they may
            #     carry the role tag (R8.1) but NEVER an executable committing /
            #     suspending tool call, so a Bull/Bear node can never commit or
            #     suspend via a message either (Property 10 preserved/strengthened).
            for msg in update.get("messages", []) or []:
                tool_calls = getattr(msg, "tool_calls", None) or []
                leaked = {tc.get("name") for tc in tool_calls} & _COMMITTING_TOOLS
                assert not leaked, (
                    f"{node.__name__} emitted a message carrying committing/"
                    f"suspending tool calls {leaked!r}; role messages must be pure "
                    "reasoning"
                )
                kwargs = getattr(msg, "additional_kwargs", None) or {}
                assert kwargs.get("role") == stance_key.split("_")[0], (
                    f"{node.__name__} reasoning message must be tagged with its role "
                    f"({stance_key.split('_')[0]!r}) for R8.1, got "
                    f"{kwargs.get('role')!r}"
                )

            # 3. The node produces its own stance bookkeeping (sanity: it ran and
            #    the only side effect is an advisory stance, never a commitment).
            assert stance_key in update, (
                f"{node.__name__} should record its advisory stance under "
                f"{stance_key!r}"
            )
            assert update.get("phase") == "debate", (
                f"{node.__name__} must stay in the debate phase, got "
                f"{update.get('phase')!r}"
            )
    finally:
        graph.get_role_llm = original_get_role_llm
