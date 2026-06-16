"""Property-based test for completion-from-decision routing (graph.py, task 8.3).

Feature: multi-agent-debate

This module implements design **Property 12: Completion is read only from the
committed decision**:

    For ANY debate message history containing trade-like reasoning prose but no
    committed decision in ``state["decision"]``, the run does not terminate as
    committed; termination as a committed trade requires a validated decision in
    ``state["decision"]``.

Validates: Requirements 5.1.

The routing functions ``should_continue(state)`` and ``route_after_tools(state)``
read completion ONLY from ``state.get("decision")`` — never from keyword-matching
the reasoning prose carried in ``messages``. This test confirms that invariant by
generating message histories stuffed with trade-like prose (``AIMessage`` content
containing "BUY", "declare_trade", "I commit to a long", JSON-looking decision
blobs, etc.) and asserting:

  * With NO ``decision`` and NO pending tool calls on the last message,
    ``should_continue`` NEVER returns ``"end"`` purely because of the prose — it
    returns ``"loop_agent"`` / ``"force_hold"`` / ``DEBATE_HANDOFF`` depending on
    ``reasoning_turns`` / ``phase`` instead.
  * With a non-empty ``decision`` dict, ``should_continue`` returns ``"end"``.
  * ``route_after_tools`` returns ``"end"`` if and only if ``state["decision"]``
    is truthy, regardless of the message prose.

The last message is always constructed as an ``AIMessage`` with NO ``tool_calls``
so the pending-tool-call precedence (which takes priority over the decision /
reasoning checks) falls through to the completion logic under test. The import /
sys.path pattern mirrors the sibling ``test_*_properties`` modules (import from
``graph``).
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the agent package importable (graph.py lives one level up), mirroring the
# sibling property-test modules in this directory.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from graph import (  # noqa: E402
    DEBATE_HANDOFF,
    MAX_REASONING_TURNS,
    route_after_tools,
    should_continue,
)

# Routing labels ``should_continue`` may return when NO decision is committed and
# there are no pending tool calls — i.e. every non-terminal continuation.
_NON_END_NO_DECISION = {"loop_agent", "force_hold", DEBATE_HANDOFF}

# Trade-like prose fragments. None of these are a committed decision; they are the
# kind of reasoning text a keyword-matcher might naively mistake for completion.
_TRADE_PROSE = [
    "BUY",
    "SELL",
    "HOLD",
    "declare_trade",
    "I commit to a long position here.",
    "Final decision: SELL NIFTY at market.",
    "conviction_score: 82",
    '{"action": "BUY", "entry": 100.0, "stop_loss": 98.0, "take_profit": 104.0}',
    "EXECUTE the trade now.",
    "My verdict is a strong long.",
    "TAKE PROFIT at R2, stop below the HVN.",
    "This is an A+ setup, going long.",
    "",
]

# DEBATE and non-DEBATE phase values to exercise both the force_hold path and the
# DEBATE_HANDOFF path on reasoning exhaustion.
_PHASES = [None, "research", "debate", "find", "verify", "qa", "weird-phase"]


@st.composite
def _prose_messages(draw):
    """A message history of arbitrary trade-like prose.

    The history is a mix of Human / AI messages whose content is drawn from the
    trade-like prose fragments, with the LAST message always an ``AIMessage`` that
    carries NO tool calls (so the pending-tool-call precedence falls through to
    the completion / reasoning logic under test).
    """
    n = draw(st.integers(min_value=0, max_value=4))
    history = []
    for _ in range(n):
        content = draw(st.sampled_from(_TRADE_PROSE))
        if draw(st.booleans()):
            history.append(AIMessage(content=content))
        else:
            history.append(HumanMessage(content=content))
    # Last message: an AIMessage with explicit trade-like prose and NO tool_calls.
    last_content = draw(st.sampled_from(_TRADE_PROSE))
    history.append(AIMessage(content=last_content))
    return history


@st.composite
def _decision_dict(draw):
    """A non-empty (truthy) committed decision dict with an ``action`` key."""
    action = draw(st.sampled_from(["BUY", "SELL", "HOLD"]))
    decision = {"action": action, "conviction_score": draw(st.integers(0, 100))}
    if draw(st.booleans()):
        decision["reason"] = draw(st.sampled_from(["a+ setup", "no-decision-reached", ""]))
    return decision


def _base_state(messages, reasoning_turns, phase):
    """Assemble an AgentState-shaped dict for the routing functions."""
    return {
        "messages": messages,
        "reasoning_turns": reasoning_turns,
        "phase": phase,
        "decision": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 12: Completion is read only from the committed decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 12: Completion is read only from the committed decision
@settings(max_examples=100, deadline=None)
@given(
    messages=_prose_messages(),
    reasoning_turns=st.integers(min_value=0, max_value=MAX_REASONING_TURNS + 3),
    phase=st.sampled_from(_PHASES),
)
def test_property_12_no_decision_never_ends(messages, reasoning_turns, phase):
    """Validates: Requirements 5.1

    Trade-like prose with NO committed decision (and no pending tool calls) must
    never route to ``"end"`` — completion requires ``state["decision"]``. The run
    continues via ``loop_agent`` / ``force_hold`` / ``DEBATE_HANDOFF`` instead.
    """
    state = _base_state(messages, reasoning_turns, phase)

    route = should_continue(state)

    assert route != "end", (
        f"should_continue returned 'end' from prose alone (no decision); "
        f"reasoning_turns={reasoning_turns}, phase={phase!r}"
    )
    assert route in _NON_END_NO_DECISION, (
        f"unexpected route {route!r} for no-decision state; "
        f"expected one of {_NON_END_NO_DECISION}"
    )

    # The specific continuation is fully determined by reasoning_turns / phase,
    # NOT by the prose — confirm the deterministic branch is taken.
    if reasoning_turns < MAX_REASONING_TURNS:
        assert route == "loop_agent"
    elif phase in ("research", "debate"):
        assert route == DEBATE_HANDOFF
    else:
        assert route == "force_hold"


# Feature: multi-agent-debate, Property 12: Completion is read only from the committed decision
@settings(max_examples=100, deadline=None)
@given(
    messages=_prose_messages(),
    reasoning_turns=st.integers(min_value=0, max_value=MAX_REASONING_TURNS + 3),
    phase=st.sampled_from(_PHASES),
    decision=_decision_dict(),
)
def test_property_12_committed_decision_ends(messages, reasoning_turns, phase, decision):
    """Validates: Requirements 5.1

    A committed (truthy) ``state["decision"]`` is the ONLY thing that terminates
    the run as committed — ``should_continue`` returns ``"end"`` regardless of the
    reasoning prose, ``reasoning_turns``, or ``phase``.
    """
    state = _base_state(messages, reasoning_turns, phase)
    state["decision"] = decision

    assert should_continue(state) == "end", (
        f"should_continue must return 'end' when a decision is committed "
        f"(decision={decision!r})"
    )


# Feature: multi-agent-debate, Property 12: Completion is read only from the committed decision
@settings(max_examples=100, deadline=None)
@given(
    messages=_prose_messages(),
    phase=st.sampled_from(_PHASES),
    decision=st.one_of(st.none(), _decision_dict()),
)
def test_property_12_route_after_tools_ends_iff_decision(messages, phase, decision):
    """Validates: Requirements 5.1

    ``route_after_tools`` returns ``"end"`` if and only if ``state["decision"]``
    is truthy, irrespective of the trade-like message prose or the phase.
    """
    state = _base_state(messages, reasoning_turns=0, phase=phase)
    state["decision"] = decision

    route = route_after_tools(state)

    assert (route == "end") == bool(decision), (
        f"route_after_tools terminated={route == 'end'} but decision-present="
        f"{bool(decision)} (decision={decision!r}, phase={phase!r})"
    )
    if not decision:
        # Without a decision it continues to the agent, or hands off to the
        # debate roles when the research phase has completed (phase == "debate").
        expected = DEBATE_HANDOFF if phase == "debate" else "agent"
        assert route == expected, f"expected {expected!r} continuation, got {route!r}"
