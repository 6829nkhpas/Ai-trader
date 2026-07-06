"""Unit tests for `get_event_risk` graph registration (task 5.7).

Feature: earnings-event-risk-gate

Requirement 6 (Graph wiring of the Event Tool):
  6.1 THE Deep_Quant_Agent SHALL include `get_event_risk` in the `tools` list
      bound to the model in `graph.py`.
  6.2 THE Deep_Quant_Agent SHALL include `get_event_risk` in
      `REGISTERED_TOOL_NAMES` in `graph.py` so that a `get_event_risk` call is
      classified as a valid (not invalid-tool) call.
  6.3 THE Deep_Quant_Agent SHALL include `get_event_risk` in
      `MARKET_DATA_TOOL_NAMES` in `graph.py`.

These are plain, example-based pytest unit tests (no live LLM, no live Rust
server, no Hypothesis). The bound `tools` list holds `@tool`-decorated
StructuredTool wrappers, so membership is asserted by tool `.name`; the two
registry sets hold the plain tool-name strings.

The sys.path / import pattern mirrors the sibling
`tests/test_options_tool_registration_unit.py` and
`tests/test_forecast_graph_registration_unit.py`: the service directory (one
level up) is prepended to `sys.path` so `graph` is importable when pytest is
run from anywhere. The real LLM / Rust server is never invoked — this test only
inspects module-level registration data.
"""

import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402

EVENT_TOOL = "get_event_risk"


# ── Requirement 6.1: bound to the model via the `tools` list ─────────────────
def test_get_event_risk_in_bound_tools_list():
    """Validates: Requirements 6.1

    The tool must be present in the bound `tools` list so the model can call it.
    Each entry is a StructuredTool, so membership is checked by `.name`.
    """
    bound_names = [getattr(t, "name", None) for t in graph.tools]
    assert EVENT_TOOL in bound_names, (
        f"{EVENT_TOOL!r} not found among bound tool names: {bound_names!r}"
    )


# ── Requirement 6.2: registered as a known tool ──────────────────────────────
def test_get_event_risk_in_registered_tool_names():
    """Validates: Requirements 6.2

    The tool name must be in `REGISTERED_TOOL_NAMES` so a call to it is not
    classified as an invalid-tool call.
    """
    assert EVENT_TOOL in graph.REGISTERED_TOOL_NAMES


# ── Requirement 6.3: treated as a market-data source ─────────────────────────
def test_get_event_risk_in_market_data_tool_names():
    """Validates: Requirements 6.3

    The tool name must be in `MARKET_DATA_TOOL_NAMES` so a usable result is
    eligible to set the `market_data_seen` flag.
    """
    assert EVENT_TOOL in graph.MARKET_DATA_TOOL_NAMES
