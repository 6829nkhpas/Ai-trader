"""Property-based test for the Session_Budget predicate (opportunity.py, task 3.3).

Feature: adaptive-opportunity-engine

This module implements design **Property 8: Session_Budget predicate**:

    For any state, configuration, and clock value ``now``,
    ``session_budget_exhausted`` is true if and only if
    ``session_turns >= session_max_turns`` OR ``(now - session_started_at) >=
    session_max_wall_secs``.

Validates: Requirements 3.2.

``session_budget_exhausted(state, cfg, now)`` returns ``True`` iff the model-turn
budget is reached (``session_turns >= session_max_turns``) OR the wall-clock
budget has elapsed (``(now - session_started_at) >= session_max_wall_secs``). The
clock ``now`` is INJECTED by the caller — no clock is read inside the pure
function. It is total: a missing / ``None`` / non-numeric ``session_turns`` reads
0; a missing / ``None`` / non-numeric ``session_started_at`` (or a non-numeric
``now``) means the wall-clock condition cannot fire, but the turn condition is
still evaluated.

Correctness is checked against an **independent reference oracle**
(``_expected_exhausted``) that re-derives the biconditional directly from the raw
state / config / clock — re-implementing the documented coercion semantics rather
than calling the module under test — so the property is a genuine check rather
than a tautology. The property asserts:

  * the result is always a plain ``bool`` (totality, never raises);
  * the result equals the independently derived oracle for the whole fuzzed input
    space (the true-iff biconditional, R3.2);
  * the turn condition alone forces ``True`` whenever ``turns >=
    session_max_turns`` regardless of the clock;
  * the wall-clock condition cannot fire when the stamp or ``now`` is non-numeric;
  * determinism — repeated calls with the same ``(state, cfg, now)`` agree.

The strategy fuzzes ``state`` (``session_turns`` / ``session_started_at`` spanning
valid ints/floats, negatives, ``None``, booleans, and non-numeric garbage), the
clock ``now`` (finite floats, ``None``, NaN/inf, non-numeric), and a configuration
whose turn budget and wall-clock budget are drawn small so both bounds are hit
frequently. The sys.path / import pattern mirrors the sibling deep-quant-loop
opportunity property tests.
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
)


# ── Independent reference oracle ──────────────────────────────────────────────
# Re-derives the documented biconditional (R3.2) WITHOUT calling the module under
# test. It re-implements the two coercion rules the predicate documents:
#   * session_turns: a missing / None / bool / non-numeric / non-finite / negative
#     value reads 0; a finite numeric value is floored toward zero and clamped
#     non-negative (mirrors opportunity._coerce_count).
#   * session_started_at / now: only a real, finite int/float (bool excluded)
#     counts as a usable clock value (mirrors opportunity._is_finite_number); if
#     either is unusable the wall-clock condition cannot fire.
# so the property genuinely checks the true-iff behavior rather than restating it.


def _oracle_is_finite_number(value):
    """A real, finite int/float — a bool is NOT a level/clock value."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _oracle_coerce_turns(value):
    """Coerce session_turns to a non-negative int; missing/None/malformed -> 0."""
    if not _oracle_is_finite_number(value):
        return 0
    count = int(value)
    return count if count > 0 else 0


def _expected_exhausted(state, cfg, now):
    """Independently derived expected value of session_budget_exhausted (R3.2)."""
    st_dict = state if isinstance(state, dict) else {}

    turns = _oracle_coerce_turns(st_dict.get("session_turns"))
    if turns >= cfg.session_max_turns:
        return True

    started_at = st_dict.get("session_started_at")
    # The wall-clock condition can only fire when BOTH the stamp and now are
    # usable finite numbers.
    if not _oracle_is_finite_number(started_at) or not _oracle_is_finite_number(now):
        return False
    return (float(now) - float(started_at)) >= cfg.session_max_wall_secs


# ── Generators over the documented state / clock / config input space ─────────

# A session-turn counter: valid non-negative ints, negatives, None, booleans, and
# non-numeric garbage — the predicate must be total over all of these.
_turns_strategy = st.one_of(
    st.integers(min_value=0, max_value=12),
    st.integers(min_value=-5, max_value=-1),
    st.none(),
    st.sampled_from(["", "3", float("nan"), float("inf"), True, False, 2.9, -0.5]),
    st.text(max_size=3),
)

# A wall-clock stamp / clock value: finite floats (incl. negatives), None,
# NaN/inf, booleans, and non-numeric garbage.
_clock_strategy = st.one_of(
    st.floats(min_value=0.0, max_value=1_000_000.0),
    st.floats(min_value=-1000.0, max_value=1000.0),
    st.none(),
    st.sampled_from([float("nan"), float("inf"), float("-inf"), "now", True, False]),
)


@st.composite
def _state_strategy(draw):
    """A state dict spanning the documented (and malformed / partial) input space."""
    st_dict = {}
    # Each key is independently present / absent so partial states are exercised.
    if draw(st.booleans()):
        st_dict["session_turns"] = draw(_turns_strategy)
    if draw(st.booleans()):
        st_dict["session_started_at"] = draw(_clock_strategy)
    return st_dict


# Small budgets so both the turn bound and the wall-clock bound are hit frequently
# across the fuzzed states.
_config_strategy = st.builds(
    OpportunityConfig,
    watch_cap=st.integers(min_value=1, max_value=5),
    session_max_turns=st.integers(min_value=1, max_value=6),
    session_max_wall_secs=st.floats(min_value=1.0, max_value=500.0),
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
# Property 8 (task 3.3): Session_Budget predicate
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 8: For any state, configuration, and clock value now, session_budget_exhausted is true if and only if session_turns >= session_max_turns OR (session_started_at is a finite number AND now is a finite number AND (now - session_started_at) >= session_max_wall_secs), is always a plain bool (total, never raises), and is deterministic across repeated calls with the same (state, cfg, now).
@settings(max_examples=400, deadline=None)
@given(state=_state_strategy(), cfg=_config_strategy, now=_clock_strategy)
def test_property_8_session_budget_predicate(state, cfg, now):
    """Feature: adaptive-opportunity-engine, Property 8: Session_Budget predicate —
    ``session_budget_exhausted`` is True iff the turn budget is reached OR the
    wall-clock budget has elapsed (with a finite stamp and a finite injected
    clock), is always a plain bool, and is deterministic.

    Validates: Requirements 3.2
    """
    result = session_budget_exhausted(state, cfg, now)

    # ── Shape: always a plain bool (totality, never raises). ──────────────────
    assert isinstance(result, bool)

    # ── The true-iff biconditional against the independent oracle (R3.2). ─────
    expected = _expected_exhausted(state, cfg, now)
    assert result == expected, (
        f"session_budget_exhausted returned {result!r} but the biconditional "
        f"implies {expected!r} for state={state!r}, cfg.session_max_turns="
        f"{cfg.session_max_turns!r}, cfg.session_max_wall_secs="
        f"{cfg.session_max_wall_secs!r}, now={now!r}"
    )

    # ── Turn condition alone forces True regardless of the clock. ─────────────
    turns = _oracle_coerce_turns(state.get("session_turns"))
    if turns >= cfg.session_max_turns:
        assert result is True

    # ── The wall-clock condition cannot fire with an unusable stamp/clock. ────
    started_at = state.get("session_started_at")
    clock_usable = _oracle_is_finite_number(started_at) and _oracle_is_finite_number(now)
    if not clock_usable and turns < cfg.session_max_turns:
        assert result is False

    # ── Determinism: repeated calls with the same inputs agree. ───────────────
    assert session_budget_exhausted(state, cfg, now) == result
    assert session_budget_exhausted(state, cfg, now) == result
