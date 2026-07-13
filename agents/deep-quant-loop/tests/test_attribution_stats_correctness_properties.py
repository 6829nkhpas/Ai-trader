"""Property-based test for statistics correctness and bounds (attribution.py, task 3.3).

Feature: feature-attribution-pruning

This module implements design **Property 4: Statistics correctness and bounds**:

    For any list of trade rows, for every reported ``Dimension_Stats``:
    ``count == wins + losses``, ``win_rate`` is ``None`` when ``count == 0`` and
    otherwise equals ``wins / (wins + losses)`` and lies in ``[0.0, 1.0]``, and
    ``expectancy_r`` is ``None`` when ``count == 0`` and otherwise equals the mean
    ``r_multiple`` of that value's scored trades.

Validates: Requirements 1.1, 1.4.

The statistic correctness is checked against an INDEPENDENT oracle that
re-derives the per-dimension/per-value tallies directly from the rows using
``parse_setup_key`` + ``is_scored_trade`` (never calling the function under
test). The implementation rounds ``win_rate`` and ``expectancy_r`` to 4 decimals
and accumulates the R-multiple sum by iterating rows in list order (and each
row's parsed ``{dimension: value}`` in insertion order); the oracle mirrors that
exact accumulation order so the rounded expectations match bit-for-bit.

The sys.path / import pattern and the ``@composite`` journal generator mirror
``tests/test_attribution_scored_only_properties.py`` (kept local to this file
for consistency).
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
    parse_setup_key,
)

# A deterministic, fixed configuration. compute_dimension_stats only consults
# ``min_sample_value`` (for the per-value weak_prior flag), which is irrelevant
# to the count/win_rate/expectancy correctness this property checks.
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
def _journal(draw, min_size=0, max_size=40):
    """A random journal: a list of arbitrary trade rows."""
    return draw(st.lists(_journal_row(), min_size=min_size, max_size=max_size))


# ── Independent oracle ────────────────────────────────────────────────────────
def _oracle_stats(rows):
    """Re-derive ``{dimension: {value: (count, wins, losses, expectancy_r)}}``
    directly from the rows using ``parse_setup_key`` + ``is_scored_trade``.

    Mirrors the implementation's accumulation order — rows are visited in list
    order and each row's parsed ``{dimension: value}`` in insertion order — so
    the floating-point R-multiple sum (and hence the 4-decimal-rounded
    ``expectancy_r``) matches the implementation bit-for-bit. This deliberately
    does NOT call ``compute_dimension_stats``.
    """
    acc = {}  # dimension -> value -> {"wins", "losses", "r_sum"}
    for row in rows:
        if not is_scored_trade(row):
            continue
        status = str(row.get("status") or "").strip().lower()
        r_multiple = float(row.get("r_multiple"))
        parsed = parse_setup_key(row.get("setup_key"))
        for dimension, value in parsed.items():
            bucket = acc.setdefault(dimension, {}).setdefault(
                value, {"wins": 0, "losses": 0, "r_sum": 0.0}
            )
            if status == "win":
                bucket["wins"] += 1
            else:
                bucket["losses"] += 1
            bucket["r_sum"] += r_multiple

    out = {}
    for dimension, values in acc.items():
        out[dimension] = {}
        for value, bucket in values.items():
            wins = bucket["wins"]
            losses = bucket["losses"]
            count = wins + losses
            win_rate = round(wins / count, 4) if count else None
            expectancy_r = round(bucket["r_sum"] / count, 4) if count else None
            out[dimension][value] = {
                "count": count,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "expectancy_r": expectancy_r,
            }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Property 4 (task 3.3): Statistics correctness and bounds
# ─────────────────────────────────────────────────────────────────────────────

# Feature: feature-attribution-pruning, Property 4: For any list of trade rows, for every reported Dimension_Stats: count == wins + losses, win_rate is None when count == 0 and otherwise equals wins/(wins+losses) and lies in [0.0, 1.0], and expectancy_r is None when count == 0 and otherwise equals the mean r_multiple of that value's scored trades.
@settings(max_examples=100, deadline=None)
@given(rows=_journal())
def test_property_4_statistics_correctness_and_bounds(rows):
    """Feature: feature-attribution-pruning, Property 4: every per-value
    Dimension_Stats satisfies count == wins + losses; win_rate is None iff
    count == 0, else wins/(wins+losses) (rounded to 4dp) in [0, 1]; expectancy_r
    is None iff count == 0, else the mean r_multiple (rounded to 4dp). Verified
    against an independent oracle re-derived from the rows.

    Validates: Requirements 1.1, 1.4
    """
    stats = compute_dimension_stats(rows, _CONFIG)
    oracle = _oracle_stats(rows)

    # The set of dimensions/values is exactly what the oracle derives.
    assert set(stats.keys()) == set(oracle.keys())

    for dimension, value_stats in stats.items():
        assert set(value_stats.keys()) == set(oracle[dimension].keys())

        for value, ds in value_stats.items():
            o = oracle[dimension][value]
            count = ds["count"]
            wins = ds["wins"]
            losses = ds["losses"]
            win_rate = ds["win_rate"]
            expectancy_r = ds["expectancy_r"]

            # ── count == wins + losses (Requirement 1.1) ──────────────────────
            assert count == wins + losses
            assert wins == o["wins"]
            assert losses == o["losses"]
            assert count == o["count"]

            # ── win_rate: None iff count == 0, else in [0,1] and exact (R1.4) ──
            if count == 0:
                assert win_rate is None
            else:
                assert win_rate is not None
                assert win_rate == round(wins / (wins + losses), 4)
                assert win_rate == o["win_rate"]
                assert 0.0 <= win_rate <= 1.0

            # ── expectancy_r: None iff count == 0, else the mean (R1.4) ────────
            if count == 0:
                assert expectancy_r is None
            else:
                assert expectancy_r is not None
                assert math.isfinite(expectancy_r)
                assert expectancy_r == o["expectancy_r"]
