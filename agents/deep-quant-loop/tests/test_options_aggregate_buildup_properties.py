"""Property-based tests for aggregate OI buildup (options.py, task 5.4).

Feature: options-analytics-engine

This Hypothesis property exercises the deterministic aggregate OI-buildup
analytic (:func:`options.aggregate_oi_buildup`) across arbitrary latest/prior
chain-snapshot pairs. It complements example-based unit tests by asserting the
universal invariants the aggregate label must satisfy:

  * Property 8 (3.2, 3.3) — Aggregate OI buildup is consistent and neutral
                            without a prior: for any latest+prior snapshot pair,
                            the call-side and put-side aggregate OI buildup are
                            each one of the five buildup labels derived from that
                            side's net OI and net price change over the strikes
                            the two snapshots share (matching
                            ``classify_oi_buildup`` applied to those net
                            changes); and when no prior snapshot is available,
                            every aggregate (and per-strike) buildup label is
                            ``neutral`` rather than a fabricated direction.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options import (  # noqa: E402
    BUILDUP_LONG,
    BUILDUP_LONG_UNWINDING,
    BUILDUP_NEUTRAL,
    BUILDUP_SHORT,
    BUILDUP_SHORT_COVERING,
    ChainSnapshot,
    StrikeQuote,
    aggregate_oi_buildup,
    classify_oi_buildup,
    resolve_options_config,
)


# The complete, closed set of the five buildup labels.
_ALL_LABELS = frozenset({
    BUILDUP_LONG,
    BUILDUP_SHORT,
    BUILDUP_SHORT_COVERING,
    BUILDUP_LONG_UNWINDING,
    BUILDUP_NEUTRAL,
})


# ── Smart generators constrained to well-defined chain snapshots ──────────────
# OI / price span the realistic input space the analytic must tolerate: a finite
# number (the normal case) OR a non-finite / absent value (None, NaN, ±inf) which
# the implementation excludes from the net-change sums (Requirement 9.3).
_finite_num = st.floats(
    min_value=-1_000_000.0, max_value=1_000_000.0,
    allow_nan=False, allow_infinity=False,
)
_field_value = st.one_of(
    st.none(),
    _finite_num,
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)

# A shared universe of distinct finite positive strikes. Each snapshot draws a
# subset, so the two snapshots overlap on some strikes and diverge on others.
_strike_value = st.floats(
    min_value=1.0, max_value=100_000.0,
    allow_nan=False, allow_infinity=False,
)


@st.composite
def _strike_quote(draw, strike):
    """A StrikeQuote at ``strike`` with independently-generated CE/PE fields."""
    return StrikeQuote(
        strike=strike,
        ce_price=draw(_field_value),
        pe_price=draw(_field_value),
        ce_oi=draw(_field_value),
        pe_oi=draw(_field_value),
        ce_volume=None,
        pe_volume=None,
    )


@st.composite
def _chain_snapshot(draw, ts):
    """A ChainSnapshot with an ascending, distinct-strike ladder (possibly empty)."""
    strikes = draw(st.lists(_strike_value, min_size=0, max_size=10, unique=True))
    quotes = tuple(draw(_strike_quote(k)) for k in sorted(strikes))
    return ChainSnapshot(
        underlying="TEST",
        expiry="2025-12-25",
        snapshot_ts=ts,
        strikes=quotes,
    )


@st.composite
def _snapshot_pair(draw):
    """A (latest, prior) pair of independently-generated chain snapshots."""
    latest = draw(_chain_snapshot(ts=2_000))
    prior = draw(_chain_snapshot(ts=1_000))
    return latest, prior


def _is_finite(x):
    """True iff x is a real, finite number (mirrors options._is_finite)."""
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(x)
    )


def _sum_finite(values):
    """Sum only the finite entries of ``values`` (mirrors options._sum_finite)."""
    total = 0.0
    for v in values:
        if _is_finite(v):
            total += float(v)
    return total


def _expected_net_changes(latest, prior, side):
    """Independently recompute (net ΔOI, net Δprice) over the shared strikes.

    Mirrors ``aggregate_oi_buildup``'s matching: strikes present in BOTH
    snapshots (matched by finite strike value, first prior occurrence wins),
    summing only finite OI / price values on each side (Requirement 9.3).
    Returns ``None`` when no shared strike exists (no comparison possible).
    """
    oi_attr = "ce_oi" if side == "call" else "pe_oi"
    price_attr = "ce_price" if side == "call" else "pe_price"

    prior_by_strike = {}
    for q in prior.strikes:
        if _is_finite(q.strike):
            prior_by_strike.setdefault(float(q.strike), q)

    latest_oi, prior_oi, latest_price, prior_price = [], [], [], []
    for q in latest.strikes:
        if not _is_finite(q.strike):
            continue
        match = prior_by_strike.get(float(q.strike))
        if match is None:
            continue
        latest_oi.append(getattr(q, oi_attr))
        prior_oi.append(getattr(match, oi_attr))
        latest_price.append(getattr(q, price_attr))
        prior_price.append(getattr(match, price_attr))

    if not latest_oi:
        return None
    d_oi = _sum_finite(latest_oi) - _sum_finite(prior_oi)
    d_price = _sum_finite(latest_price) - _sum_finite(prior_price)
    return d_oi, d_price


# ─────────────────────────────────────────────────────────────────────────────
# Property 8 (3.2, 3.3): Aggregate OI buildup is consistent and neutral
#                        without a prior
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 8: Aggregate OI buildup is consistent and neutral without a prior
@settings(max_examples=100)
@given(pair=_snapshot_pair())
def test_property_8_aggregate_buildup_consistent_and_neutral_without_prior(pair):
    """Feature: options-analytics-engine, Property 8: Aggregate OI buildup is
    consistent and neutral without a prior — for any latest+prior snapshot, the
    call-side and put-side aggregate OI buildup are each one of the five labels
    derived from that side's net OI and price change over the shared strikes
    (matching ``classify_oi_buildup`` of those net changes); and when no prior
    snapshot is available every aggregate buildup label is ``neutral``.

    Validates: Requirements 3.2, 3.3
    """
    latest, prior = pair
    config = resolve_options_config()

    for side in ("call", "put"):
        # ── Always one of the five buildup labels (the analytic is total). ──
        result = aggregate_oi_buildup(latest, prior, config, side)
        assert result in _ALL_LABELS

        # ── Consistency: the aggregate equals classify_oi_buildup applied to
        # the net OI / price change over the strikes the two snapshots share. ──
        expected_changes = _expected_net_changes(latest, prior, side)
        if expected_changes is None:
            # No shared strike → no comparison possible → neutral.
            assert result == BUILDUP_NEUTRAL
        else:
            d_oi, d_price = expected_changes
            assert result == classify_oi_buildup(d_oi, d_price, config)

        # ── Neutral without a prior: a missing prior snapshot never fabricates
        # a direction — the aggregate label is neutral (Requirement 3.3). ──
        assert aggregate_oi_buildup(latest, None, config, side) == BUILDUP_NEUTRAL
