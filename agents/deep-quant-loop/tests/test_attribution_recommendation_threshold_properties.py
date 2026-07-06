"""Property-based test for recommendation threshold logic (attribution.py, task 5.4).

Feature: feature-attribution-pruning

This module implements design **Property 10: Recommendation threshold logic**:

    For any dimension with sufficient sample (total scored >= min_sample_dimension)
    and a meaningful contribution, the Recommendation is down_weight when the
    contribution is below contribution_threshold and keep when it is at or above
    the threshold.

Validates: Requirements 3.4, 3.5.

``build_attribution_report`` wires the pure pipeline end to end and returns a
ranked ``report["dimensions"]`` list of Dimension_Report entries. Each entry
carries a ``"total_scored"`` (the Σ of its per-value scored counts), a
``"contribution"`` (a float, or ``None`` when not meaningful) plus its
``"contribution_meaningful"`` flag, and exactly one ``"recommendation"`` label.

Design AD-3 fixes the recommendation control flow so that, *once a dimension has
cleared the sample gate AND has a meaningful contribution*, the threshold
comparison is the sole decider: ``contribution < contribution_threshold`` ->
``down_weight``; ``contribution >= contribution_threshold`` -> ``keep``. This
property exercises exactly that branch across the journal space.

To make the keep/down_weight branch fire on most examples, this test uses a
deterministic config with a LOW ``min_sample_dimension`` (2) and generates larger
journals over a SMALL pool of dimensions/values, so per-dimension scored counts
routinely clear the sample gate and the threshold comparison is reached.

The sys.path / import pattern and the ``@composite`` journal generator mirror
``tests/test_attribution_statistical_honesty_properties.py`` /
``tests/test_attribution_recommendation_totality_properties.py`` (kept local to
this file for consistency).
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
    RECOMMENDATION_DOWN_WEIGHT,
    RECOMMENDATION_INSUFFICIENT_SAMPLE,
    RECOMMENDATION_KEEP,
    build_attribution_report,
)

# A deterministic, fixed configuration. ``min_sample_dimension`` is set LOW (2)
# relative to the larger journals generated below, so most dimensions CLEAR the
# sample gate and the keep/down_weight threshold branch is exercised on the bulk
# of examples. ``min_sample_value`` is 1 so per-value stats stay usable.
_CONFIG = AttributionConfig(
    min_sample_dimension=2,
    min_sample_value=1,
    contribution_threshold=0.15,
    global_min_scored=50,
    down_weight_factor=0.5,
    weight_map_enabled=False,
)


# ── Shared journal generators (local to this file) ────────────────────────────
# A SMALL pool of fingerprint dimensions and values so generated keys collide
# heavily across rows, driving per-value/per-dimension scored counts well above
# the (low) sample gate and exercising real aggregation + the threshold branch.
_DIMENSIONS = ["dir", "regime", "rs", "fc", "opt"]
_VALUES = ["BUY", "SELL", "aligned", "below", "strong", "weak"]

# A finite, usable R-multiple (a *scored* row must carry one of these). Kept on a
# small, spread-out grid so that across a dimension's values the per-value mean
# R-multiples both sometimes coincide (contribution near 0 -> down_weight) and
# sometimes diverge materially (contribution above threshold -> keep).
_finite_r = st.sampled_from([-3.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0])

# A non-finite / unusable R-multiple: None, NaN, or ±inf. A win/loss row carrying
# one of these is NOT a Scored_Trade.
_nonfinite_r = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)


@st.composite
def _setup_key(draw):
    """A random ``setup_key``: a structured dimension:value fingerprint, or one of
    a set of malformed / empty keys (robustness coverage)."""
    kind = draw(st.integers(min_value=0, max_value=4))
    if kind == 0:
        # Malformed / empty / degenerate keys the parser must tolerate.
        return draw(st.sampled_from(
            ["", "   ", "|", "||", "a||b", ":", ":trend", "regime", "regime:",
             "regime:unknown", "fc:aligned:strong", "x:|y:unknown|z"]
        ))
    # Structured: a random non-empty subset of dimensions, each with a random
    # value. dict() collapses duplicate dimensions deterministically. Weighted
    # toward structured keys (kinds 1-4) so the journal is rich in real
    # dimensions that clear the sample gate.
    spec = draw(st.dictionaries(
        keys=st.sampled_from(_DIMENSIONS),
        values=st.sampled_from(_VALUES),
        min_size=1,
        max_size=len(_DIMENSIONS),
    ))
    return "|".join(f"{d}:{v}" for d, v in spec.items())


_source = st.sampled_from(["backtest", "live", "LIVE", "Backtest", None, "", "paper"])


@st.composite
def _scored_row(draw):
    """A guaranteed Scored_Trade: win/loss status with a finite ``r_multiple``."""
    return {
        "setup_key": draw(_setup_key()),
        "status": draw(st.sampled_from(["win", "loss", "WIN", "Loss"])),
        "r_multiple": draw(_finite_r),
        "source": draw(_source),
        "symbol": draw(st.sampled_from(["RELIANCE", "TCS", "INFY", None])),
    }


@st.composite
def _non_scored_row(draw):
    """A guaranteed NON-scored row (non-resolving status, or unusable r_multiple)."""
    setup_key = draw(_setup_key())
    source = draw(_source)
    symbol = draw(st.sampled_from(["RELIANCE", "TCS", "INFY", None]))
    if draw(st.booleans()):
        return {
            "setup_key": setup_key,
            "status": draw(st.sampled_from(["open", "expired", "hold", "OPEN", "", "pending"])),
            "r_multiple": draw(st.one_of(_finite_r, _nonfinite_r)),
            "source": source,
            "symbol": symbol,
        }
    return {
        "setup_key": setup_key,
        "status": draw(st.sampled_from(["win", "loss", "WIN", "Loss"])),
        "r_multiple": draw(_nonfinite_r),
        "source": source,
        "symbol": symbol,
    }


@st.composite
def _journal_row(draw):
    """An arbitrary trade row: mostly scored (so the gate is cleared), some not."""
    # Bias toward scored rows so per-dimension totals routinely clear the (low)
    # sample gate and the keep/down_weight threshold branch is exercised.
    if draw(st.integers(min_value=0, max_value=3)) != 0:
        return draw(_scored_row())
    return draw(_non_scored_row())


@st.composite
def _journal(draw, min_size=0, max_size=60):
    """A random, deliberately LARGER journal: a list of arbitrary trade rows.

    Larger (relative to ``min_sample_dimension == 2``) and drawn over a small
    dimension/value pool so per-dimension scored counts routinely clear the
    sample gate, ensuring the keep/down_weight threshold branch is exercised on
    most examples.
    """
    return draw(st.lists(_journal_row(), min_size=min_size, max_size=max_size))


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (task 5.4): Recommendation threshold logic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 10: For any dimension with sufficient sample (total scored >= min_sample_dimension) and a meaningful contribution, the Recommendation is down_weight when the contribution is below contribution_threshold and keep when it is at or above the threshold.
@settings(max_examples=200, deadline=None)
@given(rows=_journal())
def test_property_10_recommendation_threshold(rows):
    """Feature: feature-attribution-pruning, Property 10: for every dimension that
    has cleared the sample gate (``total_scored >= min_sample_dimension``) AND has
    a meaningful contribution, the Recommendation is exactly ``down_weight`` iff
    its contribution is below ``contribution_threshold`` and exactly ``keep`` iff
    its contribution is at or above the threshold.

    Validates: Requirements 3.4, 3.5
    """
    report = build_attribution_report(rows, _CONFIG)

    dimensions = report["dimensions"]
    assert isinstance(dimensions, list)

    for entry in dimensions:
        total_scored = entry["total_scored"]
        contribution = entry["contribution"]
        recommendation = entry["recommendation"]

        # Only the sufficient-sample, meaningful-contribution branch is governed
        # by the threshold comparison (Requirements 3.4, 3.5).
        if total_scored >= _CONFIG.min_sample_dimension and contribution is not None:
            # Sanity: a meaningful contribution must carry its flag.
            assert entry["contribution_meaningful"] is True

            if contribution < _CONFIG.contribution_threshold:
                assert recommendation == RECOMMENDATION_DOWN_WEIGHT, (
                    f"dimension {entry.get('dimension')!r} with contribution="
                    f"{contribution} (< threshold={_CONFIG.contribution_threshold}) "
                    f"was assigned {recommendation!r}; expected "
                    f"{RECOMMENDATION_DOWN_WEIGHT!r}"
                )
                # iff: below-threshold is never keep / insufficient_sample here.
                assert recommendation != RECOMMENDATION_KEEP
                assert recommendation != RECOMMENDATION_INSUFFICIENT_SAMPLE
            else:  # contribution >= threshold
                assert recommendation == RECOMMENDATION_KEEP, (
                    f"dimension {entry.get('dimension')!r} with contribution="
                    f"{contribution} (>= threshold={_CONFIG.contribution_threshold}) "
                    f"was assigned {recommendation!r}; expected "
                    f"{RECOMMENDATION_KEEP!r}"
                )
                # iff: at/above-threshold is never down_weight / insufficient here.
                assert recommendation != RECOMMENDATION_DOWN_WEIGHT
                assert recommendation != RECOMMENDATION_INSUFFICIENT_SAMPLE
