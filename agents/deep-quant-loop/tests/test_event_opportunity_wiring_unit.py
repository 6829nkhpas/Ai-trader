"""Unit tests for the Earnings/Event-Date Risk Gate adaptive-engine wiring (task 9.2).

Feature: earnings-event-risk-gate

Covers the ``opportunity.py`` integration of ``get_event_risk`` (Requirement 6.6):
  - ``get_event_risk`` is part of ``FULL_ORDER_OF_OPERATIONS_TOOLS`` (the event-date
    risk gate step of the documented full scan);
  - ``get_event_risk`` is part of the ``RESUME_TARGET`` Delta_Recheck plan so a
    resume that is about to confirm an entry re-consults scheduled-event proximity;
  - the ``RESUME_TARGET`` plan remains a NON-EMPTY, STRICT (proper) subset of
    ``FULL_ORDER_OF_OPERATIONS_TOOLS``.

These are plain example-based unit tests; no LLM / network is invoked.
"""

import os
import sys

# Make the service package importable (modules live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import opportunity  # noqa: E402


def test_get_event_risk_in_full_order_of_operations():
    """``get_event_risk`` is registered as the event-date risk gate step.

    Validates: Requirements 6.6
    """
    assert "get_event_risk" in opportunity.FULL_ORDER_OF_OPERATIONS_TOOLS


def test_get_event_risk_in_resume_target_delta_recheck_plan():
    """A ``target`` resume re-consults scheduled-event proximity.

    Validates: Requirements 6.6
    """
    plan = opportunity.delta_recheck_plan(opportunity.RESUME_TARGET)
    assert "get_event_risk" in plan


def test_resume_target_plan_is_non_empty_strict_subset():
    """The ``RESUME_TARGET`` plan stays a non-empty STRICT subset of the full scan.

    Validates: Requirements 6.6
    """
    full = list(opportunity.FULL_ORDER_OF_OPERATIONS_TOOLS)
    full_set = set(full)
    plan = opportunity.delta_recheck_plan(opportunity.RESUME_TARGET)
    plan_set = set(plan)

    # Non-empty.
    assert len(plan) >= 1
    # Every tool in the plan is part of the full scan.
    assert plan_set <= full_set
    # Strict subset: at least one tool in the full scan is not in the plan.
    assert plan_set != full_set
    assert full_set - plan_set, "at least one full-scan tool must be absent from the plan"
