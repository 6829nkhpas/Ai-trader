"""Property-based test for the Contribution_Metric being marked not-meaningful
below the dimension sample gate (attribution.py, task 4.3).

Feature: feature-attribution-pruning

This module implements design **Property 7: Contribution marked not-meaningful
below sample**:

    For any dimension whose total scored-trade count is below
    ``min_sample_dimension`` (including single-value, all-null, and zero-sample
    dimensions), ``compute_contribution`` reports the metric as not-meaningful
    (``None`` / ``contribution_meaningful == False``) rather than a spurious
    value, and never raises.

Validates: Requirements 2.4, 2.3.

The gate under test is the dimension-level SAMPLE gate inside
``compute_contribution``: it sums the ``count`` of every value carrying a usable
(finite, non-``None``) ``expectancy_r`` and returns ``None`` when that usable
total is ``0`` (empty / all-null / zero-sample) or is strictly below
``config.min_sample_dimension``. To exercise the "below sample" branch the
generators build ``{value: Dimension_Stats}`` mappings whose usable counts sum
to LESS than a deliberately HIGH ``min_sample_dimension`` (30), plus a battery of
degenerate shapes (empty dict, single value, all-null expectancy, zero/negative
counts, non-dict garbage). The complementary direction is asserted lightly: once
the usable total reaches the gate with at least one usable value, the result is a
non-negative float rather than ``None``.

The sys.path / import pattern mirrors
``tests/test_attribution_contribution_dispersion_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (attribution.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from attribution import (  # noqa: E402
    AttributionConfig,
    compute_contribution,
)

# A configuration with a deliberately HIGH dimension sample gate so that
# generated value_stats whose usable counts sum below 30 land squarely in the
# "below sample" / not-meaningful branch. Only ``min_sample_dimension`` is
# consulted by compute_contribution.
_MIN_SAMPLE_DIMENSION = 30
_CONFIG = AttributionConfig(
    min_sample_dimension=_MIN_SAMPLE_DIMENSION,
    min_sample_value=10,
    contribution_threshold=0.15,
    global_min_scored=50,
    down_weight_factor=0.5,
    weight_map_enabled=False,
)

# A pool of distinct value names so a dimension can carry several values.
_VALUE_NAMES = [
    "BUY", "SELL", "aligned", "below", "above",
    "trend-favorable", "leader-aligned", "strong", "weak", "morning", "unknown",
]

_expectancy = st.floats(
    min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False
)


# ── Generator: usable value_stats whose TOTAL usable count is BELOW the gate ──
@st.composite
def _below_sample_value_stats(draw):
    """Build a ``{value: Dimension_Stats}`` mapping with finite expectancies whose
    usable counts sum to STRICTLY LESS than ``_MIN_SAMPLE_DIMENSION`` (30).

    Each value carries a finite ``expectancy_r`` (so it is "usable") and a
    positive ``count``; the counts are drawn so their sum stays below the gate,
    placing the whole mapping in the not-meaningful "below sample" branch.
    """
    names = draw(
        st.lists(
            st.sampled_from(_VALUE_NAMES),
            min_size=1,
            max_size=5,
            unique=True,
        )
    )
    # Distribute a small total budget (< 30) across the chosen values so the sum
    # of counts is guaranteed below the gate. Budget in [1, 29].
    budget = draw(st.integers(min_value=1, max_value=_MIN_SAMPLE_DIMENSION - 1))
    value_stats = {}
    remaining = budget
    for i, name in enumerate(names):
        slots_left = len(names) - i
        # Leave at least 1 for each remaining value so every value has count >= 1.
        max_here = remaining - (slots_left - 1)
        count = draw(st.integers(min_value=1, max_value=max(1, max_here)))
        remaining -= count
        value_stats[name] = {
            "value": name,
            "count": count,
            "expectancy_r": round(draw(_expectancy), 4),
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "weak_prior": True,
        }
    # Invariant: usable total is below the gate.
    assert sum(s["count"] for s in value_stats.values()) < _MIN_SAMPLE_DIMENSION
    return value_stats


# ── Generator: usable value_stats whose TOTAL usable count MEETS the gate ─────
@st.composite
def _at_or_above_sample_value_stats(draw):
    """Build a ``{value: Dimension_Stats}`` mapping with finite expectancies whose
    usable counts sum to AT LEAST ``_MIN_SAMPLE_DIMENSION`` (the complementary,
    meaningful direction)."""
    names = draw(
        st.lists(
            st.sampled_from(_VALUE_NAMES),
            min_size=1,
            max_size=5,
            unique=True,
        )
    )
    value_stats = {}
    for name in names:
        count = draw(st.integers(min_value=1, max_value=40))
        value_stats[name] = {
            "value": name,
            "count": count,
            "expectancy_r": round(draw(_expectancy), 4),
        }
    # Bump the first value's count so the usable total comfortably clears the gate.
    first = next(iter(value_stats))
    value_stats[first]["count"] += _MIN_SAMPLE_DIMENSION
    assert sum(s["count"] for s in value_stats.values()) >= _MIN_SAMPLE_DIMENSION
    return value_stats


# ── Generator: degenerate / non-usable shapes (all expected to yield None) ────
@st.composite
def _all_null_value_stats(draw):
    """A dimension where NO value carries a usable expectancy: every value's
    ``expectancy_r`` is None (all-null), or its ``count`` is zero/negative, so the
    usable total is 0 regardless of how large the raw counts look."""
    names = draw(
        st.lists(
            st.sampled_from(_VALUE_NAMES),
            min_size=1,
            max_size=5,
            unique=True,
        )
    )
    value_stats = {}
    for name in names:
        kind = draw(st.sampled_from(["null_expectancy", "zero_count", "neg_count"]))
        if kind == "null_expectancy":
            # Plenty of count but no usable expectancy -> not usable.
            count = draw(st.integers(min_value=1, max_value=100))
            mean_r = None
        elif kind == "zero_count":
            count = 0
            mean_r = round(draw(_expectancy), 4)
        else:  # neg_count
            count = draw(st.integers(min_value=-100, max_value=-1))
            mean_r = round(draw(_expectancy), 4)
        value_stats[name] = {
            "value": name,
            "count": count,
            "expectancy_r": mean_r,
        }
    return value_stats


# Non-dict / garbage inputs the function must tolerate by returning None.
_GARBAGE_INPUTS = st.one_of(
    st.none(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(),
    st.lists(st.integers()),
    st.tuples(st.integers(), st.text()),
    st.booleans(),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 7 (task 4.3): Contribution marked not-meaningful below sample
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 7: For any dimension whose total scored-trade count is below min_sample_dimension (including single-value, all-null, and zero-sample dimensions), compute_contribution reports the metric as not-meaningful (None) rather than a spurious value, and never raises.
@settings(max_examples=100, deadline=None)
@given(value_stats=_below_sample_value_stats())
def test_property_7_below_sample_is_not_meaningful(value_stats):
    """Feature: feature-attribution-pruning, Property 7: a dimension whose usable
    total count is below ``min_sample_dimension`` is reported not-meaningful
    (None), never a spurious value, and never raises.

    Validates: Requirements 2.4, 2.3
    """
    result = compute_contribution(value_stats, _CONFIG)
    assert result is None


# Feature: feature-attribution-pruning, Property 7: For any dimension whose total scored-trade count is below min_sample_dimension (including single-value, all-null, and zero-sample dimensions), compute_contribution reports the metric as not-meaningful (None) rather than a spurious value, and never raises.
@settings(max_examples=100, deadline=None)
@given(
    name=st.sampled_from(_VALUE_NAMES),
    count=st.integers(min_value=1, max_value=_MIN_SAMPLE_DIMENSION - 1),
    mean_r=_expectancy,
)
def test_property_7_single_value_below_sample_is_not_meaningful(name, count, mean_r):
    """Feature: feature-attribution-pruning, Property 7: a single-value dimension
    whose lone count is below the gate is not-meaningful (None), never raises.

    Validates: Requirements 2.4, 2.3
    """
    value_stats = {
        name: {"value": name, "count": count, "expectancy_r": round(mean_r, 4)}
    }
    assert compute_contribution(value_stats, _CONFIG) is None


# Feature: feature-attribution-pruning, Property 7: For any dimension whose total scored-trade count is below min_sample_dimension (including single-value, all-null, and zero-sample dimensions), compute_contribution reports the metric as not-meaningful (None) rather than a spurious value, and never raises.
@settings(max_examples=100, deadline=None)
@given(value_stats=_all_null_value_stats())
def test_property_7_all_null_zero_sample_is_not_meaningful(value_stats):
    """Feature: feature-attribution-pruning, Property 7: a dimension with no usable
    value (all-null expectancy, zero/negative counts) has a usable total of 0 and
    is reported not-meaningful (None), never raises.

    Validates: Requirements 2.4, 2.3
    """
    assert compute_contribution(value_stats, _CONFIG) is None


# Feature: feature-attribution-pruning, Property 7: For any dimension whose total scored-trade count is below min_sample_dimension (including single-value, all-null, and zero-sample dimensions), compute_contribution reports the metric as not-meaningful (None) rather than a spurious value, and never raises.
@settings(max_examples=100, deadline=None)
@given(garbage=_GARBAGE_INPUTS)
def test_property_7_empty_or_garbage_is_not_meaningful(garbage):
    """Feature: feature-attribution-pruning, Property 7: an empty dict and any
    non-dict garbage input are reported not-meaningful (None), never raise.

    Validates: Requirements 2.4, 2.3
    """
    # Empty dict is the canonical zero-sample dimension.
    assert compute_contribution({}, _CONFIG) is None
    # Arbitrary non-dict input is tolerated (totality) and yields None.
    assert compute_contribution(garbage, _CONFIG) is None


# Feature: feature-attribution-pruning, Property 7: For any dimension whose total scored-trade count is below min_sample_dimension (including single-value, all-null, and zero-sample dimensions), compute_contribution reports the metric as not-meaningful (None) rather than a spurious value, and never raises.
@settings(max_examples=100, deadline=None)
@given(value_stats=_at_or_above_sample_value_stats())
def test_property_7_complement_at_or_above_sample_is_meaningful(value_stats):
    """Feature: feature-attribution-pruning, Property 7 (complementary direction):
    once the usable total meets ``min_sample_dimension`` with at least one usable
    value, the result is a non-negative float rather than None.

    Validates: Requirements 2.4, 2.3
    """
    result = compute_contribution(value_stats, _CONFIG)
    assert result is not None
    assert isinstance(result, float)
    assert result >= 0.0
