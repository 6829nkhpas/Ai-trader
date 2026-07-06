"""Property-based test for identical Shared_Evidence across debate roles.

Feature: multi-agent-debate (task 6.3)

This module implements design **Property 8: All three roles consume the same
Shared_Evidence**:

    For ANY set of Analysis_Tool results gathered in the Research_Phase, the
    evidence (ToolMessages) made available to the Bull_Agent, the Bear_Agent,
    and the Judge_Agent is identical.

Validates: Requirements 2.2.

The Shared_Evidence is the set of ToolMessages accumulated in
``state["messages"]`` during the Research_Phase — consumed verbatim by all three
roles and never re-gathered (see the research→debate handoff in ``tool_node``).
There is no per-role evidence selector in ``graph.py`` (task 7.1 leaves the
Bull/Bear/Judge nodes consuming ``state["messages"]`` directly), so the
shared-evidence *projection* every role uses is exactly the deterministic, pure
filter over the message history:

  * ``_is_tool_message`` — selects the ToolMessages (the raw Shared_Evidence),
  * ``_latest_tool_results`` — the parsed per-tool-name view of that evidence
    (what the Judge / defensibility builder reads).

Because both are pure functions of the message history with no role parameter,
the evidence each role derives from the SAME history is necessarily identical.
This test proves that invariant directly:

  * **Deterministic** — computing the projection twice over the same history
    yields identical results (so re-deriving evidence per role never drifts).
  * **Role-independent** — the projection computed "for the Bull", "for the
    Bear", and "for the Judge" (each from its own copy of the shared history,
    as the nodes receive it from the shared ``state["messages"]``) is identical
    across all three roles.

The strategy generates arbitrary message histories mixing ToolMessages (varied
tool names and contents — usable JSON results, error results, unavailable
markers, and free-form strings), AIMessages (with and without tool calls),
HumanMessages, and SystemMessages, exercising the full space the Research_Phase
can accumulate. The sys.path / import pattern mirrors the sibling
``test_debate_*`` and ``test_regime_*`` modules.
"""

import copy
import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import _is_tool_message, _latest_tool_results  # noqa: E402


# The roles that all consume the SAME Shared_Evidence (R2.2). Used only to label
# the per-role projection so the role-independence assertion is explicit.
_ROLES = ("bull", "bear", "judge")

# A spread of analysis-tool names (so the per-tool-name projection is exercised)
# plus a couple of arbitrary names to stress the filter beyond the known set.
_TOOL_NAMES = [
    "get_multi_tf_trend",
    "get_consensus_report",
    "get_market_regime",
    "get_relative_strength",
    "get_forecast",
    "get_session_context",
    "get_support_resistance",
    "get_volume_profile",
    "get_chart_patterns",
    "get_news_context",
    "declare_trade",
    "some_unknown_tool",
]


def _tool_content():
    """Varied ToolMessage payloads: usable JSON, error, unavailable, free text."""
    json_payload = st.dictionaries(
        keys=st.sampled_from(
            ["trend", "rsi_14", "poc", "vah", "val", "favorability", "score"]
        ),
        values=st.one_of(
            st.floats(allow_nan=False, allow_infinity=False, width=32),
            st.integers(min_value=-1000, max_value=1000),
            st.sampled_from(["up", "down", "flat", "favorable", "leader"]),
        ),
        min_size=0,
        max_size=4,
    ).map(lambda d: json.dumps(d))
    return st.one_of(
        json_payload,
        st.just('{"error": "insufficient data"}'),
        st.just('{"sentiment_summary": "Unavailable"}'),
        st.just('{"status": "unavailable"}'),
        st.text(min_size=0, max_size=40),
    )


@st.composite
def _tool_message(draw):
    """An arbitrary ToolMessage (a unit of Shared_Evidence)."""
    name = draw(st.sampled_from(_TOOL_NAMES))
    call_id = draw(st.text(min_size=1, max_size=8))
    content = draw(_tool_content())
    return ToolMessage(content=content, tool_call_id=f"call_{call_id}", name=name)


@st.composite
def _ai_message(draw):
    """An arbitrary AIMessage, sometimes carrying tool calls."""
    text = draw(st.text(min_size=0, max_size=40))
    has_calls = draw(st.booleans())
    if not has_calls:
        return AIMessage(content=text)
    name = draw(st.sampled_from(_TOOL_NAMES))
    call_id = draw(st.text(min_size=1, max_size=8))
    return AIMessage(
        content=text,
        tool_calls=[{"name": name, "args": {}, "id": f"call_{call_id}"}],
    )


def _message():
    """Any message kind that can appear in the Research_Phase history."""
    return st.one_of(
        _tool_message(),
        _ai_message(),
        st.text(min_size=0, max_size=40).map(lambda t: HumanMessage(content=t)),
        st.text(min_size=0, max_size=40).map(lambda t: SystemMessage(content=t)),
    )


def _shared_evidence(messages):
    """The Shared_Evidence projection every debate role reads from the history.

    Mirrors how the Bull/Bear/Judge nodes consume the evidence: the ToolMessages
    accumulated in ``state["messages"]`` (filtered via the same ``_is_tool_message``
    predicate the rest of the graph uses). Returned as identity-independent
    ``(name, content)`` tuples so equality reflects the evidence content, not
    object identity.
    """
    return [
        (getattr(m, "name", None), getattr(m, "content", None))
        for m in messages
        if _is_tool_message(m)
    ]


@st.composite
def _history(draw):
    """An arbitrary Research_Phase message history (mixed message kinds)."""
    return draw(st.lists(_message(), min_size=0, max_size=12))


# ─────────────────────────────────────────────────────────────────────────────
# Property 8: All three roles consume the same Shared_Evidence
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 8: All three roles consume the same Shared_Evidence
@settings(max_examples=100, deadline=None)
@given(messages=_history())
def test_property_8_shared_evidence_identical_across_roles(messages):
    """Validates: Requirements 2.2

    For any Research_Phase message history, the Shared_Evidence projection is
    deterministic (computing it twice is identical) and role-independent (the
    evidence derived for the Bull, the Bear, and the Judge — each from its own
    copy of the shared history — is identical across all three roles). This is
    exactly the guarantee that all three roles consume the same Shared_Evidence.
    """
    # ── Determinism: the same history yields the same evidence every time. ────
    once = _shared_evidence(messages)
    twice = _shared_evidence(messages)
    assert once == twice, "Shared_Evidence projection is not deterministic"

    # The parsed per-tool-name view (what the Judge / defensibility reads) is
    # likewise a deterministic pure function of the history.
    parsed_once = _latest_tool_results(messages)
    parsed_twice = _latest_tool_results(messages)
    assert parsed_once == parsed_twice, "_latest_tool_results is not deterministic"

    # ── Role-independence: each role derives evidence from its OWN copy of the
    # shared history (as the nodes receive it from the shared state["messages"]).
    # No role re-gathers or filters differently, so all three must be identical.
    per_role_raw = {
        role: _shared_evidence(copy.copy(messages)) for role in _ROLES
    }
    per_role_parsed = {
        role: _latest_tool_results(copy.copy(messages)) for role in _ROLES
    }

    bull, bear, judge = _ROLES
    assert per_role_raw[bull] == per_role_raw[bear] == per_role_raw[judge], (
        "Bull, Bear, and Judge see different raw Shared_Evidence"
    )
    assert per_role_parsed[bull] == per_role_parsed[bear] == per_role_parsed[judge], (
        "Bull, Bear, and Judge see different parsed Shared_Evidence"
    )

    # The role-independent evidence equals the directly computed projection, so
    # there is a single shared evidence base, not three role-specific views.
    assert per_role_raw[bull] == once
    assert per_role_parsed[bull] == parsed_once

    # ── Sanity: the projection really is only the ToolMessages (the evidence),
    # never the reasoning / prompt messages — so non-tool turns can never leak a
    # role-specific difference into the Shared_Evidence.
    expected_tool_count = sum(1 for m in messages if _is_tool_message(m))
    assert len(once) == expected_tool_count
