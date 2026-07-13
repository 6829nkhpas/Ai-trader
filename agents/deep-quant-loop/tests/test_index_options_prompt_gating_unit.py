"""Unit tests for the symbol-class-aware options prompt gating (task 6.2).

Feature: index-options-intraday-context

These example-based unit tests (no live LLM, no live Rust server, no Hypothesis)
assert **Property 2 — Index enables options, equity does not (prompt gating)**
from the design:

    For an index symbol on a non-FNO profile, the formatted system prompt
    enables/prioritizes options and marks spot-volume tools expected-N/A; for an
    equity on a non-FNO profile, the formatted prompt is unchanged from today
    (options not enabled).

    Validates: Requirements 2.1, 3.1, 4.1

They read ``graph.format_system_prompt`` and ``graph.INDEX_OPTIONS_ADDENDUM``
directly off the module, matching the import/setup pattern used by the other
prompt-content unit tests in this suite (``import graph`` after putting the
service dir on ``sys.path``). graph.py loads .env / builds LLM clients at import
time, but every existing prompt test imports it this way, so it is safe here.
"""

import os
import sys

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402


def _index_state():
    """A FIND run analyzing a spot index (NIFTY 50) in the INTRADAY workspace."""
    return {"mode": "FIND", "symbol": "NIFTY 50", "profile": "INTRADAY", "timeframe": "10m"}


def _equity_state():
    """A FIND run analyzing a liquid stock (RELIANCE) in the INTRADAY workspace."""
    return {"mode": "FIND", "symbol": "RELIANCE", "profile": "INTRADAY", "timeframe": "10m"}


# ── Requirement 2.1 / 3.1: index + INTRADAY enables + prioritizes options ─────
def test_index_intraday_prompt_contains_options_addendum():
    """Validates: Requirements 2.1, 3.1

    For an index symbol on a non-FNO profile, the formatted prompt must carry the
    index options addendum: the ``<index_options_context>`` marker, the
    options-enabled/primary language (enabling ``get_options_analytics``), and
    the spot-volume "expected-unavailable" language.
    """
    prompt = graph.format_system_prompt(_index_state())

    # The addendum block is present, identified by its marker tag.
    assert "<index_options_context>" in prompt
    assert "</index_options_context>" in prompt

    # Options is ENABLED + PRIMARY for the index (Requirement 2.1).
    assert "OPTIONS IS ENABLED AND PRIMARY HERE" in prompt
    assert "get_options_analytics" in prompt

    # Spot-volume tools are marked EXPECTED-unavailable, not evidence against the
    # setup (Requirement 3.1) — the "spot volume expected-N/A" language.
    assert "SPOT-VOLUME TOOLS ARE EXPECTED-UNAVAILABLE" in prompt
    assert "EXPECTED for the instrument" in prompt

    # The whole addendum constant is appended verbatim.
    assert graph.INDEX_OPTIONS_ADDENDUM in prompt


# ── Requirement 4.1: equity + INTRADAY does NOT enable options ────────────────
def test_equity_intraday_prompt_excludes_options_addendum():
    """Validates: Requirements 4.1

    For an equity symbol on a non-FNO profile, the formatted prompt must NOT
    contain the index options addendum — the equity path is unchanged from today
    (options stays off outside the F&O workspace).
    """
    prompt = graph.format_system_prompt(_equity_state())

    assert "<index_options_context>" not in prompt
    assert graph.INDEX_OPTIONS_ADDENDUM not in prompt


# ── Requirement 4.1: equity path is byte-identical to "no addendum" ───────────
def test_index_prompt_is_equity_prompt_plus_addendum():
    """Validates: Requirements 2.1, 4.1

    The only difference between the index prompt and the equity prompt (same
    mode / profile / timeframe) is the appended addendum, proving the equity path
    is left byte-identical to today.
    """
    index_prompt = graph.format_system_prompt(_index_state())
    equity_prompt = graph.format_system_prompt(_equity_state())

    assert index_prompt == equity_prompt + graph.INDEX_OPTIONS_ADDENDUM
