# Feature: multi-agent-debate (task 15.3): Bull/Bear tool binding
"""Example/unit test for the Bull/Bear read-only tool binding (task 15.3).

Feature: multi-agent-debate

Task 7.1 bound the Bull_Agent and Bear_Agent to a READ-ONLY Analysis_Tool set
that excludes the trade-committing / run-suspending tools, and the roles argue
over the ALREADY-gathered Shared_Evidence (the ToolMessages in
``state["messages"]``) without re-running the tool-gathering loop. This test
pins that contract down with concrete examples:

  1. ``DEBATE_READONLY_EXCLUDED_TOOLS`` is exactly
     ``{"declare_trade", "watch_price_condition"}`` (R2.3 — no committing /
     suspending tool is available to a debate role).
  2. ``readonly_tools`` contains NO tool named ``declare_trade`` or
     ``watch_price_condition``.
  3. Every tool in ``readonly_tools`` is also a tool in the full ``tools`` set —
     no NEW data source is introduced; the read-only set is a strict subset of
     the existing analysis tools (R12.5). Concretely the read-only names equal
     the full tool names minus the two excluded names.
  4. ``_collect_shared_evidence`` reads the gathered ToolMessages and returns
     them as text WITHOUT re-gathering / invoking any tool (R2.3): given a small
     messages list it returns one line per usable ToolMessage and nothing else.

Validates: Requirements 2.3, 12.5

The sys.path / import pattern mirrors
``tests/test_forecast_graph_registration_unit.py``: the service directory (one
level up) is prepended to ``sys.path`` so ``graph`` is importable when pytest is
run from anywhere. The real LLM / Rust server is never invoked — this test only
inspects module-level binding data and calls the pure
``_collect_shared_evidence`` helper.
"""

import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

import graph  # noqa: E402

EXCLUDED = {"declare_trade", "watch_price_condition"}


def test_excluded_tools_are_exactly_declare_trade_and_watch():
    """R2.3: the read-only exclusion set is exactly the committing/suspending tools."""
    assert graph.DEBATE_READONLY_EXCLUDED_TOOLS == EXCLUDED


def test_readonly_tools_exclude_committing_and_suspending_tools():
    """R2.3: Bull/Bear bindings offer neither `declare_trade` nor `watch_price_condition`."""
    readonly_names = [getattr(t, "name", None) for t in graph.readonly_tools]
    assert "declare_trade" not in readonly_names
    assert "watch_price_condition" not in readonly_names


def test_readonly_tools_are_strict_subset_of_full_tools_no_new_data_source():
    """R12.5: the read-only set is the full tool set minus the excluded tools.

    No NEW data source is introduced for the debate — every read-only tool is an
    existing analysis tool from the full ``tools`` list.
    """
    full_names = {getattr(t, "name", None) for t in graph.tools}
    readonly_names = {getattr(t, "name", None) for t in graph.readonly_tools}

    # Every read-only tool is also an existing full tool (no new data source).
    assert readonly_names <= full_names

    # Concretely the read-only names equal the full names minus the excluded set.
    assert readonly_names == (full_names - EXCLUDED)


def test_collect_shared_evidence_reads_toolmessages_without_regathering():
    """R2.3: evidence is derived ONLY from the provided ToolMessages, no re-gather.

    ``_collect_shared_evidence`` is pure: it walks the supplied messages, renders
    each usable ToolMessage as a ``<name>: <content>`` line, and returns those
    lines. It does not invoke any tool or hit the network — so the returned lines
    are derived solely from the messages we pass in.
    """
    messages = [
        SystemMessage(content="system prompt"),
        HumanMessage(content="analyze NIFTY"),
        AIMessage(content="", additional_kwargs={}),
        ToolMessage(
            content="regime=trending_up adx=28.4",
            tool_call_id="c1",
            name="get_market_regime",
        ),
        ToolMessage(
            content="rs_score=72 vs benchmark NIFTY",
            tool_call_id="c2",
            name="get_relative_strength",
        ),
    ]

    evidence = graph._collect_shared_evidence(messages)

    # Exactly one line per usable ToolMessage — nothing fabricated, nothing else.
    assert evidence == [
        "get_market_regime: regime=trending_up adx=28.4",
        "get_relative_strength: rs_score=72 vs benchmark NIFTY",
    ]
    assert len(evidence) == 2


def test_collect_shared_evidence_skips_non_tool_and_empty_messages():
    """R2.3: only usable ToolMessages contribute; non-tool/empty messages are ignored."""
    messages = [
        HumanMessage(content="prompt"),
        ToolMessage(content="", tool_call_id="empty", name="get_candles"),
        ToolMessage(content="   ", tool_call_id="blank", name="get_order_flow"),
        ToolMessage(
            content="patterns: bull_flag",
            tool_call_id="c3",
            name="get_chart_patterns",
        ),
    ]

    evidence = graph._collect_shared_evidence(messages)

    # The empty/whitespace ToolMessages and the HumanMessage are dropped.
    assert evidence == ["get_chart_patterns: patterns: bull_flag"]


def test_collect_shared_evidence_handles_empty_input():
    """R2.3: no messages yields no evidence (still pure, never raises)."""
    assert graph._collect_shared_evidence([]) == []
    assert graph._collect_shared_evidence(None) == []
