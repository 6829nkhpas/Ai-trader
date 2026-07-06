"""Property-based test for bounded heartbeat accounting (opportunity.py, task 5.3).

Feature: adaptive-opportunity-engine

This module implements design **Property 12: Heartbeat usage is bounded and counts
toward the budget**:

    For any state and configuration, ``account_heartbeat`` returns a
    ``heartbeat_count`` that NEVER exceeds ``heartbeat_max`` (even from a corrupt
    over-count state, which is clamped down), and an ACCEPTED heartbeat increments
    ``session_turns`` by exactly one (charging the Session_Budget) while a
    non-accepted heartbeat leaves the turn count unchanged. The ``heartbeat_max ==
    0`` case never accepts a heartbeat, so the Heartbeat_Monitor cannot run
    unbounded.

Validates: Requirements 5.2.

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
    DEFAULT_HEARTBEAT_CADENCE_SECS,
    DEFAULT_HEARTBEAT_ENABLED,
    DEFAULT_LOWER_TIERS_ENABLED,
    DEFAULT_PRUNE_KEEP_RECENT_TURNS,
    DEFAULT_PRUNE_MAX_MESSAGES,
    DEFAULT_SESSION_MAX_TURNS,
    DEFAULT_SESSION_MAX_WALL_SECS,
    DEFAULT_SIZE_FACTOR_A_PLUS,
    DEFAULT_SIZE_FACTOR_B_CONTINUATION,
    DEFAULT_SIZE_FACTOR_SCALP,
    DEFAULT_WATCH_CAP,
    OpportunityConfig,
    account_heartbeat,
)


# ── Config builder: fuzz only heartbeat_max, hold the rest at defaults ─────────
@st.composite
def configs(draw):
    """Build an ``OpportunityConfig`` fuzzing only the field ``account_heartbeat``
    consults — ``heartbeat_max`` (>= 0, including the disabling 0) — and holding
    every other field at its documented default.
    """
    return OpportunityConfig(
        watch_cap=DEFAULT_WATCH_CAP,
        session_max_turns=DEFAULT_SESSION_MAX_TURNS,
        session_max_wall_secs=DEFAULT_SESSION_MAX_WALL_SECS,
        size_factor_a_plus=DEFAULT_SIZE_FACTOR_A_PLUS,
        size_factor_b_continuation=DEFAULT_SIZE_FACTOR_B_CONTINUATION,
        size_factor_scalp=DEFAULT_SIZE_FACTOR_SCALP,
        lower_tiers_enabled=DEFAULT_LOWER_TIERS_ENABLED,
        heartbeat_enabled=DEFAULT_HEARTBEAT_ENABLED,
        heartbeat_cadence_secs=DEFAULT_HEARTBEAT_CADENCE_SECS,
        heartbeat_max=draw(st.integers(min_value=0, max_value=25)),
        prune_keep_recent_turns=DEFAULT_PRUNE_KEEP_RECENT_TURNS,
        prune_max_messages=DEFAULT_PRUNE_MAX_MESSAGES,
    )


# A heartbeat_count / session_turns value: valid ints (incl. corrupt over-count)
# plus the degraded forms the module documents as reading 0.
_DEGRADED = st.sampled_from([None, "abc", "", [], {}, float("nan"), True])
_counter = st.one_of(st.integers(min_value=0, max_value=50), _DEGRADED)


def _coerce(value) -> int:
    """Independent oracle mirroring the module's non-negative int coercion."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        return 0
    count = int(value)
    return count if count > 0 else 0


# ─────────────────────────────────────────────────────────────────────────────
# Property 12, facet 1 — the count NEVER exceeds heartbeat_max, for ANY input
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 12: account_heartbeat's returned heartbeat_count is always <= heartbeat_max, even from a corrupt over-count state.
@settings(max_examples=300, deadline=None)
@given(cfg=configs(), heartbeat_count=_counter, session_turns=_counter)
def test_property_12_heartbeat_count_never_exceeds_max(cfg, heartbeat_count, session_turns):
    """Feature: adaptive-opportunity-engine, Property 12 (bounded): for any state and
    configuration ``account_heartbeat().heartbeat_count <= heartbeat_max`` — the
    invariant holds even when the input state carries a corrupt over-count.

    Validates: Requirements 5.2
    """
    state = {"heartbeat_count": heartbeat_count, "session_turns": session_turns}
    result = account_heartbeat(state, cfg)
    assert result.heartbeat_count <= cfg.heartbeat_max
    # And the count is never negative.
    assert result.heartbeat_count >= 0


# ─────────────────────────────────────────────────────────────────────────────
# Property 12, facet 2 — an accepted heartbeat charges exactly one budget turn
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 12: an accepted heartbeat increments session_turns by exactly one and heartbeat_count by one; a rejected heartbeat changes neither.
@settings(max_examples=300, deadline=None)
@given(cfg=configs(), heartbeat_count=_counter, session_turns=_counter)
def test_property_12_accepted_heartbeat_charges_budget(cfg, heartbeat_count, session_turns):
    """Feature: adaptive-opportunity-engine, Property 12 (budget accounting): an
    ACCEPTED heartbeat increments both ``heartbeat_count`` and ``session_turns`` by
    exactly one; a REJECTED heartbeat (ceiling reached) leaves ``session_turns``
    unchanged and clamps ``heartbeat_count`` to the ceiling.

    Validates: Requirements 5.2
    """
    prior_hb = _coerce(heartbeat_count)
    prior_turns = _coerce(session_turns)
    state = {"heartbeat_count": heartbeat_count, "session_turns": session_turns}

    result = account_heartbeat(state, cfg)

    # Acceptance is exactly "within the ceiling" against the coerced prior count.
    expected_accepted = prior_hb < cfg.heartbeat_max
    assert result.accepted is expected_accepted

    if expected_accepted:
        assert result.heartbeat_count == prior_hb + 1
        assert result.session_turns == prior_turns + 1
    else:
        # Ceiling reached: no charge, count clamped down to the ceiling.
        assert result.session_turns == prior_turns
        assert result.heartbeat_count == min(prior_hb, cfg.heartbeat_max)


# ─────────────────────────────────────────────────────────────────────────────
# Property 12, facet 3 — heartbeat_max == 0 never accepts (monitor stays off)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 12: with heartbeat_max == 0 no heartbeat is ever accepted, so the monitor cannot run even when enabled.
@settings(max_examples=100, deadline=None)
@given(heartbeat_count=_counter, session_turns=_counter)
def test_property_12_zero_max_never_accepts(heartbeat_count, session_turns):
    """Feature: adaptive-opportunity-engine, Property 12 (zero ceiling): with
    ``heartbeat_max == 0`` a heartbeat is never accepted and the turn budget is
    never charged, no matter the input state.

    Validates: Requirements 5.2
    """
    cfg = OpportunityConfig(
        watch_cap=DEFAULT_WATCH_CAP,
        session_max_turns=DEFAULT_SESSION_MAX_TURNS,
        session_max_wall_secs=DEFAULT_SESSION_MAX_WALL_SECS,
        size_factor_a_plus=DEFAULT_SIZE_FACTOR_A_PLUS,
        size_factor_b_continuation=DEFAULT_SIZE_FACTOR_B_CONTINUATION,
        size_factor_scalp=DEFAULT_SIZE_FACTOR_SCALP,
        lower_tiers_enabled=DEFAULT_LOWER_TIERS_ENABLED,
        heartbeat_enabled=True,  # enabled but capped at 0 → still never fires
        heartbeat_cadence_secs=DEFAULT_HEARTBEAT_CADENCE_SECS,
        heartbeat_max=0,
        prune_keep_recent_turns=DEFAULT_PRUNE_KEEP_RECENT_TURNS,
        prune_max_messages=DEFAULT_PRUNE_MAX_MESSAGES,
    )
    state = {"heartbeat_count": heartbeat_count, "session_turns": session_turns}
    result = account_heartbeat(state, cfg)
    assert result.accepted is False
    assert result.heartbeat_count == 0
    assert result.session_turns == _coerce(session_turns)
