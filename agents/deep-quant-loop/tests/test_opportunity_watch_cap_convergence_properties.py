"""Property-based test for the Watch_Cap predicate and bounded termination
convergence (opportunity.py, task 3.2).

Feature: adaptive-opportunity-engine

This module implements design **Property 7: Watch_Cap predicate and bounded
termination convergence**:

    For any state and configuration, ``watch_cap_reached`` is true if and only if
    ``watch_cycles >= watch_cap``, each invalidation increments ``watch_cycles`` (a
    non-decreasing counter), and for any monotonically increasing sequence of
    turns / watch cycles / elapsed time there is a finite step bound after which
    ``termination_reason`` becomes non-``None`` — no state can keep both the cap and
    budget predicates false forever.

Validates: Requirements 3.1, 3.3, 4.4.

The test asserts three facets over the pure ``opportunity.py`` bounded-hunt core:

  * **Predicate (R3.1):** ``watch_cap_reached(state, cfg)`` is exactly
    ``watch_cycles >= watch_cap`` across the full integer space, including the
    boundary ``watch_cycles == watch_cap`` and the degraded (missing / ``None`` /
    non-numeric) counter which the module reads as 0.
  * **Monotonicity (R4.4):** because each watch registration AND each invalidation
    increments ``watch_cycles``, the predicate is monotone non-decreasing in
    ``watch_cycles`` — once true it stays true as the counter grows, so repeated
    invalidations never "un-trip" the cap.
  * **Convergence (R3.3):** simulating a session as a monotonically increasing
    sequence of watch cycles / model turns / wall-clock time, ``termination_reason``
    becomes non-``None`` within a finite, computed step bound — no monotone run can
    keep both the cap and budget predicates false forever.

The sys.path / import bootstrap and the ``@settings`` / ``@given`` convention mirror
``tests/test_opportunity_config_resolution_properties.py`` and the sibling
``tests/test_opportunity_*_properties.py`` modules.
"""

import math
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
    DEFAULT_HEARTBEAT_MAX,
    DEFAULT_LOWER_TIERS_ENABLED,
    DEFAULT_PRUNE_KEEP_RECENT_TURNS,
    DEFAULT_PRUNE_MAX_MESSAGES,
    DEFAULT_SIZE_FACTOR_A_PLUS,
    DEFAULT_SIZE_FACTOR_B_CONTINUATION,
    DEFAULT_SIZE_FACTOR_SCALP,
    OpportunityConfig,
    session_budget_exhausted,
    termination_reason,
    watch_cap_reached,
)


# ── Config builder: fuzz the bound-carrying fields, hold the rest at defaults ──
@st.composite
def configs(draw):
    """Build an ``OpportunityConfig`` with fuzzed, in-range bound fields.

    Only the fields the bounded-hunt predicates consult — ``watch_cap`` (>= 1),
    ``session_max_turns`` (>= 1), ``session_max_wall_secs`` (> 0) — are fuzzed; the
    remaining fields are held at their documented defaults because they do not
    influence ``watch_cap_reached`` / ``session_budget_exhausted`` /
    ``termination_reason``.
    """
    return OpportunityConfig(
        watch_cap=draw(st.integers(min_value=1, max_value=25)),
        session_max_turns=draw(st.integers(min_value=1, max_value=60)),
        session_max_wall_secs=draw(
            st.floats(min_value=1.0, max_value=7200.0, allow_nan=False, allow_infinity=False)
        ),
        size_factor_a_plus=DEFAULT_SIZE_FACTOR_A_PLUS,
        size_factor_b_continuation=DEFAULT_SIZE_FACTOR_B_CONTINUATION,
        size_factor_scalp=DEFAULT_SIZE_FACTOR_SCALP,
        lower_tiers_enabled=DEFAULT_LOWER_TIERS_ENABLED,
        heartbeat_enabled=DEFAULT_HEARTBEAT_ENABLED,
        heartbeat_cadence_secs=DEFAULT_HEARTBEAT_CADENCE_SECS,
        heartbeat_max=DEFAULT_HEARTBEAT_MAX,
        prune_keep_recent_turns=DEFAULT_PRUNE_KEEP_RECENT_TURNS,
        prune_max_messages=DEFAULT_PRUNE_MAX_MESSAGES,
    )


# A watch_cycles value: valid ints (incl. boundary neighbourhood) plus the degraded
# forms the module documents as reading 0 (missing key, None, non-numeric).
_DEGRADED_COUNTERS = st.sampled_from([None, "abc", "", [], {}, float("nan")])
_watch_cycles = st.one_of(st.integers(min_value=0, max_value=50), _DEGRADED_COUNTERS)


# ─────────────────────────────────────────────────────────────────────────────
# Property 7 (task 3.2), facet 1 — Watch_Cap predicate is exactly the threshold
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 7: watch_cap_reached is true iff watch_cycles >= watch_cap.
@settings(max_examples=200, deadline=None)
@given(cfg=configs(), watch_cycles=_watch_cycles)
def test_property_7_watch_cap_reached_iff_threshold(cfg, watch_cycles):
    """Feature: adaptive-opportunity-engine, Property 7 (predicate): for any state
    and configuration, ``watch_cap_reached`` is true iff ``watch_cycles >=
    watch_cap`` — including the boundary and the degraded counter read as 0.

    Validates: Requirements 3.1, 3.3, 4.4
    """
    state = {"watch_cycles": watch_cycles}

    # Independent oracle: the documented coercion reads a non-int counter as 0.
    effective = watch_cycles if isinstance(watch_cycles, int) and not isinstance(
        watch_cycles, bool
    ) else 0
    expected = effective >= cfg.watch_cap

    assert watch_cap_reached(state, cfg) is expected


# ─────────────────────────────────────────────────────────────────────────────
# Property 7, facet 2 — the predicate is monotone non-decreasing in watch_cycles
# (each invalidation increments the counter; once tripped it stays tripped)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 7: once watch_cap_reached is true it stays true as watch_cycles grows (monotone non-decreasing counter).
@settings(max_examples=200, deadline=None)
@given(
    cfg=configs(),
    watch_cycles=st.integers(min_value=0, max_value=50),
    increment=st.integers(min_value=0, max_value=50),
)
def test_property_7_watch_cap_reached_monotonic(cfg, watch_cycles, increment):
    """Feature: adaptive-opportunity-engine, Property 7 (monotonicity): because each
    invalidation increments ``watch_cycles``, ``watch_cap_reached`` is monotone
    non-decreasing — if it is true at ``watch_cycles`` it remains true at any
    ``watch_cycles + increment`` (increment >= 0), so repeated invalidations never
    un-trip the cap.

    Validates: Requirements 3.1, 3.3, 4.4
    """
    before = watch_cap_reached({"watch_cycles": watch_cycles}, cfg)
    after = watch_cap_reached({"watch_cycles": watch_cycles + increment}, cfg)

    # Monotone non-decreasing: True implies still True after a non-negative step.
    if before:
        assert after
    # And the higher count is reached exactly when it meets the cap.
    assert after is ((watch_cycles + increment) >= cfg.watch_cap)


# ─────────────────────────────────────────────────────────────────────────────
# Property 7, facet 3 — bounded termination convergence
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 7: for any monotonically increasing sequence of watch cycles / turns / elapsed time, termination_reason becomes non-None within a finite step bound (no state keeps both bounds false forever).
@settings(max_examples=200, deadline=None)
@given(
    cfg=configs(),
    watch_step=st.integers(min_value=1, max_value=5),
    turn_step=st.integers(min_value=1, max_value=5),
    time_step=st.floats(min_value=0.5, max_value=120.0, allow_nan=False, allow_infinity=False),
)
def test_property_7_bounded_termination_convergence(cfg, watch_step, turn_step, time_step):
    """Feature: adaptive-opportunity-engine, Property 7 (convergence): simulate a
    session whose watch cycles, model turns, and elapsed wall-clock all increase
    monotonically each step; ``termination_reason`` becomes non-``None`` within a
    finite, computed step bound — no monotone run can keep both the cap and budget
    predicates false forever.

    Validates: Requirements 3.1, 3.3, 4.4
    """
    started_at = 0.0

    # Every channel advances by a positive amount each step, so at step k the state
    # is (k*watch_step, k*turn_step, now=k*time_step). Each channel independently
    # crosses its finite threshold, so termination is guaranteed by the EARLIEST of:
    steps_to_cap = math.ceil(cfg.watch_cap / watch_step)
    steps_to_turns = math.ceil(cfg.session_max_turns / turn_step)
    steps_to_wall = math.ceil(cfg.session_max_wall_secs / time_step)
    guaranteed_bound = min(steps_to_cap, steps_to_turns, steps_to_wall)

    # Before any progress the session must NOT be forced to terminate (all three
    # counters at 0, below every positive threshold).
    zero_state = {"watch_cycles": 0, "session_turns": 0, "session_started_at": started_at}
    assert termination_reason(zero_state, cfg, started_at) is None

    reason_step = None
    for k in range(0, guaranteed_bound + 1):
        state = {
            "watch_cycles": k * watch_step,
            "session_turns": k * turn_step,
            "session_started_at": started_at,
        }
        now = started_at + k * time_step
        reason = termination_reason(state, cfg, now)
        if reason is not None:
            reason_step = k
            break

    # Convergence: a terminal reason was reached within the finite bound.
    assert reason_step is not None, (
        f"termination_reason stayed None through the guaranteed bound {guaranteed_bound}"
    )
    assert reason_step <= guaranteed_bound

    # The reason is one of the two stated, deterministic values.
    final_state = {
        "watch_cycles": reason_step * watch_step,
        "session_turns": reason_step * turn_step,
        "session_started_at": started_at,
    }
    final_now = started_at + reason_step * time_step
    final_reason = termination_reason(final_state, cfg, final_now)
    assert final_reason in ("watch-cap-reached", "session-budget-exhausted")

    # Once terminal, it STAYS terminal for the rest of the monotone run (no state
    # can reopen the loop once a bound is crossed).
    for k in range(reason_step, guaranteed_bound + 1):
        state = {
            "watch_cycles": k * watch_step,
            "session_turns": k * turn_step,
            "session_started_at": started_at,
        }
        now = started_at + k * time_step
        assert termination_reason(state, cfg, now) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Property 7, facet 3 (isolated channels) — each bound alone forces convergence,
# so "no state keeps BOTH predicates false forever" holds even when only one
# counter advances.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 7: a monotone increase in watch_cycles alone converges via the Watch_Cap, and a monotone increase in turns/time alone converges via the Session_Budget.
@settings(max_examples=200, deadline=None)
@given(cfg=configs())
def test_property_7_single_channel_convergence(cfg):
    """Feature: adaptive-opportunity-engine, Property 7 (convergence, isolated
    channels): a monotone increase in watch cycles ALONE converges via the
    Watch_Cap, and a monotone increase in model turns ALONE (and wall-clock time
    ALONE) converges via the Session_Budget — so neither bound can be kept false
    forever by advancing only one counter.

    Validates: Requirements 3.1, 3.3, 4.4
    """
    started_at = 100.0

    # (a) Watch cycles alone → 'watch-cap-reached' by exactly watch_cap steps.
    converged = False
    for wc in range(0, cfg.watch_cap + 1):
        state = {"watch_cycles": wc, "session_turns": 0, "session_started_at": started_at}
        reason = termination_reason(state, cfg, started_at)  # now == started_at → no wall fire
        if reason is not None:
            assert reason == "watch-cap-reached"
            converged = True
            break
    assert converged, "watch cycles increasing alone never reached the Watch_Cap"

    # (b) Model turns alone → 'session-budget-exhausted' by exactly session_max_turns
    #     steps (watch_cycles held at 0, clock held so the wall bound cannot fire).
    converged = False
    for turns in range(0, cfg.session_max_turns + 1):
        state = {"watch_cycles": 0, "session_turns": turns, "session_started_at": started_at}
        if session_budget_exhausted(state, cfg, started_at):
            assert termination_reason(state, cfg, started_at) == "session-budget-exhausted"
            converged = True
            break
    assert converged, "model turns increasing alone never exhausted the Session_Budget"

    # (c) Wall-clock time alone → 'session-budget-exhausted' within a finite number
    #     of positive time steps (watch_cycles and turns held below their bounds).
    time_step = 30.0
    steps_to_wall = math.ceil(cfg.session_max_wall_secs / time_step)
    converged = False
    for k in range(0, steps_to_wall + 1):
        state = {"watch_cycles": 0, "session_turns": 0, "session_started_at": started_at}
        now = started_at + k * time_step
        if session_budget_exhausted(state, cfg, now):
            assert termination_reason(state, cfg, now) == "session-budget-exhausted"
            converged = True
            break
    assert converged, "wall-clock time increasing alone never exhausted the Session_Budget"
