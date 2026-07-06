"""Property-based test for bounded debate termination (graph.route_debate, task 8.5).

Feature: multi-agent-debate

This module implements design **Property 16: The debate always terminates within
its turn budget**:

    The bull -> bear -> (additional rounds) -> judge sequencing is bounded by
    ``debate_round < rounds`` AND ``debate_turns < max_turns``, so the debate can
    never loop forever — ``route_debate`` always routes to ``"judge"`` within a
    bounded number of bull/bear rounds for ANY state and configuration.

Validates: Requirements 2.4, 2.5, 6.2.

``route_debate(state)`` (graph.py) returns ``"bull"`` (loop another round) ONLY
while ``debate_round < cfg.rounds`` AND ``debate_turns < cfg.max_turns``; it
otherwise routes to ``"judge"``. The configuration is resolved from the
environment via ``resolve_debate_config`` with ``rounds`` clamped to
``[1, MAX_ROUNDS]`` and ``max_turns`` bounded to ``[derived, MAX_TURNS_CAP]``.

The test drives the real ``route_debate`` over a faithful simulation of the
bull -> bear -> route loop. Each iteration models one round: the Bull turn
derives its 1-based round index from the bounded turn counter exactly as
``bull_node`` does (``round = debate_turns // TURNS_PER_ROUND + 1``) and bumps
``debate_turns``; the Bear turn bumps ``debate_turns`` again; then
``route_debate`` decides whether to loop or hand off to the Judge. We assert:

  * The loop ALWAYS terminates (routes to ``"judge"``) within a bounded number of
    iterations — never hitting the safety cap (Requirements 2.4, 2.5, 6.2).
  * ``route_debate`` NEVER returns ``"bull"`` once ``debate_turns >= max_turns``
    — the hard turn-budget bound that guarantees termination (Requirement 6.2).

``graph.resolve_debate_config`` is monkeypatched to return a stub ``DebateConfig``
built from hypothesis-chosen ``rounds`` / ``max_turns`` so the env-driven config
resolution is replaced by the property's chosen bounds. The patch is always
restored in a ``finally`` block. The sys.path / import pattern mirrors the
sibling ``test_*`` modules in this directory.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the agent package importable (graph.py / debate.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import route_debate  # noqa: E402
from debate import (  # noqa: E402
    DebateConfig,
    MAX_ROUNDS,
    TURNS_PER_ROUND,
    JUDGE_TURNS,
    MAX_TURNS_CAP,
)


def _make_config(rounds: int, max_turns: int) -> DebateConfig:
    """Build a stub DebateConfig with the chosen round/turn bounds.

    The model fields are irrelevant to ``route_debate`` (it reads only ``rounds``
    and ``max_turns``), so they are filled with placeholders.
    """
    return DebateConfig(
        rounds=rounds,
        max_turns=max_turns,
        judge_max_tool_calls=0,
        bull_model="stub-model",
        bear_model="stub-model",
        judge_model="stub-model",
    )


@st.composite
def _bounds(draw):
    """A (rounds, max_turns) pair honouring resolve_debate_config invariants.

    ``rounds`` in ``[1, MAX_ROUNDS]``; ``max_turns`` in ``[derived, MAX_TURNS_CAP]``
    where ``derived = rounds * TURNS_PER_ROUND + JUDGE_TURNS`` is the smallest
    budget that can run the configured rounds.
    """
    rounds = draw(st.integers(min_value=1, max_value=MAX_ROUNDS))
    derived = rounds * TURNS_PER_ROUND + JUDGE_TURNS
    max_turns = draw(st.integers(min_value=derived, max_value=MAX_TURNS_CAP))
    return rounds, max_turns


# ─────────────────────────────────────────────────────────────────────────────
# Property 16: The debate always terminates within its turn budget
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 16: The debate always terminates within its turn budget
@settings(max_examples=100, deadline=None)
@given(bounds=_bounds())
def test_property_16_debate_loop_always_terminates(bounds):
    """Validates: Requirements 2.4, 2.5, 6.2

    Driving the real ``route_debate`` over a faithful bull -> bear -> route loop,
    the debate always reaches the Judge within a bounded number of iterations and
    never loops forever, for any configured rounds / max_turns.
    """
    rounds, max_turns = bounds
    cfg = _make_config(rounds, max_turns)

    original = graph.resolve_debate_config
    graph.resolve_debate_config = lambda *a, **k: cfg
    try:
        # Simulate the bull -> bear -> route_debate loop as a state machine,
        # starting exactly as a fresh debate does: round 1, zero turns.
        debate_turns = 0
        # Safety cap: a generous upper bound on iterations. If the loop were
        # unbounded it would blow past this; reaching the Judge before it proves
        # termination. max_turns is the hard turn budget, so no more than
        # ~max_turns rounds can ever run.
        iteration_cap = max_turns + JUDGE_TURNS + 2

        terminated = False
        for iteration in range(iteration_cap):
            # ── Bull turn: derive the 1-based round index from the bounded turn
            # counter exactly as bull_node does, then bump the turn counter. ───
            current_round = (debate_turns // TURNS_PER_ROUND) + 1
            debate_turns += 1
            # ── Bear turn: bump the turn counter again. ───────────────────────
            debate_turns += 1

            state = {"debate_round": current_round, "debate_turns": debate_turns}
            route = route_debate(state)

            # route_debate is total: it only ever routes to a debate role or the
            # Judge.
            assert route in ("bull", "judge"), f"unexpected route {route!r}"

            # Hard bound (R6.2): once the turn budget is spent it MUST hand off to
            # the Judge — never loop back to the Bull.
            if debate_turns >= max_turns:
                assert route == "judge", (
                    f"route_debate looped to bull with debate_turns={debate_turns} "
                    f">= max_turns={max_turns} (rounds={rounds})"
                )

            if route == "judge":
                terminated = True
                break

        assert terminated, (
            f"debate did not terminate within {iteration_cap} iterations "
            f"(rounds={rounds}, max_turns={max_turns}) — possible infinite loop"
        )
    finally:
        graph.resolve_debate_config = original


# Feature: multi-agent-debate, Property 16: The debate always terminates within its turn budget
@settings(max_examples=100, deadline=None)
@given(
    bounds=_bounds(),
    extra_turns=st.integers(min_value=0, max_value=MAX_TURNS_CAP),
    debate_round=st.integers(min_value=1, max_value=MAX_ROUNDS + 5),
)
def test_property_16_hard_turn_bound_routes_to_judge(bounds, extra_turns, debate_round):
    """Validates: Requirements 2.4, 2.5, 6.2

    For ANY state whose ``debate_turns`` has reached or exceeded ``max_turns``,
    ``route_debate`` MUST route to ``"judge"`` regardless of the round counter —
    the hard turn-budget bound that makes infinite looping impossible.
    """
    rounds, max_turns = bounds
    cfg = _make_config(rounds, max_turns)

    original = graph.resolve_debate_config
    graph.resolve_debate_config = lambda *a, **k: cfg
    try:
        state = {
            "debate_round": debate_round,
            "debate_turns": max_turns + extra_turns,
        }
        route = route_debate(state)
        assert route == "judge", (
            f"expected 'judge' at debate_turns={max_turns + extra_turns} "
            f">= max_turns={max_turns}, got {route!r}"
        )
    finally:
        graph.resolve_debate_config = original
