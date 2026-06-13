"""Unit test for `get_forecast` tool registration in `graph.py` (task 8.2).

Feature: volatility-aware-forecaster

Task 8.1 wired the Volatility_Aware_Forecaster's `get_forecast` tool into the
agent graph. This plain pytest unit test pins that wiring down by asserting the
tool is registered in all three places the graph depends on:

  1. The bound ``tools`` list passed to ``llm.bind_tools(...)`` — so the LLM is
     actually offered the tool (Requirement 7.1).
  2. ``REGISTERED_TOOL_NAMES`` — so a ``get_forecast`` call discovered in model
     output is recognised as a valid Analysis_Tool rather than flagged invalid
     (Requirement 7.2).
  3. ``MARKET_DATA_TOOL_NAMES`` — so a usable ``get_forecast`` result counts as
     market data for the ``market_data_seen`` gate (Requirement 7.3).

Validates: Requirements 7.1, 7.2, 7.3.

The sys.path / import pattern mirrors ``tests/test_regime_market_data_gate_properties.py``:
the service directory (one level up) is prepended to ``sys.path`` so ``graph``
is importable when pytest is run from anywhere. The real LLM / Rust server is
never invoked — this test only inspects module-level registration data.
"""

import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402

FORECAST_TOOL = "get_forecast"


def test_get_forecast_in_registered_tool_names():
    """R7.2: a discovered `get_forecast` call is a valid Analysis_Tool."""
    assert FORECAST_TOOL in graph.REGISTERED_TOOL_NAMES


def test_get_forecast_in_market_data_tool_names():
    """R7.3: a usable `get_forecast` result participates in the market-data gate."""
    assert FORECAST_TOOL in graph.MARKET_DATA_TOOL_NAMES


def test_get_forecast_in_bound_tools_list():
    """R7.1: `get_forecast` is bound to the LLM via the `tools` list."""
    bound_names = [getattr(t, "name", None) for t in graph.tools]
    assert FORECAST_TOOL in bound_names, (
        f"{FORECAST_TOOL!r} not found among bound tool names: {bound_names!r}"
    )
