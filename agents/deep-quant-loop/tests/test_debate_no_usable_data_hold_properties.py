"""Property-based test that no usable market data yields a stated HOLD
(graph.py, task 8.7).

Feature: multi-agent-debate

This module implements design **Property 31: No usable market data yields a
stated HOLD**:

    For any debate run in which the Research_Phase gathered no usable market
    data (market-data tools attempted but returning no usable directional data),
    the finalized decision is a HOLD that states the data limitation.

Validates: Requirements 12.3.

The implementation under test is ``graph.tool_node``. The Research_Phase reuses
the existing single-agent data-gate: when ``market_data_seen`` is False and a
``declare_trade`` is attempted, ``tool_node`` checks ``_market_data_attempted``.
If market-data tools WERE attempted but returned only error / unavailable
results (attempted == True, seen == False), the run finalizes a HOLD with
``action == "HOLD"``, ``reason == "directional-data-unavailable"``,
``source == "data_gating"`` and a ``setup_validation`` string that states the
data limitation — exactly as the single-agent loop does today.

IMPORTANT — phase selection: during the research phase (``phase == "research"``)
``declare_trade`` is suppressed via a DIFFERENT path (the research → debate
handoff sets ``phase = "debate"`` and finalizes no decision). To exercise the
data-limitation HOLD path we therefore set ``phase = None`` so the standard
data-gate (``blocked_declares``) branch runs.

The test is hermetic: no LLM and no Rust server are involved. The ONLY ok-call
generated is the ``declare_trade`` call, which the data-gate strips before any
real tool would be invoked, so ``_base_tool_node.invoke`` is never reached with
a non-empty call list. ``graph._finalize_decision`` is monkeypatched to a stub
returning ``{}`` so no journal / DB write happens — the HOLD decision dict is
still built and set as ``update["decision"]``. The original is restored in a
``finally`` block on every hypothesis-generated input.

The sys.path / import pattern mirrors the sibling
``test_debate_research_never_commits_properties.py``.
"""

import json
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
from graph import tool_node, MARKET_DATA_TOOL_NAMES  # noqa: E402


# Market-data tools whose results may be attempted during research. Restricting
# to a stable, recognised subset keeps the generator meaningful while still
# varying which tool(s) were attempted. Every name is a member of
# MARKET_DATA_TOOL_NAMES, so each ToolMessage counts as an *attempt* (it just
# returned no usable data).
_MARKET_DATA_TOOLS = st.sampled_from(
    [
        "get_multi_tf_trend",
        "get_consensus_report",
        "get_support_resistance",
        "get_volume_profile",
        "get_chart_patterns",
        "get_market_regime",
        "get_relative_strength",
        "get_forecast",
        "get_prediction",
        "get_candles",
    ]
)

# Symbols/timeframes restricted to tokens that can never contain the "error" or
# "unavailable" substrings, so a result is classified error/unavailable purely
# by its structure, not by incidental text in a free-form field.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_serialization_style = st.sampled_from(["json", "repr"])


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string.

    Both quoting styles ("..." JSON and '...' dict-repr) flow through the stack,
    and ``_tool_result_is_error`` / ``_tool_result_is_unavailable`` match both.
    """
    return json.dumps(payload) if style == "json" else repr(payload)


@st.composite
def _error_content(draw):
    """An error result string (carries an ``error`` key)."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "error": draw(
            st.sampled_from(
                [
                    "no data",
                    "Failed to retrieve candles from Rust server: timeout",
                    "connection refused",
                    "contract_violation",
                    "candle retrieval failed",
                ]
            )
        ),
    }
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _unavailable_content(draw):
    """An explicit graceful-degradation (Unavailable_Marker) result string."""
    marker = draw(st.sampled_from(["status", "unavailable", "sentiment_summary"]))
    payload = {"symbol": draw(_symbol), "timeframe": draw(_timeframe)}
    if marker == "status":
        payload["status"] = "unavailable"
    elif marker == "unavailable":
        payload["unavailable"] = True
    else:
        payload["sentiment_summary"] = "Unavailable"
    payload["reason"] = draw(
        st.sampled_from(
            [
                "insufficient data: 12 valid candles available, 31 required",
                "retrieval timeout",
                "no usable directional data could be computed",
            ]
        )
    )
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _attempted_tool_message(draw):
    """A market-data ToolMessage whose content is error OR unavailable.

    Either way it counts as an *attempt* (the tool name is in
    MARKET_DATA_TOOL_NAMES) but does NOT yield usable directional data.
    """
    name = draw(_MARKET_DATA_TOOLS)
    content = draw(st.one_of(_error_content(), _unavailable_content()))
    return ToolMessage(content=content, tool_call_id=f"t_{draw(st.integers(0, 9999))}", name=name)


def _build_state(tool_messages):
    """Construct a non-research ``AgentState``-like dict whose history holds the
    given (error/unavailable) market-data ToolMessages and whose final message
    is an AIMessage carrying a single ``declare_trade`` ok-call.

    Built the way ``call_model`` builds the assistant message: ``tool_calls``
    list plus ``_extraction_status`` / ``_synthetic_results`` in
    ``additional_kwargs``.
    """
    ai_message = AIMessage(
        content="",
        tool_calls=[{"name": "declare_trade", "args": {"action": "BUY"}, "id": "d0"}],
    )
    ai_message.additional_kwargs["_extraction_status"] = {"d0": "ok"}
    ai_message.additional_kwargs["_synthetic_results"] = {}

    state = {
        "messages": list(tool_messages) + [ai_message],
        # phase = None (NOT "research") so the standard data-gate path runs.
        "phase": None,
        "market_data_seen": False,
        "reasoning_turns": 0,
        "mode": "DEBATE",
    }
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Property 31: No usable market data yields a stated HOLD
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 31: No usable market data yields a stated HOLD
@settings(max_examples=100, deadline=None)
@given(tool_messages=st.lists(_attempted_tool_message(), min_size=1, max_size=6))
def test_property_31_no_usable_data_yields_stated_hold(tool_messages):
    """Validates: Requirements 12.3

    For any run in which market-data tools were attempted but returned only
    error / unavailable results (no usable directional data), a ``declare_trade``
    issued while ``market_data_seen`` is False finalizes a HOLD that states the
    data limitation:
      * ``action == "HOLD"``,
      * ``source == "data_gating"``,
      * ``reason == "directional-data-unavailable"``,
      * ``setup_validation`` is a non-empty string stating the data limitation.
    """
    # Precondition: every generated tool message is a recognised market-data tool
    # (so it counts as an attempt).
    for m in tool_messages:
        assert m.name in MARKET_DATA_TOOL_NAMES

    state = _build_state(tool_messages)

    # Stub the finalize chokepoint so no journal / DB write happens; the HOLD
    # decision dict is still built and set as update["decision"].
    original_finalize = graph._finalize_decision
    graph._finalize_decision = lambda s, decision, thread_id=None: {}
    try:
        update = tool_node(state)
    finally:
        graph._finalize_decision = original_finalize

    decision = update.get("decision")
    assert decision is not None, "data-gate must finalize a HOLD decision, got none"

    assert decision.get("action") == "HOLD", (
        f"expected a HOLD action, got {decision.get('action')!r}"
    )
    assert decision.get("source") == "data_gating", (
        f"expected source 'data_gating', got {decision.get('source')!r}"
    )
    assert decision.get("reason") == "directional-data-unavailable", (
        f"expected reason 'directional-data-unavailable', got {decision.get('reason')!r}"
    )

    setup_validation = decision.get("setup_validation")
    assert isinstance(setup_validation, str) and setup_validation.strip(), (
        "setup_validation must be a non-empty string stating the data limitation, "
        f"got {setup_validation!r}"
    )
    # It must actually state the data limitation, not be arbitrary prose.
    assert "unavailable" in setup_validation.lower() or "data" in setup_validation.lower(), (
        f"setup_validation must state the data limitation, got {setup_validation!r}"
    )
