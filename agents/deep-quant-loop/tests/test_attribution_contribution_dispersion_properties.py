"""Property-based test for the Contribution_Metric as a sample-weighted dispersion
of expectancy (attribution.py, task 4.2).

Feature: feature-attribution-pruning

This module implements design **Property 6: Contribution is a sample-weighted
dispersion of expectancy**:

    For any dimension's per-value statistics, ``compute_contribution`` equals the
    sample-weighted standard deviation of per-value expectancy about the
    sample-weighted mean expectancy: it is ``0`` when all values share an equal
    expectancy, is non-decreasing as the values' expectancies are scaled further
    apart, and is non-negative whenever it is meaningful.

Validates: Requirements 2.1, 2.2.

The contribution is checked against an INDEPENDENT oracle that re-derives the
sample-weighted standard deviation directly from the (count, expectancy_r) pairs
(never calling the function under test for the expected value). The oracle
mirrors the implementation's meaningfulness gate (Σ n_v >= min_sample_dimension,
usable finite expectancy with positive count) and its 4-decimal rounding so the
expected values match bit-for-bit.

The sys.path / import pattern mirrors
``tests/test_attribution_stats_correctness_properties.py``. Here the generator
builds the inner ``{value: Dimension_Stats}`` mapping for ONE dimension directly,
which is exactly the shape ``compute_contribution`` consumes.
"""

import math
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

# A configuration with a LOW dimension sample gate (min_sample_dimension=1) so a
# generated dimension that carries at least one usable value clears the
# meaningfulness gate and the dispersion is actually computed (not short-circuited
# to None). Only ``min_sample_dimension`` is consulted by compute_contribution.
_CONFIG = AttributionConfig(
    min_sample_dimension=1,
    min_sample_value=10,
    contribution_threshold=0.15,
    global_min_scored=50,
    down_weight_factor=0.5,
    weight_map_enabled=False,
)


# ── Generators: a dimension's inner {value: Dimension_Stats} mapping ──────────
# A finite, usable per-value expectancy (mean R-multiple). Bounded so the squared
# deviations stay well within float precision and the 4dp rounding is stable.
_expectancy = st.floats(
    min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False
)

# A positive scored-trade count for a value (>= 1).
_count = st.integers(min_value=1, max_value=50)

# A pool of distinct value names so a dimension carries several values.
_VALUE_NAMES = [
    "BUY", "SELL", "aligned", "below", "above",
    "trend-favorable", "leader-aligned", "strong", "weak", "morning", "unknown",
]


@st.composite
def _value_stats(draw, min_values=1, max_values=8):
    """Build one dimension's inner ``{value: Dimension_Stats}`` mapping directly.

    Each Dimension_Stats carries the two fields ``compute_contribution`` reads —
    ``count`` (>= 1) and ``expectancy_r`` (a finite float) — plus the structural
    fields a real Dimension_Stats carries, so the generated shape matches what
    ``compute_dimension_stats`` would produce.
    """
    names = draw(
        st.lists(
            st.sampled_from(_VALUE_NAMES),
            min_size=min_values,
            max_size=max_values,
            unique=True,
        )
    )
    value_stats = {}
    for name in names:
        count = draw(_count)
        mean_r = draw(_expectancy)
        value_stats[name] = {
            "value": name,
            "count": count,
            "expectancy_r": round(mean_r, 4),
        }
    return value_stats


# ── Independent oracle ────────────────────────────────────────────────────────
def _oracle_contribution(value_stats, config):
    """Re-derive the sample-weighted standard deviation of per-value expectancy
    directly from the (count, expectancy_r) pairs.

    Mirrors the implementation's meaningfulness gate and 4-decimal rounding so
    the expected value matches bit-for-bit. Deliberately does NOT call
    ``compute_contribution``.
    """
    weighted = []  # [(n_v, meanR_v), ...]
    total_n = 0
    for stats in value_stats.values():
        count = stats.get("count")
        mean_r = stats.get("expectancy_r")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            continue
        if not (isinstance(mean_r, (int, float)) and not isinstance(mean_r, bool)
                and math.isfinite(mean_r)):
            continue
        weighted.append((count, float(mean_r)))
        total_n += count

    if total_n <= 0:
        return None
    if total_n < config.min_sample_dimension:
        return None

    mu = sum(n * mean_r for n, mean_r in weighted) / total_n
    variance = sum(n * (mean_r - mu) ** 2 for n, mean_r in weighted) / total_n
    if variance < 0.0:
        variance = 0.0
    return round(math.sqrt(variance), 4)


def _scale_deviations(value_stats, k):
    """Return a copy of ``value_stats`` where each value's expectancy is mapped
    e -> mu + k*(e - mu) about the sample-weighted mean mu, pushing the
    expectancies further apart for k > 1."""
    weighted = [(s["count"], float(s["expectancy_r"])) for s in value_stats.values()]
    total_n = sum(n for n, _ in weighted)
    mu = sum(n * r for n, r in weighted) / total_n
    out = {}
    for name, s in value_stats.items():
        e = float(s["expectancy_r"])
        scaled = mu + k * (e - mu)
        out[name] = {**s, "expectancy_r": round(scaled, 4)}
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Property 6 (task 4.2): Contribution is a sample-weighted dispersion of expectancy
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 6: For any dimension's per-value statistics, compute_contribution equals the sample-weighted standard deviation of per-value expectancy about the sample-weighted mean expectancy: it is 0 when all values share an equal expectancy, is non-decreasing as the values' expectancies are scaled further apart, and is non-negative whenever it is meaningful.
@settings(max_examples=200, deadline=None)
@given(value_stats=_value_stats())
def test_property_6_contribution_is_sample_weighted_dispersion(value_stats):
    """Feature: feature-attribution-pruning, Property 6: compute_contribution
    equals the independent sample-weighted-stddev oracle (rounded to 4dp) when
    meaningful, and is non-negative.

    Validates: Requirements 2.1, 2.2
    """
    contrib = compute_contribution(value_stats, _CONFIG)
    expected = _oracle_contribution(value_stats, _CONFIG)

    # 1. Equals the independent oracle (rounded to 4dp) when meaningful.
    assert contrib == expected

    # 3. Non-negative whenever it is meaningful.
    if contrib is not None:
        assert contrib >= 0.0


# Feature: feature-attribution-pruning, Property 6: For any dimension's per-value statistics, compute_contribution equals the sample-weighted standard deviation of per-value expectancy about the sample-weighted mean expectancy: it is 0 when all values share an equal expectancy, is non-decreasing as the values' expectancies are scaled further apart, and is non-negative whenever it is meaningful.
@settings(max_examples=200, deadline=None)
@given(
    names=st.lists(
        st.sampled_from(_VALUE_NAMES), min_size=1, max_size=8, unique=True
    ),
    counts=st.lists(_count, min_size=1, max_size=8),
    shared_expectancy=_expectancy,
)
def test_property_6_zero_when_all_values_share_one_expectancy(
    names, counts, shared_expectancy
):
    """Feature: feature-attribution-pruning, Property 6: when every value shares a
    single expectancy, the dispersion is exactly 0.0 (R2.2).

    Validates: Requirements 2.1, 2.2
    """
    shared = round(shared_expectancy, 4)
    value_stats = {
        name: {"value": name, "count": counts[i % len(counts)], "expectancy_r": shared}
        for i, name in enumerate(names)
    }
    contrib = compute_contribution(value_stats, _CONFIG)
    assert contrib == 0.0


# Feature: feature-attribution-pruning, Property 6: For any dimension's per-value statistics, compute_contribution equals the sample-weighted standard deviation of per-value expectancy about the sample-weighted mean expectancy: it is 0 when all values share an equal expectancy, is non-decreasing as the values' expectancies are scaled further apart, and is non-negative whenever it is meaningful.
@settings(max_examples=200, deadline=None)
@given(
    value_stats=_value_stats(min_values=2),
    k=st.floats(min_value=1.0, max_value=20.0, allow_nan=False, allow_infinity=False),
)
def test_property_6_monotonic_under_scaling_deviations_apart(value_stats, k):
    """Feature: feature-attribution-pruning, Property 6: scaling each value's
    expectancy further from the sample-weighted mean (e -> mu + k*(e-mu), k>=1)
    yields a contribution >= the original (non-decreasing as expectancies are
    pushed apart) (R2.2).

    Validates: Requirements 2.1, 2.2
    """
    base = compute_contribution(value_stats, _CONFIG)
    scaled = compute_contribution(_scale_deviations(value_stats, k), _CONFIG)

    # Both clear the same sample gate (the scaling does not change any count), so
    # both are meaningful here.
    assert base is not None
    assert scaled is not None

    # Non-decreasing. Allow a small tolerance for the 4dp rounding applied on both
    # the scaled expectancies and the final contribution.
    assert scaled >= base - 1e-4
