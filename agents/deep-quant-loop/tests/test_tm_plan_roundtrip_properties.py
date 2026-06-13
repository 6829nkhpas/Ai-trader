"""Property-based test for plan persistence round-trip (trade_manager.py, task 2.3).

Feature: trade-management

This module implements design **Property 19: Plan persistence round-trip**:

    For any generated ``ManagementPlan``, ``plan_from_json(plan_to_json(plan))``
    reconstructs an *equal* ``ManagementPlan`` — action, entry, initial_stop,
    every leg's target/fraction, the breakeven price/r_multiple, the trailing
    atr_multiple/r_increment, and atr_14 are all preserved. The serialized form
    is additionally a ``str`` and is valid JSON.

Validates: Requirements 6.3.

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_config_default_fallback_properties.py`` and
``tests/test_tm_config_path_independent_properties.py``.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (trade_manager.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from trade_manager import (  # noqa: E402
    BreakevenTrigger,
    ManagementPlan,
    ScaleOutLeg,
    TrailingStop,
    plan_from_json,
    plan_to_json,
)


# ── Strategies producing valid ManagementPlans ────────────────────────────────
# Finite, well-behaved floats only (the round-trip is exercised on plans the
# validator / simulator would consider well-formed). ``allow_nan=False`` matters:
# JSON has no NaN/Infinity literal in strict mode and NaN != NaN would break the
# equality assertion anyway, so non-finite fields are deliberately excluded.
_finite = st.floats(
    min_value=-1e6,
    max_value=1e6,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)

# Leg fraction in (0.0, 1.0] — strictly positive, at most the whole position.
_fraction = st.floats(
    min_value=1e-6,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)

_leg = st.builds(ScaleOutLeg, target=_finite, fraction=_fraction)

# 1..N legs (the plan always carries at least one leg, Requirement 1.1).
_legs = st.lists(_leg, min_size=1, max_size=5).map(tuple)

# Breakeven expressed as EITHER a price OR an r_multiple (Requirement 1.4), or
# absent. Each variant exercises a distinct serialization branch.
_breakeven = st.one_of(
    st.none(),
    st.builds(BreakevenTrigger, price=_finite, r_multiple=st.none()),
    st.builds(
        BreakevenTrigger,
        price=st.none(),
        r_multiple=st.floats(
            min_value=1e-6, max_value=100.0, allow_nan=False, allow_infinity=False
        ),
    ),
)

# Trailing expressed as EITHER an atr_multiple OR an r_increment, or absent.
_trailing = st.one_of(
    st.none(),
    st.builds(
        TrailingStop,
        atr_multiple=st.floats(
            min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False
        ),
        r_increment=st.none(),
    ),
    st.builds(
        TrailingStop,
        atr_multiple=st.none(),
        r_increment=st.floats(
            min_value=1e-6, max_value=100.0, allow_nan=False, allow_infinity=False
        ),
    ),
)

_atr_14 = st.one_of(
    st.none(),
    st.floats(min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _management_plans(draw):
    """Build a valid, finite ``ManagementPlan`` exercising every optional field."""
    action = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(_finite)
    initial_stop = draw(_finite)
    legs = draw(_legs)
    breakeven = draw(_breakeven)
    trailing = draw(_trailing)
    atr_14 = draw(_atr_14)
    return ManagementPlan(
        action=action,
        entry=entry,
        initial_stop=initial_stop,
        legs=legs,
        breakeven=breakeven,
        trailing=trailing,
        atr_14=atr_14,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 19 (task 2.3): Plan persistence round-trip
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 19: Plan persistence round-trip
@settings(max_examples=200, deadline=None)
@given(plan=_management_plans())
def test_property_19_plan_persistence_round_trip(plan):
    """Feature: trade-management, Property 19: Plan persistence round-trip —
    for any generated ``ManagementPlan``, ``plan_from_json(plan_to_json(plan))``
    reconstructs an equal plan, and the serialized form is a ``str`` of valid
    JSON.

    Validates: Requirements 6.3
    """
    serialized = plan_to_json(plan)

    # Serialized form is a str and is valid JSON.
    assert isinstance(serialized, str)
    parsed = json.loads(serialized)  # raises if not valid JSON
    assert isinstance(parsed, dict)

    # Round-trip reconstructs an equal plan (frozen dataclasses give structural
    # equality across every field: action, entry, initial_stop, every leg's
    # target/fraction, breakeven price/r_multiple, trailing atr_multiple/
    # r_increment, and atr_14).
    reconstructed = plan_from_json(serialized)
    assert reconstructed == plan
