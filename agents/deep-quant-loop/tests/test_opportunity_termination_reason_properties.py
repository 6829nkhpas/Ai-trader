"""Property-based test for the bounded-hunt termination reason (opportunity.py, task 3.4).

Feature: adaptive-opportunity-engine

This module implements design **Property 9: Termination reason is stated and
deterministic**:

    For any state in which a bound is met, ``termination_reason`` returns exactly
    one of ``'watch-cap-reached'`` or ``'session-budget-exhausted'`` under a
    deterministic precedence, and that reason is the one carried on the committed
    terminal decision.

Validates: Requirements 3.5.

``termination_reason(state, cfg, now)`` returns exactly one of
``'watch-cap-reached'`` | ``'session-budget-exhausted'`` | ``None`` under a fixed,
deterministic precedence: ``watch_cap_reached`` is checked FIRST, so when BOTH
bounds hold it returns ``'watch-cap-reached'``; it returns ``None`` when neither
bound holds.

Correctness is checked against an **independent reference oracle**
(``_expected_reason``) that re-derives the reason directly from the two bound
predicates ``watch_cap_reached`` / ``session_budget_exhausted`` under the
documented precedence, exactly as the task requires. The property asserts:

  * the result is always one of the three allowed values;
  * when a bound is met the result is exactly one of the two reason strings under
    the deterministic precedence (watch-cap-reached wins when both hold);
  * the result is ``None`` iff neither bound holds;
  * the reason is consistent with the predicates (``watch_cap_reached`` =>
    'watch-cap-reached'; else ``session_budget_exhausted`` =>
    'session-budget-exhausted'; else ``None``);
  * determinism — repeated calls with the same ``(state, cfg, now)`` return the
    identical reason.

The strategy fuzzes ``state`` (watch_cycles / session_turns / session_started_at,
each spanning valid ints, negatives, ``None``, and non-numeric garbage), the clock
``now`` (finite floats, ``None``, non-numeric), and a configuration whose caps and
budgets are drawn small so the bounds are hit frequently. The sys.path / import
pattern mirrors the sibling deep-quant-loop opportunity property tests.
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
    OpportunityConfig,
    session_budget_exhausted,
    termination_reason,
    watch_cap_reached,
)

_ALLOWED = frozenset({"watch-cap-reached", "session-budget-exhausted", None})


# ── Independent reference oracle ──────────────────────────────────────────────
# Derived from the two bound predicates under the documented deterministic
# precedence (watch_cap checked FIRST). This is the oracle the task prescribes:
# it re-derives the reason from watch_cap_reached / session_budget_exhausted
# rather than re-implementing termination_reason, so the property genuinely checks
# the precedence and the None-iff-neither behavior.
def _expected_reason(state, cfg, now):
    if watch_cap_reached(state, cfg):
        return "watch-cap-reached"
    if session_budget_exhausted(state, cfg, now):
        return "session-budget-exhausted"
    return None


# ── Generators over the documented state / clock / config input space ─────────

# A session counter: valid non-negative ints, negatives, None, and non-numeric
# garbage — termination_reason must be total over all of these.
_counter_strategy = st.one_of(
    st.integers(min_value=0, max_value=10),
    st.integers(min_value=-5, max_value=-1),
    st.none(),
    st.sampled_from(["", "3", None, float("nan"), float("inf"), True, False]),
    st.text(max_size=3),
)

# A wall-clock stamp / clock value: finite floats, None, and non-numeric garbage.
_clock_strategy = st.one_of(
    st.floats(min_value=0.0, max_value=1_000_000.0),
    st.floats(min_value=-1000.0, max_value=1000.0),
    st.none(),
    st.sampled_from([float("nan"), float("inf"), "now", True]),
)


@st.composite
def _state_strategy(draw):
    """A state dict spanning the documented (and malformed) input space."""
    st_dict = {}
    # Each key is independently present / absent so partial states are exercised.
    if draw(st.booleans()):
        st_dict["watch_cycles"] = draw(_counter_strategy)
    if draw(st.booleans()):
        st_dict["session_turns"] = draw(_counter_strategy)
    if draw(st.booleans()):
        st_dict["session_started_at"] = draw(_clock_strategy)
    return st_dict


# Small caps/budgets so the bounds are hit frequently across the fuzzed states.
_config_strategy = st.builds(
    OpportunityConfig,
    watch_cap=st.integers(min_value=1, max_value=5),
    session_max_turns=st.integers(min_value=1, max_value=5),
    session_max_wall_secs=st.floats(min_value=1.0, max_value=100.0),
    size_factor_a_plus=st.just(1.0),
    size_factor_b_continuation=st.just(0.6),
    size_factor_scalp=st.just(0.3),
    lower_tiers_enabled=st.just(True),
    heartbeat_enabled=st.just(False),
    heartbeat_cadence_secs=st.just(300.0),
    heartbeat_max=st.just(6),
    prune_keep_recent_turns=st.just(8),
    prune_max_messages=st.just(40),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 9 (task 3.4): Termination reason is stated and deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 9: For any state in which a bound is met, termination_reason returns exactly one of 'watch-cap-reached' or 'session-budget-exhausted' under a deterministic precedence (watch-cap-reached wins when both hold), returns None iff neither bound holds, is consistent with the watch_cap_reached / session_budget_exhausted predicates, and is deterministic across repeated calls with the same (state, cfg, now).
@settings(max_examples=300, deadline=None)
@given(state=_state_strategy(), cfg=_config_strategy, now=_clock_strategy)
def test_property_9_termination_reason_stated_and_deterministic(state, cfg, now):
    """Feature: adaptive-opportunity-engine, Property 9: Termination reason is
    stated and deterministic — ``termination_reason`` returns exactly one of the
    two reason strings under the deterministic precedence when a bound is met,
    returns None iff neither bound holds, is consistent with the bound predicates,
    and returns the identical reason on repeated calls with the same inputs.

    Validates: Requirements 3.5
    """
    reason = termination_reason(state, cfg, now)

    # ── Shape: always one of the three allowed values (totality). ─────────────
    assert reason in _ALLOWED

    cap = watch_cap_reached(state, cfg)
    budget = session_budget_exhausted(state, cfg, now)
    expected = _expected_reason(state, cfg, now)

    # ── Consistency with the bound predicates under the documented precedence.
    #    watch_cap_reached => 'watch-cap-reached' (wins when both hold);
    #    else session_budget_exhausted => 'session-budget-exhausted'; else None. ─
    assert reason == expected, (
        f"termination_reason returned {reason!r} but the precedence over "
        f"watch_cap_reached={cap}, session_budget_exhausted={budget} implies "
        f"{expected!r} for state={state!r}, now={now!r}"
    )

    # ── A bound met => exactly one of the two reason strings (precedence). ────
    if cap or budget:
        assert reason in ("watch-cap-reached", "session-budget-exhausted")
        if cap:
            # Deterministic precedence: watch-cap-reached wins even when both hold.
            assert reason == "watch-cap-reached"
        else:
            assert reason == "session-budget-exhausted"

    # ── None iff neither bound holds. ─────────────────────────────────────────
    assert (reason is None) == (not cap and not budget)

    # ── Determinism: repeated calls with the same inputs are identical. ───────
    assert termination_reason(state, cfg, now) == reason
    assert termination_reason(state, cfg, now) == reason
