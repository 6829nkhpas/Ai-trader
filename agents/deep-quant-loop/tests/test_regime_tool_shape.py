"""Unit tests for the `get_market_regime` tool shape (task 5.3).

Feature: regime-detection-gate

Requirement 3.1:
    THE Market_Regime_Tool SHALL be exposed to the Deep_Quant_Agent as an
    `@tool`-decorated function named `get_market_regime` following the existing
    tool pattern in `tools.py`.

Requirement 3.2:
    THE Market_Regime_Tool SHALL accept a `symbol` argument and a `timeframe`
    argument.

These are example-based unit tests (no live LLM, no live Rust server, no
Hypothesis). The `@tool` decorator from `langchain_core.tools` wraps the
function as a ``StructuredTool``, so we assert against that wrapper:

  (a) `get_market_regime` is a StructuredTool instance (i.e. `@tool`-decorated),
      not a bare function;
  (b) its `.name` is exactly ``"get_market_regime"``;
  (c) its argument schema exposes both ``symbol`` and ``timeframe``.
"""

import os
import sys

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import get_market_regime  # noqa: E402

from langchain_core.tools import BaseTool  # noqa: E402


# ── Requirement 3.1: `@tool`-decorated and correctly named ───────────────────
def test_get_market_regime_is_tool_decorated():
    """Validates: Requirements 3.1

    A bare ``def`` would not be a ``BaseTool``; the ``@tool`` decorator wraps it
    as a StructuredTool. The tool must also be reachable on the module.
    """
    assert isinstance(get_market_regime, BaseTool)
    assert isinstance(tools.get_market_regime, BaseTool)


def test_get_market_regime_name():
    """Validates: Requirements 3.1

    The exposed tool name must be exactly ``get_market_regime`` so the agent and
    the registry can address it.
    """
    assert get_market_regime.name == "get_market_regime"


# ── Requirement 3.2: accepts `symbol` and `timeframe` ────────────────────────
def test_get_market_regime_accepts_symbol_and_timeframe():
    """Validates: Requirements 3.2

    The tool's argument schema must expose both ``symbol`` and ``timeframe``.
    ``StructuredTool.args`` is the resolved JSON-schema properties mapping.
    """
    args = get_market_regime.args
    assert "symbol" in args
    assert "timeframe" in args


def test_get_market_regime_args_schema_fields():
    """Validates: Requirements 3.2

    Cross-check the underlying args_schema as well, so the assertion does not
    rely solely on the convenience ``.args`` view.
    """
    schema = get_market_regime.args_schema
    # Pydantic v1/v2 both expose model fields; resolve whichever is present.
    fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", {})
    field_names = set(fields.keys())
    assert {"symbol", "timeframe"}.issubset(field_names)
