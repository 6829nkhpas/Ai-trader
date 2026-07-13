"""Property-based test for statistical honesty (attribution.py, task 5.3).

Feature: feature-attribution-pruning

This module implements design **Property 9: Statistical honesty (never prune on
noise)**:

    For any list of trade rows, every dimension whose total scored-trade count is
    below the configured ``min_sample_dimension`` is assigned
    ``insufficient_sample`` and is never assigned ``down_weight`` or ``keep``.

Validates: Requirements 3.3, 5.1.

``build_attribution_report`` wires the pure pipeline end to end and returns a
ranked ``report["dimensions"]`` list of Dimension_Report entries. Each entry
carries a ``"total_scored"`` (the Σ of its per-value scored counts) and exactly
one ``"recommendation"`` label. Design AD-3 enforces statistical honesty
*structurally*: the recommendation control flow makes
``RECOMMENDATION_INSUFFICIENT_SAMPLE`` the ONLY reachable outcome for a dimension
whose ``total_scored`` is below ``min_sample_dimension`` — ``down_weight`` and
``keep`` are unreachable in that branch. This property exercises that guarantee
across the whole journal space: for every dimension below the sample gate, the
recommendation must be exactly ``insufficient_sample`` and must never be
``down_weight`` or ``keep``.

To make the below-sample branch actually fire across most examples, this test
uses a deterministic config with a moderately high ``min_sample_dimension`` and
generates small journals, so the per-dimension scored counts routinely fall
below the gate.

The sys.path / import pattern and the ``@composite`` journal generator mirror
``tests/test_attribution_recommendation_totality_properties.py`` /
``tests/test_attribution_scored_only_properties.py`` (kept local to this file
for consistency).
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

# A deterministic, fixed configuration. ``min_sample_dimension`` is set
# moderately high (30) relative to the small journals generated below, so most
# dimensions fall BELOW the sample gate and the honesty branch is exercised on
# the bulk of examples (the property still holds for the rare dimension that
# clears the gate — it simply has no below-sample obligation to check).
_CONFIG = AttributionConfig(
    min_sample_dimension=30,
    min_sample_value=10,
    contribution_threshold=0.15,
    global_min_scored=50,
    down_weight_factor=0.5,
    weight_map_enabled=False,
)


# ── Shared journal generators (local to this file) ────────────────────────────
# The real fingerprint dimensions and a small pool of values, so generated keys
# look like the journal's low-cardinality fingerprints and collide across rows
# (exercising real per-value aggregation rather than all-singleton values).
_DIMENSIONS = [
    "dir", "macro", "pred", "va", "regime",
    "rs", "fc", "tm", "sess", "db", "opt",
]
_VALUES = [
    "BUY", "SELL", "aligned", "below", "above",
    "trend-favorable", "leader-aligned", "strong", "weak", "morning",
    "unknown", "",
]

# A finite, usable R-multiple (a *scored* row must carry one of these).
_finite_r = st.floats(
    min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False
)

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
    kind = draw(st.integers(min_value=0, max_value=3))
    if kind == 0:
        # Malformed / empty / degenerate keys the parser must tolerate.
        return draw(st.sampled_from(
            ["", "   ", "|", "||", "a||b", ":", ":trend", "regime", "regime:",
             "regime:unknown", "fc:aligned:strong", "x:|y:unknown|z"]
        ))
    if kind == 1:
        # Wholly arbitrary text.
        return draw(st.text(max_size=40))
    # Structured: a random non-empty subset of dimensions, each with a random
    # value. dict() collapses duplicate dimensions deterministically.
    spec = draw(st.dictionaries(
        keys=st.sampled_from(_DIMENSIONS),
        values=st.sampled_from(_VALUES),
        min_size=1,
        max_size=6,
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
    """An arbitrary trade row: scored OR non-scored, full range of keys/statuses."""
    if draw(st.booleans()):
        return draw(_scored_row())
    return draw(_non_scored_row())


@st.composite
def _journal(draw, min_size=0, max_size=20):
    """A random, deliberately SMALL journal: a list of arbitrary trade rows.

    Kept small (relative to ``min_sample_dimension == 30``) so per-dimension
    scored counts routinely fall below the sample gate, ensuring the honesty
    branch is exercised on most examples.
    """
    return draw(st.lists(_journal_row(), min_size=min_size, max_size=max_size))


# ─────────────────────────────────────────────────────────────────────────────
# Property 9 (task 5.3): Statistical honesty (never prune on noise)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 9: For any list of trade rows, every dimension whose total scored-trade count is below the configured min_sample_dimension is assigned insufficient_sample and is never assigned down_weight or keep.
@settings(max_examples=100, deadline=None)
@given(rows=_journal())
def test_property_9_statistical_honesty(rows):
    """Feature: feature-attribution-pruning, Property 9: every dimension whose
    ``total_scored`` is below ``min_sample_dimension`` is assigned
    ``insufficient_sample`` and is NEVER assigned ``down_weight`` or ``keep``.

    Design AD-3 makes ``insufficient_sample`` the only reachable recommendation
    below the dimension sample gate, so a feature is never pruned (down-weighted)
    or kept on a sample too small to be meaningful.

    Validates: Requirements 3.3, 5.1
    """
    report = build_attribution_report(rows, _CONFIG)

    dimensions = report["dimensions"]
    assert isinstance(dimensions, list)

    for entry in dimensions:
        total_scored = entry["total_scored"]
        recommendation = entry["recommendation"]

        if total_scored < _CONFIG.min_sample_dimension:
            assert recommendation == RECOMMENDATION_INSUFFICIENT_SAMPLE, (
                f"dimension {entry.get('dimension')!r} with total_scored="
                f"{total_scored} (< min_sample_dimension="
                f"{_CONFIG.min_sample_dimension}) was assigned {recommendation!r}; "
                f"expected {RECOMMENDATION_INSUFFICIENT_SAMPLE!r}"
            )
            # Explicitly: never pruned (down_weight) or kept on too small a sample.
            assert recommendation != RECOMMENDATION_DOWN_WEIGHT
            assert recommendation != RECOMMENDATION_KEEP
