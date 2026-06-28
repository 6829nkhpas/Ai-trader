"""Unit tests for `get_options_analytics` graph registration (task 6.3).

Feature: options-agent-integration

Requirement 4.1:
    THE Deep_Quant_Agent SHALL include `get_options_analytics` in the `tools`
    list, in `REGISTERED_TOOL_NAMES`, and in `MARKET_DATA_TOOL_NAMES` in
    `graph.py`.

These are example-based unit tests (no live LLM, no live Rust server, no
Hypothesis). The bound `tools` list holds ``@tool``-decorated StructuredTool
wrappers, so membership is asserted by tool ``.name``; the two registry sets
hold the plain tool-name strings.
"""

import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402


# ── Requirement 4.1: bound to the model via the `tools` list ─────────────────
def test_get_options_analytics_in_bound_tools_list():
    """Validates: Requirements 4.1

    The tool must be present in the bound `tools` list so the model can call it.
    Each entry is a StructuredTool, so membership is checked by `.name`.
    """
    bound_tool_names = {getattr(t, "name", None) for t in graph.tools}
    assert "get_options_analytics" in bound_tool_names


# ── Requirement 4.1: registered as a known tool ──────────────────────────────
def test_get_options_analytics_in_registered_tool_names():
    """Validates: Requirements 4.1

    The tool name must be in `REGISTERED_TOOL_NAMES` so a call to it is not
    classified as an invalid-tool call.
    """
    assert "get_options_analytics" in graph.REGISTERED_TOOL_NAMES


# ── Requirement 4.1: treated as a market-data source ─────────────────────────
def test_get_options_analytics_in_market_data_tool_names():
    """Validates: Requirements 4.1

    The tool name must be in `MARKET_DATA_TOOL_NAMES` so a usable result is
    eligible to set the `market_data_seen` flag.
    """
    assert "get_options_analytics" in graph.MARKET_DATA_TOOL_NAMES
