"""Property-based test for dominant-stance agreement (debate.py, task 3.3).

Feature: multi-agent-debate

This module implements design **Property 2: A dominant stance yields agreement,
not contest**:

    For any pair of stances where the strength gap
    ``|bull.strength - bear.strength| >= STRONG_GAP`` and the dominant strength
    ``>= STRONG_FLOOR`` (both stances available), ``classify_consensus`` returns
    ``"strong_agree"`` and never ``"contested"``.

Validates: Requirements 4.2.

The strategy draws the dominant (hi) strength in ``[STRONG_FLOOR, STRENGTH_MAX]``
and the recessive (lo) strength in ``[STRENGTH_MIN, hi - STRONG_GAP]`` so the gap
is always at least ``STRONG_GAP`` and the dominant side is always at least
``STRONG_FLOOR``. It then randomly assigns ``hi`` to the Bull or the Bear (with
``lo`` to the other) so the property is exercised symmetrically regardless of
which role dominates. Both stances are ``available=True`` so their strengths
count (an unavailable stance is treated as ``0`` by ``_effective_strength``).

The sys.path / import pattern mirrors the sibling ``test_debate_*`` and
``test_session_*`` modules.
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
    STRENGTH_MAX,
    STRENGTH_MIN,
    STRONG_FLOOR,
    STRONG_GAP,
    DebateStance,
    classify_consensus,
)


def _stance(role: str, strength: int, lean: str = "neutral") -> DebateStance:
    """An available stance with the given strength (other fields are immaterial)."""
    return DebateStance(
        role=role,
        lean=lean,
        strength=strength,
        arguments=["a"],
        biggest_risk="",
        available=True,
    )


@st.composite
def _dominant_pair(draw):
    """A (bull, bear) pair with gap >= STRONG_GAP and dominant >= STRONG_FLOOR.

    ``hi`` is drawn in ``[STRONG_FLOOR, STRENGTH_MAX]`` and ``lo`` in
    ``[STRENGTH_MIN, hi - STRONG_GAP]``; the dominant strength ``hi`` is randomly
    assigned to either the Bull or the Bear so both orderings are exercised.
    """
    hi = draw(st.integers(min_value=STRONG_FLOOR, max_value=STRENGTH_MAX))
    lo = draw(st.integers(min_value=STRENGTH_MIN, max_value=hi - STRONG_GAP))
    bull_dominates = draw(st.booleans())
    if bull_dominates:
        return _stance("bull", hi), _stance("bear", lo)
    return _stance("bull", lo), _stance("bear", hi)


# Feature: multi-agent-debate, Property 2: A dominant stance yields agreement, not contest
@settings(max_examples=100, deadline=None)
@given(pair=_dominant_pair())
def test_property_2_dominant_stance_yields_agreement(pair):
    """Validates: Requirements 4.2

    For any pair where the gap >= STRONG_GAP and the dominant strength >=
    STRONG_FLOOR (both available), ``classify_consensus`` returns ``strong_agree``
    and never ``contested``.
    """
    bull, bear = pair

    gap = abs(bull.strength - bear.strength)
    dominant = max(bull.strength, bear.strength)
    # Sanity-check the generator actually lands in the dominant region.
    assert gap >= STRONG_GAP
    assert dominant >= STRONG_FLOOR

    result = classify_consensus(bull, bear)
    assert result == "strong_agree", (
        f"expected strong_agree for gap={gap} dominant={dominant} "
        f"(bull={bull.strength}, bear={bear.strength}), got {result!r}"
    )
    assert result != "contested"
