"""Property-based test for recommendation totality (attribution.py, task 5.2).

Feature: feature-attribution-pruning

This module implements design **Property 8: Recommendation totality**:

    For any list of trade rows, every dimension present in the report is assigned
    exactly one Recommendation drawn from {keep, down_weight, insufficient_sample}.

Validates: Requirements 3.2.

``build_attribution_report`` wires the pure pipeline end to end and returns a
ranked ``report["dimensions"]`` list of Dimension_Report entries. Each entry
carries exactly one ``"recommendation"`` field, and that label must be one of
the three (and only three) Recommendation constants — ``RECOMMENDATION_KEEP``,
``RECOMMENDATION_DOWN_WEIGHT``, ``RECOMMENDATION_INSUFFICIENT_SAMPLE``. Because a
Dimension_Report has a single ``"recommendation"`` key, "exactly one" is well
defined per dimension; the property additionally asserts the assigned label is
drawn from the allowed set and that the report's dimension names are unique.

The sys.path / import pattern and the ``@composite`` journal generator mirror
``tests/test_attribution_scored_only_properties.py`` /
``tests/test_attribution_stats_correctness_properties.py`` (kept local to this
file for consistency).
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

# A deterministic, fixed configuration so the property is purely about the
# totality of the assigned Recommendation across the whole journal space.
_CONFIG = AttributionConfig(
    min_sample_dimension=30,
    min_sample_value=10,
    contribution_threshold=0.15,
    global_min_scored=50,
    down_weight_factor=0.5,
    weight_map_enabled=False,
)

# The three (and only three) allowed Recommendation labels.
_ALLOWED_RECOMMENDATIONS = frozenset(
    {
        RECOMMENDATION_KEEP,
        RECOMMENDATION_DOWN_WEIGHT,
        RECOMMENDATION_INSUFFICIENT_SAMPLE,
    }
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
def _journal(draw, min_size=0, max_size=40):
    """A random journal: a list of arbitrary trade rows."""
    return draw(st.lists(_journal_row(), min_size=min_size, max_size=max_size))


# ─────────────────────────────────────────────────────────────────────────────
# Property 8 (task 5.2): Recommendation totality
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 8: For any list of trade rows, every dimension present in the report is assigned exactly one Recommendation drawn from {keep, down_weight, insufficient_sample}.
@settings(max_examples=200, deadline=None)
@given(rows=_journal())
def test_property_8_recommendation_totality(rows):
    """Feature: feature-attribution-pruning, Property 8: every dimension in the
    report carries exactly one ``recommendation`` drawn from the three (and only
    three) allowed Recommendation labels.

    Each Dimension_Report dict has a single ``"recommendation"`` key, so "exactly
    one" recommendation is structurally guaranteed; we assert that the key is
    present and well-defined, that its value is one of
    {keep, down_weight, insufficient_sample}, and that the report's dimension
    names are unique (so each dimension is assigned its own single label).

    Validates: Requirements 3.2
    """
    report = build_attribution_report(rows, _CONFIG)

    dimensions = report["dimensions"]
    assert isinstance(dimensions, list)

    seen_names = []
    for entry in dimensions:
        # Exactly one recommendation field per dimension entry.
        assert "recommendation" in entry
        recommendation = entry["recommendation"]

        # Drawn from exactly the three allowed Recommendation constants.
        assert recommendation in _ALLOWED_RECOMMENDATIONS, (
            f"dimension {entry.get('dimension')!r} has recommendation "
            f"{recommendation!r} outside the allowed set "
            f"{sorted(_ALLOWED_RECOMMENDATIONS)}"
        )

        seen_names.append(entry["dimension"])

    # Every dimension is present once: unique names => each is assigned exactly
    # one (its own) Recommendation, so the assignment is a total function over
    # the dimensions present in the report.
    assert len(seen_names) == len(set(seen_names))
