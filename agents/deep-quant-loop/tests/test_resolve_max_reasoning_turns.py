"""Unit test for ``resolve_max_reasoning_turns`` (graph.py, task 4.1).

Feature: agent-loop-responsiveness

Covers design **Property 1: Reasoning budget is configurable, bounded, and
safe** at the example level:

  * A valid, in-range env value for ``DEEP_QUANT_MAX_REASONING_TURNS`` is
    returned verbatim.
  * Unset / empty / whitespace / non-integer / out-of-range (`< 1`) inputs all
    fall back to the documented default ``MAX_REASONING_TURNS`` (6).
  * The resolver never raises for any of those inputs.

Validates: Requirements 1.1, 1.3.

The env var is controlled via ``monkeypatch.setenv`` / ``monkeypatch.delenv`` so
the mutation is scoped to each test and cannot leak into sibling tests. The
sys.path / import pattern mirrors the sibling ``test_extract_tool_calls`` and
``test_completion_from_decision_properties`` modules.
"""

import os
import sys

import pytest

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` resolves exactly as every sibling test module expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import MAX_REASONING_TURNS, resolve_max_reasoning_turns  # noqa: E402

ENV_VAR = "DEEP_QUANT_MAX_REASONING_TURNS"


def test_documented_default_is_six():
    """The fallback constant is the documented default of 6 (R1.1)."""
    assert MAX_REASONING_TURNS == 6


def test_valid_positive_integer_is_returned(monkeypatch):
    """A valid, in-range env value is returned verbatim (R1.1)."""
    monkeypatch.setenv(ENV_VAR, "9")
    assert resolve_max_reasoning_turns() == 9


def test_valid_value_with_surrounding_whitespace_is_parsed(monkeypatch):
    """A valid value padded with whitespace is stripped and parsed (R1.1)."""
    monkeypatch.setenv(ENV_VAR, "  12  ")
    assert resolve_max_reasoning_turns() == 12


def test_minimum_valid_value_one_is_returned(monkeypatch):
    """The boundary value 1 is in range and returned verbatim (R1.1)."""
    monkeypatch.setenv(ENV_VAR, "1")
    assert resolve_max_reasoning_turns() == 1


def test_unset_falls_back_to_default(monkeypatch):
    """An unset variable falls back to the default (R1.3)."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert resolve_max_reasoning_turns() == MAX_REASONING_TURNS


@pytest.mark.parametrize(
    "raw",
    [
        "",          # empty
        "   ",       # whitespace only
        "\t",        # tab
        "\n  ",       # newline + spaces
        "abc",       # non-integer
        "1.5",       # float-like, not an int
        "1e3",       # scientific notation, not an int
        "0x10",      # hex literal, not a plain int
        "3,5",       # comma, not an int
        "0",         # out of range (< 1)
        "-1",        # negative, out of range
        "-100",      # negative, out of range
    ],
)
def test_degraded_and_out_of_range_fall_back_to_default(monkeypatch, raw):
    """Empty / whitespace / non-integer / out-of-range all degrade to the
    default and never raise (R1.3)."""
    monkeypatch.setenv(ENV_VAR, raw)
    result = resolve_max_reasoning_turns()
    assert result == MAX_REASONING_TURNS


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "abc", "0", "-5", "7", "  7  "],
)
def test_never_raises_for_any_input(monkeypatch, raw):
    """The resolver is total: it returns an int >= 1 for any input and never
    raises (R1.3)."""
    if raw is None:
        monkeypatch.delenv(ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(ENV_VAR, raw)

    result = resolve_max_reasoning_turns()
    assert isinstance(result, int) and not isinstance(result, bool)
    assert result >= 1
