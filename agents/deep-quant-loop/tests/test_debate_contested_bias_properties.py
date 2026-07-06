"""Property-based test for contested classification and cautious bias (debate.py, task 3.4).

Feature: multi-agent-debate

This module implements design **Property 3: Two strong opposed stances are
contested and bias to caution**:

    For any pair of stances where the weaker strength ``lo >= STRONG_FLOOR`` and
    the gap ``hi - lo <= CONTESTED_GAP`` (both stances strong and close), with
    both stances available, ``classify_consensus`` returns ``"contested"`` AND
    ``judge_directional_bias`` suggests ``"hold"`` (caution / smaller size),
    regardless of either side's directional lean.

Validates: Requirements 4.3.

The strategy draws the weaker strength ``lo`` in ``[STRONG_FLOOR, 100]`` and a
``gap`` in ``[0, CONTESTED_GAP]``, sets ``hi = min(100, lo + gap)`` (so the
realized gap stays ``<= CONTESTED_GAP`` and ``lo`` stays ``>= STRONG_FLOOR``),
then randomly assigns ``(hi, lo)`` to the (bull, bear) pair and varies each
role's lean across all of ``DEBATE_LEANS``. The contested rule biases to HOLD
independent of strengths and leans, so the bias assertion must hold for every
generated lean combination. The sys.path / import pattern mirrors the sibling
``test_session_*`` / ``test_rs_*`` property modules.
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
    CONTESTED_GAP,
    DEBATE_LEANS,
    STRONG_FLOOR,
    STRENGTH_MAX,
    DebateStance,
    classify_consensus,
    judge_directional_bias,
)


def _stance(role: str, lean: str, strength: int) -> DebateStance:
    """An available stance with the given role, lean, and strength."""
    return DebateStance(
        role=role,
        lean=lean,
        strength=strength,
        arguments=["x"],
        biggest_risk="",
        available=True,
    )


@st.composite
def _contested_stance_pairs(draw):
    """A (bull, bear) pair that is strong-and-close: lo >= STRONG_FLOOR, gap <= CONTESTED_GAP.

    ``lo`` (the weaker side) is drawn in ``[STRONG_FLOOR, STRENGTH_MAX]`` and the
    gap in ``[0, CONTESTED_GAP]``; ``hi = min(STRENGTH_MAX, lo + gap)`` keeps the
    realized gap within the contested band. The two strengths are randomly
    assigned to bull/bear and each role's lean varies across ``DEBATE_LEANS``.
    """
    lo = draw(st.integers(min_value=STRONG_FLOOR, max_value=STRENGTH_MAX))
    gap = draw(st.integers(min_value=0, max_value=CONTESTED_GAP))
    hi = min(STRENGTH_MAX, lo + gap)

    bull_gets_hi = draw(st.booleans())
    bull_strength, bear_strength = (hi, lo) if bull_gets_hi else (lo, hi)

    bull_lean = draw(st.sampled_from(DEBATE_LEANS))
    bear_lean = draw(st.sampled_from(DEBATE_LEANS))

    bull = _stance("bull", bull_lean, bull_strength)
    bear = _stance("bear", bear_lean, bear_strength)
    return bull, bear


# ─────────────────────────────────────────────────────────────────────────────
# Property 3: Two strong opposed stances are contested and bias to caution
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 3: Two strong opposed stances are contested and bias to caution
@settings(max_examples=100, deadline=None)
@given(pair=_contested_stance_pairs())
def test_property_3_contested_strong_opposed_bias_to_caution(pair):
    """Validates: Requirements 4.3

    For any pair of available stances with the weaker strength >= STRONG_FLOOR
    and the gap <= CONTESTED_GAP, classify_consensus is "contested" and
    judge_directional_bias is "hold" regardless of the roles' leans.
    """
    bull, bear = pair

    # Sanity on the generated band (both strong, close).
    lo = min(bull.strength, bear.strength)
    gap = abs(bull.strength - bear.strength)
    assert lo >= STRONG_FLOOR, f"weaker strength {lo} below STRONG_FLOOR {STRONG_FLOOR}"
    assert gap <= CONTESTED_GAP, f"gap {gap} above CONTESTED_GAP {CONTESTED_GAP}"

    consensus = classify_consensus(bull, bear)
    assert consensus == "contested", (
        f"expected 'contested' for lo={lo}, gap={gap}; got {consensus!r}"
    )

    bias = judge_directional_bias(bull, bear, consensus)
    assert bias == "hold", (
        f"expected 'hold' bias for contested consensus (bull lean={bull.lean!r}, "
        f"bear lean={bear.lean!r}); got {bias!r}"
    )
