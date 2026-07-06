"""Property-based test for the opportunity tier tag (opportunity.py, task 7.3).

Feature: adaptive-opportunity-engine

This module implements design **Property 16: Tier tag is low-cardinality and
deterministically positioned**:

    For any committed decision, ``tier_tag`` returns exactly one of the at-most-five
    values in ``TIER_TAG_VALUES`` (``a_plus``, ``b_continuation``, ``scalp``,
    ``stand_aside``, ``unknown``); a recognized tier maps to itself
    (case-insensitively, whitespace-tolerant) and every missing / non-dict /
    non-string / unrecognized tier collapses to ``unknown``. The tag is
    deterministic for identical inputs.

Validates: Requirements 9.2.

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
    TIER_TAG_VALUES,
    tier_tag,
)

_KNOWN = ("a_plus", "b_continuation", "scalp", "stand_aside")


# ─────────────────────────────────────────────────────────────────────────────
# Property 16, facet 1 — always one of at most five values (low cardinality)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 16: tier_tag returns one of at most five values for ANY decision input.
@settings(max_examples=300, deadline=None)
@given(
    decision=st.one_of(
        st.none(),
        st.sampled_from(["x", 1, [], 3.5]),
        st.dictionaries(
            keys=st.sampled_from(["opportunity_tier", "action", "conviction_score"]),
            values=st.one_of(
                st.sampled_from(list(_KNOWN)),
                st.text(max_size=12),
                st.none(),
                st.integers(),
            ),
            max_size=3,
        ),
    )
)
def test_property_16_low_cardinality(decision):
    """Feature: adaptive-opportunity-engine, Property 16 (low cardinality): the tag is
    always one of the at-most-five ``TIER_TAG_VALUES`` for any input.

    Validates: Requirements 9.2
    """
    tag = tier_tag(decision)
    assert tag in TIER_TAG_VALUES
    assert len(set(TIER_TAG_VALUES)) <= 5


# ─────────────────────────────────────────────────────────────────────────────
# Property 16, facet 2 — recognized tier maps to itself (case/space-insensitive)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 16: a recognized tier maps to its canonical tag, case-insensitively and whitespace-tolerant.
@settings(max_examples=200, deadline=None)
@given(
    tier=st.sampled_from(list(_KNOWN)),
    pad=st.sampled_from(["", " ", "  ", "\t"]),
    upper=st.booleans(),
)
def test_property_16_known_tier_maps_to_itself(tier, pad, upper):
    """Feature: adaptive-opportunity-engine, Property 16 (recognized): a known tier —
    including with surrounding whitespace and mixed case — maps to its canonical
    lowercase tag.

    Validates: Requirements 9.2
    """
    spelled = tier.upper() if upper else tier
    decision = {"opportunity_tier": f"{pad}{spelled}{pad}"}
    assert tier_tag(decision) == tier


# ─────────────────────────────────────────────────────────────────────────────
# Property 16, facet 3 — unrecognized / missing collapses to 'unknown'
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 16: a missing/non-string/unrecognized tier collapses to 'unknown'.
@settings(max_examples=200, deadline=None)
@given(
    tier=st.one_of(
        st.none(),
        st.integers(),
        st.text(max_size=15).filter(lambda s: s.strip().lower() not in _KNOWN),
        st.just("premium"),
        st.just("A"),
    )
)
def test_property_16_unrecognized_is_unknown(tier):
    """Feature: adaptive-opportunity-engine, Property 16 (fallback): a missing /
    non-string / unrecognized tier collapses deterministically to ``unknown``.

    Validates: Requirements 9.2
    """
    assert tier_tag({"opportunity_tier": tier}) == "unknown"
    assert tier_tag({}) == "unknown"
    # Deterministic.
    assert tier_tag({"opportunity_tier": tier}) == tier_tag({"opportunity_tier": tier})
