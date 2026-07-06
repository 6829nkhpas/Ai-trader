"""Unit tests for `get_relative_strength` graph registration (task 7.2).

Feature: relative-strength-context

Requirement 6.1:
    THE Deep_Quant_Agent SHALL include `get_relative_strength` in the `tools`
    list bound to the model in `graph.py`.

Requirement 6.2:
    THE Deep_Quant_Agent SHALL include `get_relative_strength` in
    `REGISTERED_TOOL_NAMES` in `graph.py` so that a `get_relative_strength` call
    is classified as a valid (not invalid-tool) call.

Requirement 6.3:
    THE Deep_Quant_Agent SHALL include `get_relative_strength` in
    `MARKET_DATA_TOOL_NAMES` in `graph.py`.

These are example-based unit tests (no live LLM, no live Rust server, no
Hypothesis). They assert that:

  (a) `"get_relative_strength"` is in `REGISTERED_TOOL_NAMES`;
  (b) `"get_relative_strength"` is in `MARKET_DATA_TOOL_NAMES`;
  (c) the bound `tools` list contains a tool whose `.name == "get_relative_strength"`.
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


# ── Requirement 6.2: registered as a valid tool name ─────────────────────────
def test_get_relative_strength_in_registered_tool_names():
    """Validates: Requirements 6.2"""
    assert "get_relative_strength" in REGISTERED_TOOL_NAMES


# ── Requirement 6.3: classified as a market-data tool ────────────────────────
def test_get_relative_strength_in_market_data_tool_names():
    """Validates: Requirements 6.3"""
    assert "get_relative_strength" in MARKET_DATA_TOOL_NAMES


# ── Requirement 6.1: bound into the model's tool list ────────────────────────
def test_get_relative_strength_in_bound_tools_list():
    """Validates: Requirements 6.1"""
    bound_names = {getattr(t, "name", None) for t in bound_tools}
    assert "get_relative_strength" in bound_names
