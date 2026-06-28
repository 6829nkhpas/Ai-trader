"""Unit/example tests: Weight_Map derivation exact values (attribution.py, task 8.3).

Feature: feature-attribution-pruning

Requirement 6.1: derive a per-dimension conviction Weight_Map from an
Attribution_Report by mapping each dimension's Recommendation to a conviction
weight in the half-open interval ``(0.0, 1.0]``:

  * ``keep``                -> ``1.0``                        (full weight)
  * ``down_weight``         -> ``config.down_weight_factor``  (reduced, in (0,1])
  * ``insufficient_sample`` -> ``1.0``                        (neutral / no change)

These are concrete EXAMPLE-based unit tests (NOT property tests). Each builds a
small Attribution_Report dict carrying dimensions with each Recommendation and a
hand-built ``AttributionConfig`` (the frozen dataclass) with a KNOWN
``down_weight_factor`` so every expected weight is deterministic and independent
of the process environment. ``derive_weight_map`` reads only each
Dimension_Report's ``"dimension"`` and ``"recommendation"`` keys, so the report
fixtures carry just those two fields.

The service package is made importable exactly as the sibling unit tests do
(insert the parent dir on ``sys.path``).
"""

import os
import sys

# Make the service package importable (attribution.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from attribution import (  # noqa: E402
    RECOMMENDATION_DOWN_WEIGHT,
    RECOMMENDATION_INSUFFICIENT_SAMPLE,
    RECOMMENDATION_KEEP,
    AttributionConfig,
    derive_weight_map,
)


# ── Test helpers ──────────────────────────────────────────────────────────────

def _make_config(*, down_weight_factor=0.5):
    """Build a deterministic AttributionConfig directly (frozen dataclass).

    Only ``down_weight_factor`` matters to ``derive_weight_map``; the remaining
    fields are filled with the documented defaults so the config is well-formed.
    """
    return AttributionConfig(
        min_sample_dimension=30,
        min_sample_value=10,
        contribution_threshold=0.15,
        global_min_scored=50,
        down_weight_factor=down_weight_factor,
        weight_map_enabled=False,
    )


def _report(*dimension_recommendations):
    """Build a minimal Attribution_Report dict for ``derive_weight_map``.

    Each argument is a ``(dimension, recommendation)`` pair; only the two keys
    the derivation reads are populated on each Dimension_Report entry.
    """
    return {
        "dimensions": [
            {"dimension": dim, "recommendation": rec}
            for dim, rec in dimension_recommendations
        ]
    }


# ── Exact-value example per Recommendation ────────────────────────────────────

def test_keep_maps_to_full_weight():
    """A ``keep`` dimension is given full conviction weight 1.0.

    Validates: Requirements 6.1
    """
    cfg = _make_config(down_weight_factor=0.5)
    weight_map = derive_weight_map(_report(("rs", RECOMMENDATION_KEEP)), cfg)

    assert weight_map == {"rs": 1.0}


def test_down_weight_maps_to_config_factor():
    """A ``down_weight`` dimension is given exactly ``config.down_weight_factor``.

    Validates: Requirements 6.1
    """
    cfg = _make_config(down_weight_factor=0.5)
    weight_map = derive_weight_map(_report(("db", RECOMMENDATION_DOWN_WEIGHT)), cfg)

    assert weight_map == {"db": 0.5}
    assert weight_map["db"] == cfg.down_weight_factor


def test_insufficient_sample_maps_to_neutral_weight():
    """An ``insufficient_sample`` dimension is neutral — full weight 1.0.

    Validates: Requirements 6.1
    """
    cfg = _make_config(down_weight_factor=0.5)
    weight_map = derive_weight_map(
        _report(("opt", RECOMMENDATION_INSUFFICIENT_SAMPLE)), cfg
    )

    assert weight_map == {"opt": 1.0}


# ── All three Recommendations together, exact weights ─────────────────────────

def test_all_recommendations_exact_weights():
    """keep->1.0, down_weight->factor, insufficient_sample->1.0 in one report.

    Validates: Requirements 6.1
    """
    cfg = _make_config(down_weight_factor=0.5)
    weight_map = derive_weight_map(
        _report(
            ("rs", RECOMMENDATION_KEEP),
            ("db", RECOMMENDATION_DOWN_WEIGHT),
            ("opt", RECOMMENDATION_INSUFFICIENT_SAMPLE),
        ),
        cfg,
    )

    assert weight_map == {"rs": 1.0, "db": 0.5, "opt": 1.0}
    # Every produced weight lies in the half-open interval (0.0, 1.0].
    assert all(0.0 < w <= 1.0 for w in weight_map.values())


def test_different_down_weight_factor_flows_through():
    """A different ``down_weight_factor`` (0.3) flows through to down_weight dims.

    Validates: Requirements 6.1
    """
    cfg = _make_config(down_weight_factor=0.3)
    weight_map = derive_weight_map(
        _report(
            ("rs", RECOMMENDATION_KEEP),
            ("db", RECOMMENDATION_DOWN_WEIGHT),
            ("opt", RECOMMENDATION_INSUFFICIENT_SAMPLE),
        ),
        cfg,
    )

    assert weight_map == {"rs": 1.0, "db": 0.3, "opt": 1.0}
    assert weight_map["db"] == cfg.down_weight_factor
    assert all(0.0 < w <= 1.0 for w in weight_map.values())


# ── Empty / degenerate report ─────────────────────────────────────────────────

def test_empty_dimensions_yields_empty_map():
    """A report with an empty ``dimensions`` list yields an empty map (no raise).

    Validates: Requirements 6.1
    """
    cfg = _make_config()
    assert derive_weight_map({"dimensions": []}, cfg) == {}


def test_degenerate_report_yields_empty_map():
    """A report missing ``dimensions`` (or not a dict) yields {} without raising.

    Validates: Requirements 6.1
    """
    cfg = _make_config()
    assert derive_weight_map({}, cfg) == {}
    assert derive_weight_map(None, cfg) == {}
