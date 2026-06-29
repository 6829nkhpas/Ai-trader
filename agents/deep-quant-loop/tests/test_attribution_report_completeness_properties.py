"""Property-based test for report completeness & source split (attribution.py, task 6.4).

Feature: feature-attribution-pruning

This module implements design **Property 12: Report completeness and source split**:

    For any list of trade rows, every Dimension_Report carries its per-value
    Dimension_Stats, its contribution (and meaningfulness flag), its rank, its
    Recommendation, and its total_scored; the report carries the resolved config
    (min samples and threshold) and the overall total_scored; and for every value
    and every dimension backtest_scored + live_scored == total_scored.

Validates: Requirements 4.1, 4.3, 5.5.

``build_attribution_report`` wraps the ranked per-dimension Dimension_Report list
with report-level totals, the seeded-vs-live split, the resolved ``config`` echo,
and the sufficiency flags. This test asserts that, across arbitrary journals, the
report is STRUCTURALLY COMPLETE — every documented key is present at every level —
and that the seeded/live split reconciles to the total at both the report level
and every dimension level, and that per-value counts sum to each dimension total.

The sys.path / import pattern and the ``@composite`` journal generator mirror
``tests/test_attribution_determinism_properties.py``. The configuration is
deterministic (a single fixed ``AttributionConfig``) per the task.
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
    build_attribution_report,
)


# ── Deterministic config (fixed per the task) ─────────────────────────────────
# A single fixed configuration so the completeness property is exercised over the
# row space rather than the configuration space. Every field sits inside its
# documented range.
_CONFIG = AttributionConfig(
    min_sample_dimension=30,
    min_sample_value=10,
    contribution_threshold=0.15,
    global_min_scored=50,
    down_weight_factor=0.5,
    weight_map_enabled=False,
)


# ── Shared journal generators (local to this file) ────────────────────────────
# Real fingerprint dimensions and a small pool of values, so generated keys look
# like the journal's low-cardinality fingerprints and collide across rows
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


# Expected keys at every level of the report (design "Data Models").
_REPORT_KEYS = {
    "dimensions", "total_scored", "backtest_scored", "live_scored",
    "config", "weak_prior", "insufficient_data",
}
_CONFIG_KEYS = {
    "min_sample_dimension", "min_sample_value", "contribution_threshold",
    "global_min_scored", "down_weight_factor",
}
_DIMENSION_KEYS = {
    "dimension", "values", "total_scored", "backtest_scored", "live_scored",
    "contribution", "contribution_meaningful", "rank", "recommendation",
}
_VALUE_KEYS = {
    "value", "count", "wins", "losses", "win_rate", "expectancy_r",
    "weak_prior", "backtest_count", "live_count",
}


# ─────────────────────────────────────────────────────────────────────────────
# Property 12 (task 6.4): Report completeness and source split
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 12: For any list of trade rows, every Dimension_Report carries its per-value Dimension_Stats, its contribution (and meaningfulness flag), its rank, its Recommendation, and its total_scored; the report carries the resolved config (min samples and threshold) and the overall total_scored; and for every value and every dimension backtest_scored + live_scored == total_scored.
@settings(max_examples=200, deadline=None)
@given(rows=_journal())
def test_property_12_report_completeness_and_source_split(rows):
    """Feature: feature-attribution-pruning, Property 12: the Attribution_Report is
    structurally complete at every level, and the seeded/live split reconciles to
    the total at the report level, every dimension level, and every value level.

    Validates: Requirements 4.1, 4.3, 5.5
    """
    report = build_attribution_report(rows, _CONFIG)

    # ── Report-level shape (R4.1, R4.3) ──────────────────────────────────────
    assert isinstance(report, dict)
    assert set(report.keys()) == _REPORT_KEYS

    # Resolved-config echo carries the five documented numeric fields (R4.3).
    config_echo = report["config"]
    assert isinstance(config_echo, dict)
    assert set(config_echo.keys()) == _CONFIG_KEYS
    assert config_echo["min_sample_dimension"] == _CONFIG.min_sample_dimension
    assert config_echo["min_sample_value"] == _CONFIG.min_sample_value
    assert config_echo["contribution_threshold"] == _CONFIG.contribution_threshold
    assert config_echo["global_min_scored"] == _CONFIG.global_min_scored
    assert config_echo["down_weight_factor"] == _CONFIG.down_weight_factor

    # Report-level totals are non-negative ints and the split reconciles (R5.5).
    total_scored = report["total_scored"]
    backtest_scored = report["backtest_scored"]
    live_scored = report["live_scored"]
    assert isinstance(total_scored, int) and not isinstance(total_scored, bool)
    assert isinstance(backtest_scored, int) and not isinstance(backtest_scored, bool)
    assert isinstance(live_scored, int) and not isinstance(live_scored, bool)
    assert total_scored >= 0
    assert backtest_scored >= 0
    assert live_scored >= 0
    assert backtest_scored + live_scored == total_scored

    assert isinstance(report["weak_prior"], bool)
    assert isinstance(report["insufficient_data"], bool)

    # ── Per-dimension Dimension_Report completeness ──────────────────────────
    dimensions = report["dimensions"]
    assert isinstance(dimensions, list)
    for dim in dimensions:
        assert isinstance(dim, dict)
        assert set(dim.keys()) == _DIMENSION_KEYS

        assert isinstance(dim["dimension"], str)

        # Rank is a positive 1-based int.
        assert isinstance(dim["rank"], int) and not isinstance(dim["rank"], bool)
        assert dim["rank"] >= 1

        # Recommendation is exactly one of the three labels.
        assert dim["recommendation"] in (
            "keep", "down_weight", "insufficient_sample",
        )

        # Contribution is a float or None, with a matching meaningfulness flag.
        contribution = dim["contribution"]
        assert contribution is None or isinstance(contribution, float)
        assert isinstance(dim["contribution_meaningful"], bool)
        assert dim["contribution_meaningful"] == (contribution is not None)

        dim_total = dim["total_scored"]
        dim_backtest = dim["backtest_scored"]
        dim_live = dim["live_scored"]
        assert isinstance(dim_total, int) and not isinstance(dim_total, bool)
        assert isinstance(dim_backtest, int) and not isinstance(dim_backtest, bool)
        assert isinstance(dim_live, int) and not isinstance(dim_live, bool)

        # Per-dimension seeded/live split reconciles to the dimension total (R5.5).
        assert dim_backtest + dim_live == dim_total

        # ── Per-value Dimension_Stats completeness (R4.1) ────────────────────
        values = dim["values"]
        assert isinstance(values, list)
        assert len(values) >= 1  # a reported dimension carries at least one value

        per_value_count_sum = 0
        per_value_backtest_sum = 0
        per_value_live_sum = 0
        for stats in values:
            assert isinstance(stats, dict)
            assert set(stats.keys()) == _VALUE_KEYS

            assert isinstance(stats["value"], str)

            count = stats["count"]
            wins = stats["wins"]
            losses = stats["losses"]
            backtest_count = stats["backtest_count"]
            live_count = stats["live_count"]
            for n in (count, wins, losses, backtest_count, live_count):
                assert isinstance(n, int) and not isinstance(n, bool)
                assert n >= 0

            # win_rate / expectancy_r are floats (or None for a zero-count value).
            win_rate = stats["win_rate"]
            expectancy_r = stats["expectancy_r"]
            assert win_rate is None or isinstance(win_rate, float)
            assert expectancy_r is None or isinstance(expectancy_r, float)
            assert isinstance(stats["weak_prior"], bool)

            # Per-value count reconciles to wins/losses and the seeded/live split.
            assert wins + losses == count
            assert backtest_count + live_count == count

            per_value_count_sum += count
            per_value_backtest_sum += backtest_count
            per_value_live_sum += live_count

        # Sum of per-value counts equals the dimension total (R4.1 / R5.5).
        assert per_value_count_sum == dim_total
        assert per_value_backtest_sum == dim_backtest
        assert per_value_live_sum == dim_live

    # ── insufficient_data contract: zero scored => no dimensions ─────────────
    if report["insufficient_data"]:
        assert total_scored == 0
        assert dimensions == []
