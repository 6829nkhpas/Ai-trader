"""Unit/example tests: ranking, recommendation & threshold boundaries (attribution.py, task 5.6).

Feature: feature-attribution-pruning

Requirement 3.1: rank the Fingerprint_Dimensions by Contribution_Metric, highest
first.
Requirement 3.4: a dimension with sufficient sample whose Contribution_Metric is
BELOW the configured contribution threshold is assigned ``down_weight``.
Requirement 3.5: a dimension with sufficient sample whose Contribution_Metric
MEETS (is at or above) the threshold is assigned ``keep``.
(Requirement 3.3 / 5.1 — never prune on a sample too small — and Requirement 5.2
— per-value weak-prior flag — are exercised by the boundary cases below as a
side effect of the ``min_sample_dimension`` / ``min_sample_value`` straddles.)

These are concrete EXAMPLE-based unit tests (NOT property tests). They are built
on a hand-crafted, fully in-memory journal of plain trade-row dicts and a
hand-built ``AttributionConfig`` (the frozen dataclass) so every threshold is
deterministic and independent of the process environment.

The headline case is a *worked separation scenario*: the ``rs`` dimension's two
values separate realized expectancy sharply (a winning leader-aligned value vs a
losing laggard value) while the ``db`` dimension's values perform identically (no
separation). With both dimensions clearing ``min_sample_dimension`` we assert
``rs`` ranks above ``db``, ``rs`` -> ``keep`` and ``db`` -> ``down_weight``.

The remaining cases straddle each gate one step BELOW / AT / ABOVE the boundary:
``min_sample_dimension`` (insufficient_sample -> rated), ``min_sample_value``
(per-value weak_prior True -> False), and ``contribution_threshold``
(down_weight -> keep). Every expected Contribution_Metric is hand-derived from
the sample-weighted-stddev formula in ``compute_contribution`` and checked
exactly.

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
    build_attribution_report,
    compute_dimension_stats,
)


# ── Test helpers ──────────────────────────────────────────────────────────────

def _row(setup_key, status, r_multiple, source="live"):
    """Build one plain in-memory trade row (a Scored_Trade when status is win/loss)."""
    return {
        "setup_key": setup_key,
        "status": status,
        "r_multiple": r_multiple,
        "source": source,
    }


def _make_config(
    *,
    min_sample_dimension=30,
    min_sample_value=10,
    contribution_threshold=0.15,
    global_min_scored=30,
    down_weight_factor=0.5,
    weight_map_enabled=False,
):
    """Build a deterministic AttributionConfig directly (frozen dataclass)."""
    return AttributionConfig(
        min_sample_dimension=min_sample_dimension,
        min_sample_value=min_sample_value,
        contribution_threshold=contribution_threshold,
        global_min_scored=global_min_scored,
        down_weight_factor=down_weight_factor,
        weight_map_enabled=weight_map_enabled,
    )


def _dimension(report, name):
    """Return the Dimension_Report entry for ``name`` from a built report."""
    for entry in report["dimensions"]:
        if entry["dimension"] == name:
            return entry
    raise AssertionError(f"dimension {name!r} not in report: "
                         f"{[d['dimension'] for d in report['dimensions']]}")


# ── Worked separation scenario: rs separates outcomes, db does not ────────────
#
# 40 scored trades, each carrying BOTH an ``rs`` tag and a ``db`` tag:
#   * 20 rs:leader-aligned rows — all WINS at +2.0R
#   * 20 rs:laggard rows       — all LOSSES at -1.0R
# The ``db`` value is interleaved so each db value sees an identical mix:
#   * db:alpha — 10 leaders (+2.0) + 10 laggards (-1.0) -> expectancy 0.5
#   * db:beta  — 10 leaders (+2.0) + 10 laggards (-1.0) -> expectancy 0.5
#
# rs contribution (sample-weighted stddev of value expectancy):
#   mu = (20*2.0 + 20*-1.0)/40 = 0.5
#   var = (20*(2.0-0.5)^2 + 20*(-1.0-0.5)^2)/40 = (45 + 45)/40 = 2.25
#   contrib_rs = sqrt(2.25) = 1.5     (>> threshold 0.15 -> keep)
# db contribution: both values share expectancy 0.5 -> var 0 -> contrib 0.0
#   (0.0 < 0.15 -> down_weight); both dims have 40 scored >= min_sample 30.

def _worked_scenario_rows():
    rows = []
    for i in range(20):  # rs:leader-aligned — winners
        db = "alpha" if i < 10 else "beta"
        rows.append(_row(f"rs:leader-aligned|db:{db}", "win", 2.0))
    for i in range(20):  # rs:laggard — losers
        db = "alpha" if i < 10 else "beta"
        rows.append(_row(f"rs:laggard|db:{db}", "loss", -1.0))
    return rows


def test_worked_scenario_rs_ranks_above_db():
    """rs separates expectancy (contrib 1.5) and outranks the flat db (contrib 0.0).

    Validates: Requirements 3.1
    """
    cfg = _make_config()
    report = build_attribution_report(_worked_scenario_rows(), cfg)

    rs = _dimension(report, "rs")
    db = _dimension(report, "db")

    # Higher contribution => lower (better) rank number.
    assert rs["contribution"] == 1.5
    assert db["contribution"] == 0.0
    assert rs["rank"] < db["rank"]
    assert rs["rank"] == 1
    assert db["rank"] == 2


def test_worked_scenario_rs_keep_db_down_weight():
    """The separating dimension is kept; the non-separating dimension is down-weighted.

    Validates: Requirements 3.4, 3.5
    """
    cfg = _make_config()
    report = build_attribution_report(_worked_scenario_rows(), cfg)

    assert _dimension(report, "rs")["recommendation"] == RECOMMENDATION_KEEP
    assert _dimension(report, "db")["recommendation"] == RECOMMENDATION_DOWN_WEIGHT


# ── Boundary: min_sample_dimension (below -> insufficient_sample; at/above rated) ─
#
# A single dimension ``x`` with two strongly-separating values (winners at +2.0,
# losers at -1.0). The dimension's TOTAL scored count straddles
# min_sample_dimension=30. Below the gate the contribution is reported
# not-meaningful (None) and the recommendation is insufficient_sample; at/above
# the gate the contribution is computed (1.5 at exactly 30) and the dimension is
# rated (keep here, since 1.5 >> threshold 0.15).

def _single_dim_x_rows(total):
    """``total`` scored rows on dimension x, split into hi (wins) / lo (losses)."""
    hi = total - total // 2  # ceil(total/2)
    lo = total // 2
    rows = [_row("x:hi", "win", 2.0) for _ in range(hi)]
    rows += [_row("x:lo", "loss", -1.0) for _ in range(lo)]
    return rows


def test_min_sample_dimension_below_is_insufficient_sample():
    """29 < min_sample_dimension(30): contribution not-meaningful, insufficient_sample.

    Validates: Requirements 3.3
    """
    cfg = _make_config(min_sample_dimension=30)
    report = build_attribution_report(_single_dim_x_rows(29), cfg)
    x = _dimension(report, "x")

    assert x["total_scored"] == 29
    assert x["contribution"] is None
    assert x["contribution_meaningful"] is False
    assert x["recommendation"] == RECOMMENDATION_INSUFFICIENT_SAMPLE


def test_min_sample_dimension_at_boundary_is_rated():
    """30 == min_sample_dimension(30): contribution computed (1.5) and dimension rated.

    Validates: Requirements 3.1, 3.5
    """
    cfg = _make_config(min_sample_dimension=30)
    report = build_attribution_report(_single_dim_x_rows(30), cfg)
    x = _dimension(report, "x")

    assert x["total_scored"] == 30
    assert x["contribution"] == 1.5
    assert x["contribution_meaningful"] is True
    assert x["recommendation"] != RECOMMENDATION_INSUFFICIENT_SAMPLE
    assert x["recommendation"] == RECOMMENDATION_KEEP


def test_min_sample_dimension_above_boundary_is_rated():
    """31 > min_sample_dimension(30): contribution meaningful and dimension rated.

    Validates: Requirements 3.1, 3.5
    """
    cfg = _make_config(min_sample_dimension=30)
    report = build_attribution_report(_single_dim_x_rows(31), cfg)
    x = _dimension(report, "x")

    assert x["total_scored"] == 31
    assert x["contribution"] is not None
    assert x["contribution_meaningful"] is True
    assert x["recommendation"] != RECOMMENDATION_INSUFFICIENT_SAMPLE
    assert x["recommendation"] == RECOMMENDATION_KEEP


# ── Boundary: min_sample_value (below -> weak_prior True; at/above -> False) ──
#
# The per-value weak_prior flag flips at min_sample_value=10. A single value's
# scored count straddles the gate; we read the flag straight off Dimension_Stats
# (min_sample_dimension is set to 1 so it never interferes with this gate).

def _single_value_rows(count):
    """``count`` scored wins for dimension d, value v."""
    return [_row("d:v", "win", 1.0) for _ in range(count)]


def test_min_sample_value_below_is_weak_prior():
    """9 < min_sample_value(10): the value's stats are flagged a weak prior.

    Validates: Requirements 5.2
    """
    cfg = _make_config(min_sample_dimension=1, min_sample_value=10, global_min_scored=1)
    stats = compute_dimension_stats(_single_value_rows(9), cfg)

    assert stats["d"]["v"]["count"] == 9
    assert stats["d"]["v"]["weak_prior"] is True


def test_min_sample_value_at_boundary_not_weak_prior():
    """10 == min_sample_value(10): the value clears the per-value gate.

    Validates: Requirements 5.2
    """
    cfg = _make_config(min_sample_dimension=1, min_sample_value=10, global_min_scored=1)
    stats = compute_dimension_stats(_single_value_rows(10), cfg)

    assert stats["d"]["v"]["count"] == 10
    assert stats["d"]["v"]["weak_prior"] is False


def test_min_sample_value_above_boundary_not_weak_prior():
    """11 > min_sample_value(10): the value is not a weak prior.

    Validates: Requirements 5.2
    """
    cfg = _make_config(min_sample_dimension=1, min_sample_value=10, global_min_scored=1)
    stats = compute_dimension_stats(_single_value_rows(11), cfg)

    assert stats["d"]["v"]["count"] == 11
    assert stats["d"]["v"]["weak_prior"] is False


# ── Boundary: contribution_threshold (below -> down_weight; at/above -> keep) ─
#
# A dimension ``t`` with two EQUAL-count values (15 each, total 30 >= sample 30)
# whose expectancies are symmetric about 0 (+X wins / -X losses). For two equal
# samples the sample-weighted stddev collapses to the half-gap |X|, so the
# contribution equals exactly X. With contribution_threshold=0.5:
#   * X = 0.4 -> contribution 0.4  (< 0.5  -> down_weight)
#   * X = 0.5 -> contribution 0.5  (== 0.5 -> keep, threshold is inclusive)
#   * X = 0.6 -> contribution 0.6  (> 0.5  -> keep)

def _symmetric_dim_rows(x):
    """Dimension t: 15 wins at +x and 15 losses at -x (contribution == x)."""
    rows = [_row("t:pos", "win", x) for _ in range(15)]
    rows += [_row("t:neg", "loss", -x) for _ in range(15)]
    return rows


def test_contribution_threshold_below_is_down_weight():
    """contribution 0.4 < threshold 0.5 (sufficient sample) -> down_weight.

    Validates: Requirements 3.4
    """
    cfg = _make_config(min_sample_dimension=30, contribution_threshold=0.5)
    report = build_attribution_report(_symmetric_dim_rows(0.4), cfg)
    t = _dimension(report, "t")

    assert t["total_scored"] == 30
    assert t["contribution"] == 0.4
    assert t["recommendation"] == RECOMMENDATION_DOWN_WEIGHT


def test_contribution_threshold_at_boundary_is_keep():
    """contribution 0.5 == threshold 0.5 (inclusive) -> keep.

    Validates: Requirements 3.5
    """
    cfg = _make_config(min_sample_dimension=30, contribution_threshold=0.5)
    report = build_attribution_report(_symmetric_dim_rows(0.5), cfg)
    t = _dimension(report, "t")

    assert t["total_scored"] == 30
    assert t["contribution"] == 0.5
    assert t["recommendation"] == RECOMMENDATION_KEEP


def test_contribution_threshold_above_boundary_is_keep():
    """contribution 0.6 > threshold 0.5 -> keep.

    Validates: Requirements 3.5
    """
    cfg = _make_config(min_sample_dimension=30, contribution_threshold=0.5)
    report = build_attribution_report(_symmetric_dim_rows(0.6), cfg)
    t = _dimension(report, "t")

    assert t["total_scored"] == 30
    assert t["contribution"] == 0.6
    assert t["recommendation"] == RECOMMENDATION_KEEP
