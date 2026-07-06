"""Unit tests for `get_market_regime` graph registration (task 7.2).

Feature: regime-detection-gate

Requirement 5.1:
    THE Deep_Quant_Agent SHALL include `get_market_regime` in the `tools` list
    bound to the model in `graph.py`.

Requirement 5.2:
    THE Deep_Quant_Agent SHALL include `get_market_regime` in
    `REGISTERED_TOOL_NAMES` in `graph.py` so that a `get_market_regime` call is
    classified as a valid (not invalid-tool) call.

Requirement 5.3:
    THE Deep_Quant_Agent SHALL include `get_market_regime` in
    `MARKET_DATA_TOOL_NAMES` in `graph.py`.

These are example-based unit tests (no live LLM, no live Rust server, no
Hypothesis). They assert that:

  (a) `"get_market_regime"` is in `REGISTERED_TOOL_NAMES`;
  (b) `"get_market_regime"` is in `MARKET_DATA_TOOL_NAMES`;
  (c) the bound `tools` list contains a tool whose `.name == "get_market_regime"`.
"""

import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import (  # noqa: E402
    tools as bound_tools,
    REGISTERED_TOOL_NAMES,
    MARKET_DATA_TOOL_NAMES,
)


# ── Requirement 5.2: registered as a valid tool name ─────────────────────────
def test_get_market_regime_in_registered_tool_names():
    """Validates: Requirements 5.2"""
    assert "get_market_regime" in REGISTERED_TOOL_NAMES


# ── Requirement 5.3: classified as a market-data tool ────────────────────────
def test_get_market_regime_in_market_data_tool_names():
    """Validates: Requirements 5.3"""
    assert "get_market_regime" in MARKET_DATA_TOOL_NAMES


# ── Requirement 5.1: bound into the model's tool list ────────────────────────
def test_get_market_regime_in_bound_tools_list():
    """Validates: Requirements 5.1"""
    bound_names = {getattr(t, "name", None) for t in bound_tools}
    assert "get_market_regime" in bound_names
