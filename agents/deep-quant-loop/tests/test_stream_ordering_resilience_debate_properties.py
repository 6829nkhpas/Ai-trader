"""Property-based test for debate-mode stream ordering and resilience.

Feature: multi-agent-debate

This module implements design **Property 23: Stream ordering and resilience are
preserved** against the pure run assembler ``assemble_run_events`` in
``stream_events.py``:

    For ANY sequence of node updates (including DEBATE-mode updates with
    role-tagged reasoning messages and a debate decision) and ANY run outcome,
    ``assemble_run_events``:

      * emits ``RUN_STARTED`` as the first event (R8.6);
      * on success (``completed`` / ``paused``): emits exactly one terminal
        ``RUN_FINISHED`` as the LAST event, and no ``ERROR`` event;
      * on error (``RUN_ERROR``): emits an ``ERROR`` event and NO ``DECISION``
        event anywhere for the run, and no ``RUN_FINISHED`` (R12.4).

    This preserves the single-terminal-event and no-fabricated-decision-on-error
    guarantees with the debate additions (role-tagged reasoning events and the
    debate-consensus verification step / debate decision).

Validates: Requirements 8.6, 12.4.

The LLM and the graph are never invoked. Lightweight stub message objects whose
class names contain ``AIMessage`` / ``ToolMessage`` stand in for the LangChain
messages (``message_events`` dispatches on ``type(msg).__name__``); the role tag
is carried on the stub's ``additional_kwargs`` exactly as ``message_events`` reads
it for the debate roles (bull / bear / judge). The node-update strategy mixes
role-tagged reasoning, tool-call / tool-result round-trips, and a DEBATE decision
whose ``defensibility`` record carries a ``debate`` entry, so the assembler is
exercised on the full debate event vocabulary. The sys.path / import pattern
mirrors the sibling ``test_stream_events`` module.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (stream_events.py / graph.py live up one).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import (  # noqa: E402
    RUN_STARTED,
    RUN_FINISHED,
    ERROR,
    DECISION,
    RUN_COMPLETED,
    RUN_PAUSED,
    RUN_ERROR,
    assemble_run_events,
)

SETTINGS = settings(max_examples=100, deadline=None)


# ── Lightweight stub message objects ─────────────────────────────────────────
# ``message_events`` dispatches on ``type(msg).__name__`` containing the strings
# "AIMessage" / "ToolMessage", and reads a debate role tag from the AIMessage's
# ``additional_kwargs["role"]``. These stub class names + attributes are chosen to
# match exactly.
class StubAIMessage:
    def __init__(self, content="", tool_calls=None, role=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.type = "ai"
        # Role-tagged reasoning (bull / bear / judge) is carried here, mirroring
        # the debate role nodes (multi-agent-debate R8.1).
        self.additional_kwargs = {"role": role} if role else {}


class StubToolMessage:
    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


# ── Shared strategies ────────────────────────────────────────────────────────

# Plain natural-language reasoning text (printable ASCII, non-empty after strip).
nl_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=80,
).filter(lambda s: s.strip())

# The three debate role tags (multi-agent-debate R8.1) plus the untagged case.
role_tag = st.sampled_from(["bull", "bear", "judge", None])

TOOL_NAMES = [
    "get_candles",
    "get_multi_tf_trend",
    "get_support_resistance",
    "get_news_context",
    "get_consensus_report",
]
tool_name = st.sampled_from(TOOL_NAMES)


def _tool_call(name):
    return {"name": name, "args": {"timeframe": "15m"}, "id": f"call_{name}"}


# ── Debate decision strategy ─────────────────────────────────────────────────
# A DEBATE-mode decision whose defensibility record carries a `debate` entry
# (bull/bear stances + consensus + conviction), exactly the shape that drives the
# debate-consensus verification step and the DECISION event (R8.2/R8.5).
debate_consensus = st.sampled_from(["strong_agree", "lean", "contested"])

_stance = st.builds(
    lambda lean, strength, risk: {
        "lean": lean,
        "strength": strength,
        "arguments": ["cited evidence value"],
        "biggest_risk": risk,
    },
    st.sampled_from(["long", "short", "neutral"]),
    st.integers(min_value=0, max_value=100),
    nl_text,
)

debate_entry = st.builds(
    lambda bull, bear, consensus, conviction, basis: {
        "bull_stance": bull,
        "bear_stance": bear,
        "consensus": consensus,
        "conviction": conviction,
        "conviction_basis": basis,
    },
    _stance,
    _stance,
    debate_consensus,
    st.integers(min_value=0, max_value=100),
    nl_text,
)

debate_decision = st.builds(
    lambda action, conviction, validation, debate: {
        "action": action,
        "conviction_score": conviction,
        "setup_validation": validation,
        "defensibility": {"debate": debate},
    },
    st.sampled_from(["BUY", "SELL", "HOLD"]),
    st.integers(min_value=0, max_value=100),
    nl_text,
    debate_entry,
)


# ── Node-update strategies (the full debate event vocabulary) ────────────────

def _role_reasoning_update(content, role):
    """A node update carrying a role-tagged (bull/bear/judge) reasoning message."""
    return {"messages": [StubAIMessage(content=content, role=role)]}


def _tool_call_update(name):
    return {"messages": [StubAIMessage(content="", tool_calls=[_tool_call(name)])]}


def _tool_result_update(name):
    return {"messages": [StubToolMessage(content='{"ok": true}', name=name)]}


node_update_strategy = st.one_of(
    # Role-tagged debate reasoning (bull / bear / judge) or untagged reasoning.
    st.builds(_role_reasoning_update, nl_text, role_tag),
    # A tool call issued during the research phase / judge clarification.
    st.builds(_tool_call_update, tool_name),
    # The matching tool result.
    st.builds(_tool_result_update, tool_name),
    # A committed DEBATE decision (drives DECISION + debate-consensus step).
    st.builds(lambda d: {"decision": d}, debate_decision),
    # Non-dict / empty updates are tolerated by the assembler.
    st.just({}),
)

node_updates_strategy = st.lists(node_update_strategy, max_size=8)

thread_id_strategy = st.one_of(
    st.text(alphabet=st.characters(min_codepoint=48, max_codepoint=122), min_size=1, max_size=12),
    st.integers(min_value=0, max_value=10_000),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 23: Stream ordering and resilience are preserved
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 23: Stream ordering and resilience are preserved
@given(
    thread_id=thread_id_strategy,
    updates=node_updates_strategy,
    outcome=st.sampled_from([RUN_COMPLETED, RUN_PAUSED, RUN_ERROR]),
)
@SETTINGS
def test_property_23_stream_ordering_and_resilience(thread_id, updates, outcome):
    """Validates: Requirements 8.6, 12.4

    For ANY debate node updates (role-tagged reasoning, tool round-trips, and a
    debate decision) and ANY outcome, the assembled stream:
      * starts with RUN_STARTED (R8.6);
      * on success: exactly one terminal RUN_FINISHED as the last event, no ERROR;
      * on error: an ERROR event, NO DECISION anywhere, and no RUN_FINISHED (R12.4).
    """
    events = assemble_run_events(thread_id, updates, outcome, error_detail="boom")
    names = [n for n, _ in events]

    # ── RUN_STARTED is always the first event (R8.6). ─────────────────────────
    assert events, "assembled stream must never be empty"
    assert names[0] == RUN_STARTED, f"first event must be RUN_STARTED, got {names[0]!r}"
    assert events[0][1].get("thread_id") == thread_id
    # It appears exactly once and only at the front.
    assert names.count(RUN_STARTED) == 1

    if outcome != RUN_ERROR:
        # ── Success / pause: exactly one terminal RUN_FINISHED, last, no ERROR.
        assert names.count(RUN_FINISHED) == 1, (
            f"expected exactly one RUN_FINISHED, got {names.count(RUN_FINISHED)}"
        )
        assert names[-1] == RUN_FINISHED, f"last event must be RUN_FINISHED, got {names[-1]!r}"
        assert events[-1][1].get("status") == outcome
        assert ERROR not in names, "a successful run must emit no ERROR event"
    else:
        # ── Error: an ERROR event, NO DECISION anywhere, no RUN_FINISHED (R12.4).
        assert ERROR in names, "an errored run must surface an ERROR event"
        assert names[-1] == ERROR, f"last event of an errored run must be ERROR, got {names[-1]!r}"
        assert DECISION not in names, "an errored run must emit NO DECISION event"
        assert RUN_FINISHED not in names, "an errored run must emit no RUN_FINISHED"


# Feature: multi-agent-debate, Property 23: Stream ordering and resilience are preserved
@given(
    thread_id=thread_id_strategy,
    updates=node_updates_strategy,
    decision=debate_decision,
)
@SETTINGS
def test_property_23_errored_debate_with_committed_decision_emits_no_decision(
    thread_id, updates, decision
):
    """Validates: Requirements 8.6, 12.4

    Even when a committed DEBATE decision is present in the run updates, an errored
    run suppresses the DECISION event and surfaces a clean ERROR with no terminal
    RUN_FINISHED (no fabricated decision on failure, R12.4).
    """
    # Guarantee at least one committed debate decision so the suppression is
    # meaningfully exercised.
    updates = list(updates) + [{"decision": decision}]
    events = assemble_run_events(thread_id, updates, RUN_ERROR, error_detail="stream blew up")
    names = [n for n, _ in events]

    assert names[0] == RUN_STARTED
    assert ERROR in names
    assert names[-1] == ERROR
    assert DECISION not in names
    assert RUN_FINISHED not in names
    # The ERROR payload is a clean analysis-unavailable message (no trade plan).
    assert "AI analysis unavailable" in events[-1][1].get("error", "")
