"""Property-based test for Weight_Map derivation (attribution.py, task 8.2).

Feature: feature-attribution-pruning

This module implements design **Property 14: Weight_Map derivation**:

    For any Attribution_Report, ``derive_weight_map`` assigns ``1.0`` to every
    ``keep`` dimension, ``down_weight_factor`` to every ``down_weight``
    dimension, and ``1.0`` (neutral) to every ``insufficient_sample`` dimension,
    with all weights in ``(0.0, 1.0]``.

Validates: Requirements 6.1.

``derive_weight_map`` is a pure mapping from each Dimension_Report's
Recommendation to a conviction weight (design "Weight_Map" / AD-5):

  * ``keep``                -> ``1.0``                        (full weight)
  * ``down_weight``         -> ``config.down_weight_factor``  (reduced, in (0,1])
  * ``insufficient_sample`` -> ``1.0``                        (neutral / no change)

To exercise "for any Attribution_Report" over the whole input space, two
generators feed the property:

  1. REAL reports produced by the pure pipeline ``build_attribution_report`` over
     a random journal + random config — so the derivation is checked against the
     exact recommendation labels the report actually emits.
  2. HAND-BUILT reports carrying arbitrary ``(dimension, recommendation)`` pairs
     (including unrecognized / missing labels) — so the derivation's totality and
     the ``(0.0, 1.0]`` bound are checked directly across every recommendation
     spelling, independent of what the pipeline happens to produce.

The sys.path / import pattern and the ``@composite`` journal + config generators
mirror ``tests/test_attribution_determinism_properties.py``.
"""

import copy
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (attribution.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from attribution import (  # noqa: E402
    RECOMMENDATION_DOWN_WEIGHT,
    RECOMMENDATION_INSUFFICIENT_SAMPLE,
    RECOMMENDATION_KEEP,
    AttributionConfig,
    build_attribution_report,
    derive_weight_map,
)


# ── Random-but-valid AttributionConfig (covers "any configuration") ───────────
# Each field is drawn within its documented range; down_weight_factor spans the
# half-open interval (0.0, 1.0] so the down_weight branch is exercised across the
# whole factor space, not just the default.
@st.composite
def _config(draw):
    """A random AttributionConfig with every field inside its documented range."""
    return AttributionConfig(
        min_sample_dimension=draw(st.integers(min_value=1, max_value=200)),
        min_sample_value=draw(st.integers(min_value=1, max_value=100)),
        contribution_threshold=draw(
            st.floats(
                min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
            )
        ),
        global_min_scored=draw(st.integers(min_value=1, max_value=500)),
        down_weight_factor=draw(
            st.floats(
                min_value=0.0,
                max_value=1.0,
                exclude_min=True,  # (0.0, 1.0]
                allow_nan=False,
                allow_infinity=False,
            )
        ),
        weight_map_enabled=draw(st.booleans()),
    )


# ── Shared journal generators (mirror the determinism property test) ──────────
_DIMENSIONS = [
    "dir", "macro", "pred", "va", "regime",
    "rs", "fc", "tm", "sess", "db", "opt",
]
_VALUES = [
    "BUY", "SELL", "aligned", "below", "above",
    "trend-favorable", "leader-aligned", "strong", "weak", "morning",
    "unknown", "",
]

_finite_r = st.floats(
    min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False
)
_nonfinite_r = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)


@st.composite
def _setup_key(draw):
    """A random ``setup_key``: a structured fingerprint or a malformed/empty key."""
    kind = draw(st.integers(min_value=0, max_value=3))
    if kind == 0:
        return draw(st.sampled_from(
            ["", "   ", "|", "||", "a||b", ":", ":trend", "regime", "regime:",
             "regime:unknown", "fc:aligned:strong", "x:|y:unknown|z"]
        ))
    if kind == 1:
        return draw(st.text(max_size=40))
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
    """An arbitrary trade row: scored OR non-scored."""
    if draw(st.booleans()):
        return draw(_scored_row())
    return draw(_non_scored_row())


@st.composite
def _journal(draw, min_size=0, max_size=40):
    """A random journal: a list of arbitrary trade rows."""
    return draw(st.lists(_journal_row(), min_size=min_size, max_size=max_size))


# ── Hand-built report generator (covers "any Attribution_Report" directly) ────
# Recommendation labels span the three real spellings PLUS unrecognized/empty
# labels, so the derivation's neutral-fallback and (0,1] bound are exercised
# beyond whatever the pipeline emits.
_recommendation = st.sampled_from(
    [
        RECOMMENDATION_KEEP,
        RECOMMENDATION_DOWN_WEIGHT,
        RECOMMENDATION_INSUFFICIENT_SAMPLE,
        "unrecognized",
        "",
    ]
)


@st.composite
def _hand_report(draw):
    """A minimal Attribution_Report with arbitrary (dimension, recommendation)s.

    Dimensions are drawn from the real pool; ``dict`` collapses duplicates so each
    dimension appears once (matching how the pipeline reports one entry per
    dimension). Only the two keys ``derive_weight_map`` reads are populated.
    """
    spec = draw(st.dictionaries(
        keys=st.sampled_from(_DIMENSIONS),
        values=_recommendation,
        min_size=0,
        max_size=len(_DIMENSIONS),
    ))
    return {
        "dimensions": [
            {"dimension": dim, "recommendation": rec} for dim, rec in spec.items()
        ]
    }


# ── Shared assertion: every weight matches its recommendation and is in (0,1] ──
def _assert_weight_map_matches(report, config, weight_map):
    """Assert the Weight_Map matches the report's recommendations and is bounded."""
    dimensions = report["dimensions"]

    # One weight per reported dimension, no extras.
    assert set(weight_map) == {entry["dimension"] for entry in dimensions}

    for entry in dimensions:
        dim = entry["dimension"]
        rec = entry["recommendation"]
        weight = weight_map[dim]

        if rec == RECOMMENDATION_KEEP:
            assert weight == 1.0
        elif rec == RECOMMENDATION_DOWN_WEIGHT:
            assert weight == config.down_weight_factor
        elif rec == RECOMMENDATION_INSUFFICIENT_SAMPLE:
            assert weight == 1.0
        else:
            # Unrecognized / missing label degrades to the neutral 1.0.
            assert weight == 1.0

    # Every produced weight lies in the half-open interval (0.0, 1.0] (R6.1).
    assert all(0.0 < w <= 1.0 for w in weight_map.values())


# ─────────────────────────────────────────────────────────────────────────────
# Property 14 (task 8.2): Weight_Map derivation — over REAL pipeline reports
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 14: For any Attribution_Report, derive_weight_map assigns 1.0 to every keep dimension, down_weight_factor to every down_weight dimension, and 1.0 (neutral) to every insufficient_sample dimension, with all weights in (0.0, 1.0].
@settings(max_examples=100, deadline=None)
@given(rows=_journal(), config=_config())
def test_property_14_weight_map_over_pipeline_reports(rows, config):
    """Feature: feature-attribution-pruning, Property 14: for a report built by the
    real pipeline, every dimension's weight matches its Recommendation and lies
    in (0.0, 1.0]; the derivation is deterministic and mutates nothing.

    Validates: Requirements 6.1
    """
    report = build_attribution_report(rows, config)

    rows_before = copy.deepcopy(rows)
    report_before = copy.deepcopy(report)

    weight_map = derive_weight_map(report, config)

    _assert_weight_map_matches(report, config, weight_map)

    # Deterministic: a second derivation over identical inputs is deep-equal.
    assert derive_weight_map(report, config) == weight_map

    # Pure: neither the rows nor the report were mutated (R6.1 / purity).
    assert rows == rows_before
    assert report == report_before


# ─────────────────────────────────────────────────────────────────────────────
# Property 14 (task 8.2): Weight_Map derivation — over ARBITRARY reports
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 14: For any Attribution_Report, derive_weight_map assigns 1.0 to every keep dimension, down_weight_factor to every down_weight dimension, and 1.0 (neutral) to every insufficient_sample dimension, with all weights in (0.0, 1.0].
@settings(max_examples=100, deadline=None)
@given(report=_hand_report(), config=_config())
def test_property_14_weight_map_over_arbitrary_reports(report, config):
    """Feature: feature-attribution-pruning, Property 14: for an arbitrary report
    carrying any recommendation spelling, keep->1.0, down_weight->factor,
    insufficient_sample->1.0, and any other label degrades to the neutral 1.0 —
    with every weight in (0.0, 1.0].

    Validates: Requirements 6.1
    """
    report_before = copy.deepcopy(report)

    weight_map = derive_weight_map(report, config)

    _assert_weight_map_matches(report, config, weight_map)

    # Deterministic and non-mutating over the arbitrary report as well.
    assert derive_weight_map(report, config) == weight_map
    assert report == report_before
