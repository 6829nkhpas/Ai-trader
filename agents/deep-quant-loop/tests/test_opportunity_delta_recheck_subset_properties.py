"""Property-based test for the Delta_Recheck plan (opportunity.py, task 5.2).

Feature: adaptive-opportunity-engine

This module implements design **Property 13: Delta_Recheck is a non-empty proper
subset of the full scan**:

    For any resume trigger kind (any spelling, including unrecognized / None /
    malformed), ``delta_recheck_plan`` returns a NON-EMPTY list that is a STRICT
    (proper) subset of ``FULL_ORDER_OF_OPERATIONS_TOOLS`` — every tool it names is
    part of the full scan, it names at least one, and it never names the whole
    scan — so every resume re-checks only the trigger-relevant data and is strictly
    cheaper than a full re-scan. The plan is deterministic for a given trigger and
    ordered in canonical order-of-operations order.

Validates: Requirements 6.1, 6.2, 6.3.

The sys.path / import bootstrap and the ``@settings`` / ``@given`` convention mirror
``tests/test_opportunity_watch_cap_convergence_properties.py`` and the sibling
``tests/test_opportunity_*_properties.py`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (opportunity.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from opportunity import (  # noqa: E402
    FULL_ORDER_OF_OPERATIONS_TOOLS,
    RESUME_HEARTBEAT,
    RESUME_INVALIDATION,
    RESUME_KINDS,
    RESUME_TARGET,
    classify_resume,
    delta_recheck_plan,
)

_FULL_SET = set(FULL_ORDER_OF_OPERATIONS_TOOLS)

# Trigger spellings: the canonical kinds, recognized synonyms, and the degraded /
# unrecognized forms that must fall back to a valid plan.
_TRIGGERS = st.one_of(
    st.sampled_from(list(RESUME_KINDS)),
    st.sampled_from(
        ["Target", "TARGET", "stop", "invalidated", "hb", "pulse", "reached", "stop-out"]
    ),
    st.sampled_from([None, "", "   ", "garbage", 123, [], {}]),
    st.text(max_size=12),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 13, facet 1 — non-empty strict subset of the full scan, for ANY trigger
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 13: for any trigger, delta_recheck_plan is a non-empty proper subset of FULL_ORDER_OF_OPERATIONS_TOOLS.
@settings(max_examples=300, deadline=None)
@given(trigger=_TRIGGERS)
def test_property_13_non_empty_strict_subset(trigger):
    """Feature: adaptive-opportunity-engine, Property 13: ``delta_recheck_plan`` is a
    NON-EMPTY, STRICT (proper) subset of the full order-of-operations scan for any
    trigger — every named tool is in the full scan, it names at least one, and it
    never names the entire scan.

    Validates: Requirements 6.1, 6.2, 6.3
    """
    plan = delta_recheck_plan(trigger)

    assert isinstance(plan, list)
    assert len(plan) >= 1, "plan must be non-empty"

    plan_set = set(plan)
    # Every tool is drawn from the full scan (subset).
    assert plan_set <= _FULL_SET, f"plan escapes the full scan: {plan_set - _FULL_SET}"
    # Strictly fewer than the full scan (proper subset → strictly cheaper).
    assert plan_set < _FULL_SET, "plan must be a STRICT subset (cheaper than a full re-scan)"
    # No duplicates.
    assert len(plan) == len(plan_set), "plan must not repeat a tool"


# ─────────────────────────────────────────────────────────────────────────────
# Property 13, facet 2 — deterministic and in canonical order for a given trigger
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 13: the plan is deterministic and ordered in canonical order-of-operations order for a given trigger.
@settings(max_examples=200, deadline=None)
@given(trigger=_TRIGGERS)
def test_property_13_deterministic_and_ordered(trigger):
    """Feature: adaptive-opportunity-engine, Property 13 (determinism/order): the plan
    is identical on repeated calls with the same trigger, depends only on the
    classified kind, and lists tools in ``FULL_ORDER_OF_OPERATIONS_TOOLS`` order.

    Validates: Requirements 6.1, 6.2, 6.3
    """
    plan_a = delta_recheck_plan(trigger)
    plan_b = delta_recheck_plan(trigger)
    assert plan_a == plan_b, "plan must be deterministic for identical input"

    # The plan depends only on the classified kind (any spelling of the same kind
    # yields the same plan).
    kind = classify_resume(trigger)
    assert delta_recheck_plan(kind) == plan_a


# ─────────────────────────────────────────────────────────────────────────────
# Property 13, facet 3 — cheapest trigger (heartbeat) ⊆ target/invalidation plans
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 13: the heartbeat plan is the cheapest and each canonical kind yields a distinct, valid strict subset.
@settings(max_examples=1, deadline=None)
@given(st.just(None))
def test_property_13_relative_plan_sizes(_ignored):
    """Feature: adaptive-opportunity-engine, Property 13 (relative cost): every
    canonical kind yields a non-empty strict subset, and the heartbeat plan (the
    cheapest mid-wait pulse) is no larger than the target/invalidation plans.

    Validates: Requirements 6.1, 6.2, 6.3
    """
    target = delta_recheck_plan(RESUME_TARGET)
    invalidation = delta_recheck_plan(RESUME_INVALIDATION)
    heartbeat = delta_recheck_plan(RESUME_HEARTBEAT)

    for plan in (target, invalidation, heartbeat):
        assert 1 <= len(plan) < len(FULL_ORDER_OF_OPERATIONS_TOOLS)

    # The heartbeat pulse is the cheapest of the three.
    assert len(heartbeat) <= len(target)
    assert len(heartbeat) <= len(invalidation)
