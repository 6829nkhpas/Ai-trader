"""Property-based test for bounded, contested-penalized conviction (debate.py, task 3.5).

Feature: multi-agent-debate

This module implements design **Property 4: Conviction is bounded and contested
is strictly less convicted**:

    (a) For ANY pair of Debate_Stances and ANY consensus string,
        ``derive_conviction`` returns an integer in [0, 100]; AND
    (b) for any two stance-pairs with identical strengths whose winning side has
        enough strength to convict on, the conviction derived under a
        ``contested`` consensus is strictly less than the conviction derived
        under a ``strong_agree`` consensus.

Validates: Requirements 4.4.

Part (a) draws arbitrary ``DebateStance`` pairs (strengths spanning below
``STRENGTH_MIN``, the in-range band, and above ``STRENGTH_MAX`` so the clamping
path is exercised, ``available`` varied so the unavailable-as-strength-0 path is
hit) together with an arbitrary consensus string (including values outside
``DEBATE_CONSENSUS_VALUES``) and asserts the result is an int in [0, 100].

Part (b) draws available stance pairs whose winning strength is >= 2 so the
unpenalized conviction ``round(W_BASE*base + W_SEP*sep)`` is >= 1. Because the
``CONTESTED_PENALTY`` subtracts then clamps to 0, ``contested = max(0, v - 25)``
and ``strong_agree = v``; with ``v >= 1`` this makes contested strictly less than
strong_agree. (For strength 0/0 both convictions are 0 and would tie, which is
why part (b) requires a nonzero winning strength.)

The sys.path / import pattern mirrors the sibling ``test_session_*`` /
``test_debate_*`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (debate.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from debate import (  # noqa: E402
    DEBATE_LEANS,
    STRENGTH_MAX,
    STRENGTH_MIN,
    DebateStance,
    derive_conviction,
)


@st.composite
def _any_stance(draw):
    """An arbitrary ``DebateStance`` built directly via the dataclass.

    Strength spans below ``STRENGTH_MIN``, the in-range band, and above
    ``STRENGTH_MAX`` so the clamping path is exercised; ``available`` is varied so
    the unavailable-as-strength-0 path is hit too.
    """
    return DebateStance(
        role=draw(st.sampled_from(["bull", "bear", "", "judge"])),
        lean=draw(st.sampled_from(list(DEBATE_LEANS) + ["garbled"])),
        strength=draw(st.integers(min_value=STRENGTH_MIN - 50, max_value=STRENGTH_MAX + 50)),
        arguments=draw(st.lists(st.text(max_size=20), max_size=5)),
        biggest_risk=draw(st.text(max_size=30)),
        available=draw(st.booleans()),
    )


@st.composite
def _convictable_pair(draw):
    """An available ``DebateStance`` pair whose winning strength is >= 2.

    Both strengths are drawn in ``[2, STRENGTH_MAX]`` and both stances are
    available, so ``max(b, r) >= 2`` and the unpenalized conviction
    ``round(W_BASE*base + W_SEP*sep)`` is guaranteed >= 1. This is exactly the
    regime in which the contested penalty produces a strict inequality.
    """
    bull_strength = draw(st.integers(min_value=2, max_value=STRENGTH_MAX))
    bear_strength = draw(st.integers(min_value=2, max_value=STRENGTH_MAX))
    bull = DebateStance(
        role="bull",
        lean=draw(st.sampled_from(list(DEBATE_LEANS))),
        strength=bull_strength,
        arguments=draw(st.lists(st.text(max_size=20), max_size=5)),
        biggest_risk=draw(st.text(max_size=30)),
        available=True,
    )
    bear = DebateStance(
        role="bear",
        lean=draw(st.sampled_from(list(DEBATE_LEANS))),
        strength=bear_strength,
        arguments=draw(st.lists(st.text(max_size=20), max_size=5)),
        biggest_risk=draw(st.text(max_size=30)),
        available=True,
    )
    return bull, bear


# ─────────────────────────────────────────────────────────────────────────────
# Property 4: Conviction is bounded and contested is strictly less convicted
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 4: Conviction is bounded and contested is strictly less convicted
@settings(max_examples=100, deadline=None)
@given(
    bull=_any_stance(),
    bear=_any_stance(),
    consensus=st.one_of(
        st.sampled_from(["strong_agree", "lean", "contested"]),
        st.text(max_size=20),
    ),
)
def test_property_4a_conviction_is_bounded_integer(bull, bear, consensus):
    """Validates: Requirements 4.4

    For any pair of ``DebateStance`` values and any consensus string,
    ``derive_conviction`` returns an integer in [0, 100] and never raises.
    """
    conviction = derive_conviction(bull, bear, consensus)

    assert isinstance(conviction, int) and not isinstance(conviction, bool), (
        f"derive_conviction returned {conviction!r}, which is not a plain int"
    )
    assert 0 <= conviction <= 100, (
        f"derive_conviction returned {conviction}, outside [0, 100]"
    )


# Feature: multi-agent-debate, Property 4: Conviction is bounded and contested is strictly less convicted
@settings(max_examples=100, deadline=None)
@given(pair=_convictable_pair())
def test_property_4b_contested_is_strictly_less_convicted(pair):
    """Validates: Requirements 4.4

    For two stance-pairs with identical strengths whose winning side has enough
    strength to convict on (unpenalized conviction >= 1), the conviction under a
    ``contested`` consensus is strictly less than under a ``strong_agree``
    consensus.
    """
    bull, bear = pair

    contested = derive_conviction(bull, bear, "contested")
    strong_agree = derive_conviction(bull, bear, "strong_agree")

    assert contested < strong_agree, (
        f"contested conviction {contested} not strictly less than strong_agree "
        f"conviction {strong_agree} for strengths "
        f"(bull={bull.strength}, bear={bear.strength})"
    )
