"""Unit tests for the `get_relative_strength` tool shape (task 5.3).

Feature: relative-strength-context

Requirement 4.1:
    THE Relative_Strength_Tool SHALL be exposed to the Deep_Quant_Agent as an
    `@tool`-decorated function named `get_relative_strength` following the
    existing tool pattern in `tools.py`.

Requirement 4.2:
    THE Relative_Strength_Tool SHALL accept a `symbol` argument, an optional
    explicit `benchmark` argument, and a `timeframe` argument, and SHALL resolve
    the Benchmark_Index via the Benchmark_Map when no explicit benchmark is
    provided.

These are example-based unit tests (no live LLM, no live Rust server, no
Hypothesis). The `@tool` decorator from `langchain_core.tools` wraps the
function as a ``StructuredTool``, so we assert against that wrapper:

  (a) `get_relative_strength` is a StructuredTool / BaseTool instance (i.e.
      `@tool`-decorated), not a bare function;
  (b) its `.name` is exactly ``"get_relative_strength"``;
  (c) its argument schema exposes ``symbol``, ``timeframe``, and an optional
      ``benchmark`` (plus the optional ``proposed_direction``).
"""

import os
import sys

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import get_relative_strength  # noqa: E402

from langchain_core.tools import BaseTool, StructuredTool  # noqa: E402


# ── Requirement 4.1: `@tool`-decorated and correctly named ───────────────────
def test_get_relative_strength_is_tool_decorated():
    """Validates: Requirements 4.1

    A bare ``def`` would not be a ``BaseTool``; the ``@tool`` decorator wraps it
    as a StructuredTool. The tool must also be reachable on the module.
    """
    assert isinstance(get_relative_strength, BaseTool)
    assert isinstance(get_relative_strength, StructuredTool)
    assert isinstance(tools.get_relative_strength, BaseTool)


def test_get_relative_strength_name():
    """Validates: Requirements 4.1

    The exposed tool name must be exactly ``get_relative_strength`` so the agent
    and the registry can address it.
    """
    assert get_relative_strength.name == "get_relative_strength"


# ── Requirement 4.2: accepts `symbol`, `timeframe`, optional `benchmark` ─────
def test_get_relative_strength_accepts_symbol_timeframe_benchmark():
    """Validates: Requirements 4.2

    The tool's argument schema must expose ``symbol`` and ``timeframe`` (both
    required) and an optional ``benchmark`` argument. ``StructuredTool.args`` is
    the resolved JSON-schema properties mapping.
    """
    args = get_relative_strength.args
    assert "symbol" in args
    assert "timeframe" in args
    assert "benchmark" in args
    # proposed_direction is the other optional arg in the established pattern.
    assert "proposed_direction" in args


def test_get_relative_strength_args_schema_fields():
    """Validates: Requirements 4.1, 4.2

    Cross-check the underlying args_schema as well, so the assertion does not
    rely solely on the convenience ``.args`` view.
    """
    schema = get_relative_strength.args_schema
    # Pydantic v1/v2 both expose model fields; resolve whichever is present.
    fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", {})
    field_names = set(fields.keys())
    assert {"symbol", "timeframe", "benchmark", "proposed_direction"}.issubset(
        field_names
    )


def test_get_relative_strength_benchmark_is_optional():
    """Validates: Requirements 4.2

    ``benchmark`` (and ``proposed_direction``) must be optional: the signature
    defaults them to empty strings so callers may omit them and let the
    Benchmark_Map resolve the benchmark.
    """
    schema = get_relative_strength.args_schema
    fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", {})

    benchmark_field = fields["benchmark"]
    # Not required: a default is present (Pydantic v2 `is_required()`, or a
    # non-Ellipsis default on v1).
    is_required = getattr(benchmark_field, "is_required", None)
    if callable(is_required):
        assert is_required() is False
    else:
        assert getattr(benchmark_field, "required", False) is False
