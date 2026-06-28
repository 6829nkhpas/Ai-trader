"""Property-based test for scored-only statistics (attribution.py, task 3.2).

Feature: feature-attribution-pruning

This module implements design **Property 3: Statistics computed over
Scored_Trades only**:

    For any list of trade rows, adding or removing any number of non-scored rows
    (status open/expired/hold, or a missing/non-finite ``r_multiple``) does not
    change any per-value or per-dimension count, win-rate, or expectancy in the
    report.

Validates: Requirements 1.2.

A Scored_Trade has ``status`` in {win, loss} with a finite ``r_multiple``;
everything else (open / expired / hold, or a missing / NaN / inf ``r_multiple``)
is non-scored and must not affect the statistics.

The sys.path / import pattern mirrors
``tests/test_attribution_parse_setup_key_properties.py`` and
``tests/test_attribution_config_robustness_properties.py``. The shared
``@composite`` journal generator below assembles random trade rows (random
``setup_key`` from random dimension:value subsets plus malformed/empty keys,
random status across win/loss/open/expired/hold, random ``r_multiple`` including
None/NaN/inf, random backtest/live source) and is reused conceptually by the
other attribution property tests.
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
    compute_dimension_stats,
    is_scored_trade,
)

# A deterministic, fixed configuration. compute_dimension_stats only consults
# ``min_sample_value`` (for the per-value weak_prior flag); a fixed config keeps
# the property purely about the scored-vs-non-scored partition.
_CONFIG = AttributionConfig(
    min_sample_dimension=30,
    min_sample_value=10,
    contribution_threshold=0.15,
    global_min_scored=50,
    down_weight_factor=0.5,
    weight_map_enabled=False,
)


# ── Shared journal generators ─────────────────────────────────────────────────
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
    """A guaranteed NON-scored row, drawn from the two ways a row fails to score:

      1. status is open / expired / hold (any ``r_multiple``, even finite), or
      2. status is win / loss but ``r_multiple`` is missing / NaN / inf.
    """
    setup_key = draw(_setup_key())
    source = draw(_source)
    symbol = draw(st.sampled_from(["RELIANCE", "TCS", "INFY", None]))
    if draw(st.booleans()):
        # Kind 1: a non-resolving status (r_multiple is irrelevant to scoring).
        return {
            "setup_key": setup_key,
            "status": draw(st.sampled_from(["open", "expired", "hold", "OPEN", "", "pending"])),
            "r_multiple": draw(st.one_of(_finite_r, _nonfinite_r)),
            "source": source,
            "symbol": symbol,
        }
    # Kind 2: a resolved win/loss but an unusable r_multiple.
    return {
        "setup_key": setup_key,
        "status": draw(st.sampled_from(["win", "loss", "WIN", "Loss"])),
        "r_multiple": draw(_nonfinite_r),
        "source": source,
        "symbol": symbol,
    }


@st.composite
def _journal_row(draw):
    """An arbitrary trade row: scored OR non-scored, with the full range of keys,
    statuses, R-multiples, and sources."""
    if draw(st.booleans()):
        return draw(_scored_row())
    return draw(_non_scored_row())


@st.composite
def _journal(draw, min_size=0, max_size=40):
    """A random journal: a list of arbitrary trade rows."""
    return draw(st.lists(_journal_row(), min_size=min_size, max_size=max_size))


# ─────────────────────────────────────────────────────────────────────────────
# Property 3 (task 3.2): Statistics computed over Scored_Trades only
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 3: For any list of trade rows, adding or removing any number of non-scored rows (status open/expired/hold, or a missing/non-finite r_multiple) does not change any per-value or per-dimension count, win-rate, or expectancy in the report.
@settings(max_examples=200, deadline=None)
@given(
    base=_journal(),
    non_scored=st.lists(_non_scored_row(), max_size=30),
    seed=st.randoms(use_true_random=False),
)
def test_property_3_non_scored_rows_do_not_affect_statistics(base, non_scored, seed):
    """Feature: feature-attribution-pruning, Property 3: injecting arbitrary
    NON-scored rows (and interleaving them) leaves every per-value and
    per-dimension count, win-rate, and expectancy deep-equal.

    Validates: Requirements 1.2
    """
    # Sanity: every injected row really is non-scored, so this is a genuine test
    # of the scored-only partition and not an accidental no-op.
    assert all(not is_scored_trade(row) for row in non_scored)

    base_stats = compute_dimension_stats(base, _CONFIG)

    # Inject the non-scored rows and interleave them arbitrarily among the base
    # rows; aggregation must be insensitive to both presence and order.
    combined = list(base) + list(non_scored)
    seed.shuffle(combined)

    combined_stats = compute_dimension_stats(combined, _CONFIG)

    # Deep equality of the whole nested {dimension: {value: Dimension_Stats}}
    # mapping: counts, wins, losses, win_rate, expectancy_r, weak_prior, and the
    # backtest/live split are all unchanged by the non-scored rows.
    assert combined_stats == base_stats


# Feature: feature-attribution-pruning, Property 3: For any list of trade rows, adding or removing any number of non-scored rows (status open/expired/hold, or a missing/non-finite r_multiple) does not change any per-value or per-dimension count, win-rate, or expectancy in the report.
@settings(max_examples=100, deadline=None)
@given(rows=_journal(min_size=1))
def test_property_3_removing_non_scored_rows_is_invariant(rows):
    """Feature: feature-attribution-pruning, Property 3: REMOVING all non-scored
    rows leaves the statistics identical to those over the full journal.

    Complements the injection test from the other direction: filtering the
    journal down to its Scored_Trades only must reproduce the same statistics,
    confirming non-scored rows contribute nothing.

    Validates: Requirements 1.2
    """
    full_stats = compute_dimension_stats(rows, _CONFIG)
    scored_only = [row for row in rows if is_scored_trade(row)]
    scored_only_stats = compute_dimension_stats(scored_only, _CONFIG)

    assert scored_only_stats == full_stats
