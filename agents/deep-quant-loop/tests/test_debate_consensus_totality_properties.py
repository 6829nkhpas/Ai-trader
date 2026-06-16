"""Property-based test for consensus classification totality (debate.py, task 3.2).

Feature: multi-agent-debate

This module implements design **Property 1: Consensus classification is total
and well-formed**:

    For ANY pair of Debate_Stances (each strength clamped to [0, 100], an
    unavailable stance treated as strength 0), ``classify_consensus`` returns
    exactly one value from ``{strong_agree, lean, contested}`` (=
    ``DEBATE_CONSENSUS_VALUES``).

Validates: Requirements 4.1.

The strategy builds arbitrary ``DebateStance`` pairs directly via the frozen
dataclass, varying role / lean / strength / arguments / biggest_risk /
available. Strengths are drawn across and beyond ``[STRENGTH_MIN, STRENGTH_MAX]``
(including negative and far-out-of-range values) and ``available`` is varied so
the unavailable-stance-as-strength-0 path is exercised. For every pair the test
asserts the classifier returns exactly one member of ``DEBATE_CONSENSUS_VALUES``.

The sys.path / import pattern mirrors the sibling ``test_session_*`` modules.
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
    DEBATE_CONSENSUS_VALUES,
    DEBATE_LEANS,
    STRENGTH_MAX,
    STRENGTH_MIN,
    DebateStance,
    classify_consensus,
)


@st.composite
def _stance(draw):
    """An arbitrary ``DebateStance`` built directly via the dataclass.

    Strength deliberately spans below ``STRENGTH_MIN``, the in-range band, and
    above ``STRENGTH_MAX`` so the classifier's clamping path is exercised;
    ``available`` is varied so the unavailable-as-strength-0 path is hit too.
    """
    strength = draw(
        st.integers(min_value=STRENGTH_MIN - 50, max_value=STRENGTH_MAX + 50)
    )
    arguments = draw(st.lists(st.text(max_size=20), max_size=5))
    return DebateStance(
        role=draw(st.sampled_from(["bull", "bear", "", "judge"])),
        lean=draw(st.sampled_from(list(DEBATE_LEANS) + ["garbled"])),
        strength=strength,
        arguments=arguments,
        biggest_risk=draw(st.text(max_size=30)),
        available=draw(st.booleans()),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 1: Consensus classification is total and well-formed
# ─────────────────────────────────────────────────────────────────────────────

# Feature: multi-agent-debate, Property 1: Consensus classification is total and well-formed
@settings(max_examples=100, deadline=None)
@given(bull=_stance(), bear=_stance())
def test_property_1_consensus_classification_is_total(bull, bear):
    """Validates: Requirements 4.1

    For any pair of ``DebateStance`` values, ``classify_consensus`` returns
    exactly one member of ``DEBATE_CONSENSUS_VALUES`` and never raises.
    """
    result = classify_consensus(bull, bear)

    assert result in DEBATE_CONSENSUS_VALUES, (
        f"classify_consensus returned {result!r}, which is not one of "
        f"{DEBATE_CONSENSUS_VALUES}"
    )
    # "Exactly one value" — the return is a single enum member, so it matches
    # precisely one of the three categorical outcomes.
    assert sum(1 for v in DEBATE_CONSENSUS_VALUES if v == result) == 1
