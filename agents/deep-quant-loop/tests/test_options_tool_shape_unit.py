"""Unit tests for the `get_options_analytics` tool shape (task 4.3).

Feature: options-agent-integration

Requirement 2.1:
    THE Options_Tool SHALL be exposed as an `@tool`-decorated function named
    `get_options_analytics` following the existing tool pattern in `tools.py`.

Requirement 2.2:
    THE Options_Tool SHALL accept a `symbol` argument, an optional `expiry`
    argument, and an optional `proposed_direction` argument.

These are example-based unit tests (no live LLM, no live Rust server, no
Hypothesis). The `@tool` decorator from `langchain_core.tools` wraps the
function as a ``StructuredTool``, so we assert against that wrapper:

  (a) `get_options_analytics` is a StructuredTool / BaseTool instance (i.e.
      `@tool`-decorated), not a bare function;
  (b) its `.name` is exactly ``"get_options_analytics"``;
  (c) its argument schema exposes ``symbol`` (required) plus optional
      ``expiry`` and ``proposed_direction`` arguments (each defaulting to an
      empty string).
"""

import os
import sys

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import tools  # noqa: E402
from tools import get_options_analytics  # noqa: E402

from langchain_core.tools import BaseTool, StructuredTool  # noqa: E402


def _resolve_fields(schema):
    """Return the schema's model-field mapping (Pydantic v1 or v2)."""
    return getattr(schema, "model_fields", None) or getattr(schema, "__fields__", {})


def _field_is_optional(field) -> bool:
    """True when a Pydantic field is not required (Pydantic v1/v2 compatible)."""
    is_required = getattr(field, "is_required", None)
    if callable(is_required):
        return is_required() is False
    return getattr(field, "required", False) is False


# ── Requirement 2.1: `@tool`-decorated and correctly named ───────────────────
def test_get_options_analytics_is_tool_decorated():
    """Validates: Requirements 2.1

    A bare ``def`` would not be a ``BaseTool``; the ``@tool`` decorator wraps it
    as a StructuredTool. The tool must also be reachable on the module.
    """
    assert isinstance(get_options_analytics, BaseTool)
    assert isinstance(get_options_analytics, StructuredTool)
    assert isinstance(tools.get_options_analytics, BaseTool)


def test_get_options_analytics_name():
    """Validates: Requirements 2.1

    The exposed tool name must be exactly ``get_options_analytics`` so the agent
    and the registry can address it.
    """
    assert get_options_analytics.name == "get_options_analytics"


# ── Requirement 2.2: accepts `symbol`, optional `expiry` + direction ─────────
def test_get_options_analytics_accepts_symbol_expiry_direction():
    """Validates: Requirements 2.2

    The tool's argument schema must expose ``symbol`` plus the optional
    ``expiry`` and ``proposed_direction`` arguments. ``StructuredTool.args`` is
    the resolved JSON-schema properties mapping.
    """
    args = get_options_analytics.args
    assert "symbol" in args
    assert "expiry" in args
    assert "proposed_direction" in args


def test_get_options_analytics_args_schema_fields():
    """Validates: Requirements 2.1, 2.2

    Cross-check the underlying args_schema as well, so the assertion does not
    rely solely on the convenience ``.args`` view.
    """
    schema = get_options_analytics.args_schema
    field_names = set(_resolve_fields(schema).keys())
    assert {"symbol", "expiry", "proposed_direction"}.issubset(field_names)


def test_get_options_analytics_symbol_is_required():
    """Validates: Requirements 2.2

    ``symbol`` is the one mandatory argument: it has no default, so callers must
    supply it.
    """
    schema = get_options_analytics.args_schema
    fields = _resolve_fields(schema)
    assert _field_is_optional(fields["symbol"]) is False


def test_get_options_analytics_expiry_and_direction_are_optional():
    """Validates: Requirements 2.2

    ``expiry`` and ``proposed_direction`` must be optional: the signature
    defaults each to an empty string so callers may omit them (nearest expiry /
    neutral alignment).
    """
    schema = get_options_analytics.args_schema
    fields = _resolve_fields(schema)
    assert _field_is_optional(fields["expiry"]) is True
    assert _field_is_optional(fields["proposed_direction"]) is True
