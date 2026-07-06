"""Unit tests for the `get_forecast` tool shape (task 6.3).

Feature: volatility-aware-forecaster

Requirement 5.1:
    THE Forecast_Tool SHALL be exposed to the Deep_Quant_Agent as an
    `@tool`-decorated function named `get_forecast` following the existing tool
    pattern in `tools.py`.

Requirement 5.2:
    THE Forecast_Tool SHALL accept a `symbol` argument, a `timeframe` argument,
    and an optional `proposed_direction` argument.

These are example-based unit tests (no live LLM, no live Rust server, no
Hypothesis). The `@tool` decorator from `langchain_core.tools` wraps the
function as a ``StructuredTool``, so we assert against that wrapper:

  (a) `get_forecast` is a StructuredTool / BaseTool instance (i.e.
      `@tool`-decorated), not a bare function;
  (b) its `.name` is exactly ``"get_forecast"``;
  (c) its argument schema exposes ``symbol``, ``timeframe``, and an optional
      ``proposed_direction``.
"""

import os
import sys

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import get_forecast  # noqa: E402

from langchain_core.tools import BaseTool, StructuredTool  # noqa: E402


# ── Requirement 5.1: `@tool`-decorated and correctly named ───────────────────
def test_get_forecast_is_tool_decorated():
    """Validates: Requirements 5.1

    A bare ``def`` would not be a ``BaseTool``; the ``@tool`` decorator wraps it
    as a StructuredTool. The tool must also be reachable on the module.
    """
    assert isinstance(get_forecast, BaseTool)
    assert isinstance(get_forecast, StructuredTool)
    assert isinstance(tools.get_forecast, BaseTool)


def test_get_forecast_name():
    """Validates: Requirements 5.1

    The exposed tool name must be exactly ``get_forecast`` so the agent and the
    registry can address it.
    """
    assert get_forecast.name == "get_forecast"


# ── Requirement 5.2: accepts `symbol`, `timeframe`, optional direction ───────
def test_get_forecast_accepts_symbol_timeframe_direction():
    """Validates: Requirements 5.2

    The tool's argument schema must expose ``symbol`` and ``timeframe`` (both
    required) and an optional ``proposed_direction`` argument.
    ``StructuredTool.args`` is the resolved JSON-schema properties mapping.
    """
    args = get_forecast.args
    assert "symbol" in args
    assert "timeframe" in args
    assert "proposed_direction" in args


def test_get_forecast_args_schema_fields():
    """Validates: Requirements 5.1, 5.2

    Cross-check the underlying args_schema as well, so the assertion does not
    rely solely on the convenience ``.args`` view.
    """
    schema = get_forecast.args_schema
    # Pydantic v1/v2 both expose model fields; resolve whichever is present.
    fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", {})
    field_names = set(fields.keys())
    assert {"symbol", "timeframe", "proposed_direction"}.issubset(field_names)


def test_get_forecast_proposed_direction_is_optional():
    """Validates: Requirements 5.2

    ``proposed_direction`` must be optional: the signature defaults it to an
    empty string so callers may omit it and let alignment default to neutral.
    """
    schema = get_forecast.args_schema
    fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", {})

    direction_field = fields["proposed_direction"]
    # Not required: a default is present (Pydantic v2 `is_required()`, or a
    # non-Ellipsis default on v1).
    is_required = getattr(direction_field, "is_required", None)
    if callable(is_required):
        assert is_required() is False
    else:
        assert getattr(direction_field, "required", False) is False
