"""Focused unit tests for the enriched heartbeat Delta_Recheck plan (opportunity.py).

Feature: agent-loop-responsiveness (task 4.2)

These tests pin design **Property 2: Heartbeat plan is enriched yet a bounded strict
subset** as it applies to the ``RESUME_HEARTBEAT`` Delta_Recheck plan produced by
``opportunity.delta_recheck_plan``:

    * the heartbeat plan carries the enriched situational tool set — beyond fresh
      candles and consensus it also re-checks the market regime and the key
      support/resistance levels, so a pulse refreshes the situation, not just price;
    * for an INDEX symbol it additionally re-consults options positioning
      (``get_options_analytics``) as the symbol-appropriate primary confirmation;
    * for a non-index (equity) symbol it does NOT pull options positioning; and
    * the plan is always a NON-EMPTY, STRICT subset of the full order-of-operations
      scan (a heartbeat is never a full re-scan).

Validates: Requirements 2.1, 2.2, 2.3.

The sys.path / import bootstrap mirrors the sibling
``tests/test_opportunity_delta_recheck_subset_properties.py`` module.
"""

import os
import sys

# Make the service package importable (opportunity.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from opportunity import (  # noqa: E402
    FULL_ORDER_OF_OPERATIONS_TOOLS,
    RESUME_HEARTBEAT,
    delta_recheck_plan,
)

_FULL_SET = set(FULL_ORDER_OF_OPERATIONS_TOOLS)

# The enriched situational tool set every heartbeat pulse must re-check (R2.1):
# fresh candles + indicator consensus + market regime + key support/resistance.
_ENRICHED_HEARTBEAT_TOOLS = {
    "get_candles",
    "get_consensus_report",
    "get_market_regime",
    "get_support_resistance",
}


def test_heartbeat_plan_contains_enriched_tool_set():
    """R2.1: the heartbeat plan refreshes the situation, not just price — it names at
    least fresh candles, consensus, market regime, and key support/resistance."""
    plan = delta_recheck_plan(RESUME_HEARTBEAT)

    assert set(plan) >= _ENRICHED_HEARTBEAT_TOOLS, (
        "heartbeat plan is missing enriched situational tools: "
        f"{_ENRICHED_HEARTBEAT_TOOLS - set(plan)}"
    )


def test_heartbeat_plan_for_index_includes_options_analytics():
    """R2.2: for an INDEX symbol the heartbeat plan additionally re-consults options
    positioning (the symbol-appropriate primary confirmation)."""
    plan = delta_recheck_plan(RESUME_HEARTBEAT, symbol_class="index")

    assert "get_options_analytics" in plan, (
        "index heartbeat plan must append get_options_analytics"
    )
    # The enriched base set is still present alongside the options confirmation.
    assert set(plan) >= _ENRICHED_HEARTBEAT_TOOLS


def test_heartbeat_plan_for_equity_excludes_options_analytics():
    """R2.2 (negative): for a non-index (equity) symbol the heartbeat plan does NOT
    pull options positioning, so equity behavior is unchanged."""
    plan = delta_recheck_plan(RESUME_HEARTBEAT, symbol_class="equity")

    assert "get_options_analytics" not in plan, (
        "equity heartbeat plan must not include get_options_analytics"
    )
    # The default (no symbol_class) plan is likewise options-free.
    assert "get_options_analytics" not in delta_recheck_plan(RESUME_HEARTBEAT)


def test_heartbeat_plan_is_non_empty_strict_subset_of_full_scan():
    """R2.3: the heartbeat plan stays a NON-EMPTY, STRICT subset of the full
    order-of-operations scan — it is never a full re-scan, for equity and index."""
    for symbol_class in (None, "equity", "index"):
        plan = delta_recheck_plan(RESUME_HEARTBEAT, symbol_class=symbol_class)
        plan_set = set(plan)

        assert len(plan) >= 1, f"heartbeat plan must be non-empty ({symbol_class})"
        assert len(plan) == len(plan_set), f"heartbeat plan must not repeat a tool ({symbol_class})"
        assert plan_set <= _FULL_SET, (
            f"heartbeat plan escapes the full scan ({symbol_class}): {plan_set - _FULL_SET}"
        )
        assert plan_set < _FULL_SET, (
            f"heartbeat plan must be a STRICT subset, never the full re-scan ({symbol_class})"
        )
