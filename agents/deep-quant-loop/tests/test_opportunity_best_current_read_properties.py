"""Property-based test for the interim Best_Current_Read (opportunity.py, task 7.2).

Feature: adaptive-opportunity-engine

This module implements design **Property 15: Best_Current_Read is evidence-only and
non-committal**:

    For any evidence dict and tier evaluation, ``best_current_read`` returns a
    ``{bias, levels, why_standing_aside}`` assessment that (a) is NON-COMMITTAL —
    it never carries a committed-trade ``action``, ``execution_plan``, or
    ``conviction_score``; (b) is EVIDENCE-ONLY — every reference level it surfaces
    is a finite number that was present in the evidence, and it fabricates nothing;
    and (c) is TOTAL and DETERMINISTIC — never raises on malformed / partial /
    ``None`` input and returns the same result for the same inputs.

Validates: Requirements 8.1, 8.3.

The sys.path / import bootstrap and the ``@settings`` / ``@given`` convention mirror
``tests/test_opportunity_watch_cap_convergence_properties.py`` and the sibling
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
    TierEvaluation,
    best_current_read,
)

_COMMITTAL_KEYS = ("action", "execution_plan", "conviction_score", "entry_order", "trade")

_signal = st.fixed_dictionaries(
    {
        "available": st.booleans(),
        "favorability": st.sampled_from(["favorable", "unfavorable", "neutral", "??"]),
        "alignment": st.sampled_from(["aligned", "misaligned", "neutral", "??"]),
    }
)

_level = st.one_of(
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.sampled_from([None, "abc", float("nan"), float("inf")]),
)


@st.composite
def evidences(draw):
    """Build a (possibly partial / malformed) evidence dict shaped like the one the
    tier ladder consumes."""
    ev = {}
    if draw(st.booleans()):
        ev["pattern_confidence"] = draw(_level)
    for key in ("entry", "stop", "target"):
        if draw(st.booleans()):
            ev[key] = draw(_level)
    for key in ("regime", "session", "relative_strength", "forecast", "macro", "options"):
        if draw(st.booleans()):
            ev[key] = draw(_signal)
    return ev


_tier_evals = st.one_of(
    st.none(),
    st.builds(
        TierEvaluation,
        tier=st.sampled_from(["a_plus", "b_continuation", "scalp", "stand_aside"]),
        size_factor=st.floats(min_value=0.0, max_value=1.0),
        rationale=st.text(max_size=40),
        gated_by=st.sampled_from([None, "regime", "session", "evidence-bar", "config"]),
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 15, facet 1 — non-committal shape (no committed-trade keys)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 15: best_current_read never carries a committed-trade action/execution_plan/conviction_score.
@settings(max_examples=300, deadline=None)
@given(evidence=st.one_of(evidences(), st.none(), st.sampled_from(["x", 1, []])), tier_eval=_tier_evals)
def test_property_15_non_committal(evidence, tier_eval):
    """Feature: adaptive-opportunity-engine, Property 15 (non-committal): the read
    carries exactly the assessment keys ``{bias, levels, why_standing_aside}`` and
    NONE of the committed-trade keys, so it can never be mistaken for a trade.

    Validates: Requirements 8.1, 8.3
    """
    read = best_current_read(evidence, tier_eval)

    assert isinstance(read, dict)
    assert set(read.keys()) == {"bias", "levels", "why_standing_aside"}
    for k in _COMMITTAL_KEYS:
        assert k not in read
    assert read["bias"] in ("bullish", "bearish", "neutral")
    assert isinstance(read["why_standing_aside"], str) and read["why_standing_aside"].strip()


# ─────────────────────────────────────────────────────────────────────────────
# Property 15, facet 2 — levels are evidence-only finite numbers (no fabrication)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 15: every surfaced reference level is a finite number that appeared in the evidence; nothing is fabricated.
@settings(max_examples=300, deadline=None)
@given(evidence=evidences(), tier_eval=_tier_evals)
def test_property_15_levels_are_evidence_only(evidence, tier_eval):
    """Feature: adaptive-opportunity-engine, Property 15 (evidence-only): each level
    surfaced is a finite float equal to the corresponding finite value in the
    evidence; a missing / non-finite evidence level is never surfaced (no
    fabrication).

    Validates: Requirements 8.3
    """
    read = best_current_read(evidence, tier_eval)
    levels = read["levels"]
    assert isinstance(levels, dict)

    for key in ("entry", "stop", "target"):
        src = evidence.get(key)
        src_finite = isinstance(src, (int, float)) and not isinstance(src, bool) and math.isfinite(src)
        if src_finite:
            assert key in levels and levels[key] == float(src)
        else:
            assert key not in levels, f"fabricated a {key} level not present as a finite number"


# ─────────────────────────────────────────────────────────────────────────────
# Property 15, facet 3 — total and deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 15: best_current_read is total (never raises) and deterministic for identical inputs.
@settings(max_examples=200, deadline=None)
@given(evidence=st.one_of(evidences(), st.none()), tier_eval=_tier_evals)
def test_property_15_total_and_deterministic(evidence, tier_eval):
    """Feature: adaptive-opportunity-engine, Property 15 (total/deterministic): the
    read never raises and returns an identical result for identical inputs.

    Validates: Requirements 8.1, 8.3
    """
    a = best_current_read(evidence, tier_eval)
    b = best_current_read(evidence, tier_eval)
    assert a == b
