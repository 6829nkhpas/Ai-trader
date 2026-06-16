"""Property-based test that the DEBATE Research_Phase never commits a trade
(graph.py, task 6.2).

Feature: multi-agent-debate

This module implements design **Property 7: The Research_Phase never commits a
trade**:

    For ANY sequence of tool calls issued while ``phase == "research"`` (including
    one or more ``declare_trade`` calls), no decision is finalized —
    ``state["decision"]`` remains unset and the run proceeds to the debate roles
    rather than terminating as committed.

Validates: Requirements 2.1.

The test exercises ``graph.tool_node`` directly at the unit level (no real LLM
and no network I/O). It constructs an ``AgentState``-like dict with
``phase == "research"`` and a final ``AIMessage`` carrying an arbitrary set of
tool calls — always at least one ``declare_trade`` ok-call, optionally mixed with
malformed (status != "ok") read-only calls — built exactly the way
``call_model`` builds it (``tool_calls`` list plus ``_extraction_status`` and
``_synthetic_results`` in ``additional_kwargs``).

To keep the test hermetic we use approach (a) from the task: the ONLY ok-calls
generated are ``declare_trade`` calls. During the research phase ``tool_node``
strips every ``declare_trade`` ok-call before it would invoke a real tool, so
``_base_tool_node.invoke`` is only ever reached with a (here empty) list of
non-declare ok-calls — meaning no tool ever hits the Rust server at
localhost:8084. The optional malformed calls (``parse_failure`` /
``invalid_tool``) are answered with synthetic feedback and likewise never invoke
a real tool.

The sys.path / import pattern mirrors the sibling
``test_debate_non_debate_routing_invariance_properties.py``. Importing ``graph``
constructs LLM client objects at import time but performs no network I/O, so a
plain import is safe here.
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

from graph import tool_node  # noqa: E402


# Read-only / non-declare tool names used to populate the malformed (non-ok)
# calls. These are NEVER executed (status != "ok"), so the names only need to be
# plausible — they are answered with synthetic feedback. ``declare_trade`` is
# deliberately excluded so the count of suppressed declarations equals exactly
# the number of ok declare_trade calls.
_NON_DECLARE_NAMES = st.one_of(
    st.sampled_from(
        [
            "get_candles",
            "get_consensus_report",
            "get_multi_tf_trend",
            "get_chart_patterns",
            "get_support_resistance",
            "get_volume_profile",
            "get_market_regime",
            "watch_price_condition",
        ]
    ),
    st.text(max_size=16),
)

# Statuses that mark a call as malformed: it is answered synthetically and never
# dispatched to a real tool.
_FAILED_STATUS = st.sampled_from(["parse_failure", "invalid_tool"])

# Arbitrary declare_trade args — irrelevant to the property because the call is
# suppressed before any execution, but varied to exercise the "arbitrary set of
# tool calls" clause of the property.
_declare_args = st.fixed_dictionaries(
    {
        "action": st.sampled_from(["BUY", "SELL", "HOLD"]),
        "conviction_score": st.integers(min_value=0, max_value=100),
    }
)


def _build_state(num_declares, failed_calls, market_data_seen):
    """Construct a research-phase ``AgentState``-like dict whose final message
    carries ``num_declares`` ok ``declare_trade`` calls plus the given malformed
    (non-ok) calls, built the way ``call_model`` builds the assistant message.
    """
    tool_calls = []
    statuses = {}
    synthetic = {}

    # 1..n ok declare_trade calls (the suppressed declarations).
    declare_ids = []
    for i in range(num_declares):
        cid = f"declare_{i}"
        declare_ids.append(cid)
        tool_calls.append({"name": "declare_trade", "args": {"action": "BUY"}, "id": cid})
        statuses[cid] = "ok"

    # Optional malformed non-declare calls answered with synthetic feedback.
    failed_ids = []
    for j, (name, status) in enumerate(failed_calls):
        cid = f"failed_{j}"
        failed_ids.append(cid)
        tool_calls.append({"name": name, "args": {}, "id": cid})
        statuses[cid] = status
        synthetic[cid] = f"Synthetic feedback for malformed '{name}' ({status})."

    ai_message = AIMessage(content="", tool_calls=tool_calls)
    ai_message.additional_kwargs["_extraction_status"] = statuses
    ai_message.additional_kwargs["_synthetic_results"] = synthetic

    state = {
        "messages": [ai_message],
        "phase": "research",
        "market_data_seen": market_data_seen,
        "reasoning_turns": 0,
        "mode": "DEBATE",
    }
    return state, declare_ids


# ─────────────────────────────────────────────────────────────────────────────
# Property 7: The Research_Phase never commits a trade
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 7: The Research_Phase never commits a trade
@settings(max_examples=100, deadline=None)
@given(
    num_declares=st.integers(min_value=1, max_value=5),
    failed_calls=st.lists(st.tuples(_NON_DECLARE_NAMES, _FAILED_STATUS), max_size=4),
    market_data_seen=st.booleans(),
)
def test_property_7_research_phase_never_commits(num_declares, failed_calls, market_data_seen):
    """Validates: Requirements 2.1

    For any set of tool calls issued during the research phase that includes one
    or more ``declare_trade`` ok-calls, ``tool_node`` must:
      * NEVER finalize a decision (no truthy ``"decision"`` in the update), and
      * hand off to the debate roles by transitioning ``phase`` to ``"debate"``, and
      * produce a synthetic ``declare_trade`` ToolMessage for each suppressed
        declaration so no call is left unanswered.
    """
    state, declare_ids = _build_state(num_declares, failed_calls, market_data_seen)

    update = tool_node(state)

    # 1. Research never commits: no finalized decision in the update.
    assert not update.get("decision"), (
        "Research_Phase must NOT finalize a decision, but tool_node returned "
        f"decision={update.get('decision')!r}"
    )

    # 2. A declare_trade during research hands off to the debate roles.
    assert update.get("phase") == "debate", (
        "A suppressed declare_trade during research must transition phase to "
        f"'debate', got {update.get('phase')!r}"
    )

    # 3. Every suppressed declare_trade is answered with a synthetic ToolMessage.
    declare_tool_msg_ids = {
        m.tool_call_id
        for m in update.get("messages", [])
        if isinstance(m, ToolMessage) and m.name == "declare_trade"
    }
    for cid in declare_ids:
        assert cid in declare_tool_msg_ids, (
            f"Suppressed declare_trade {cid!r} was not answered with a synthetic "
            "ToolMessage"
        )
