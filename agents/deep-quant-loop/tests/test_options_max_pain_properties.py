"""Property-based tests for Max Pain over the discrete strike ladder (options.py, task 4.5).

Feature: options-analytics-engine

This Hypothesis property exercises the deterministic max-pain analytic
(:func:`options.compute_max_pain`) across arbitrary chain snapshots with a
non-empty strike ladder. It complements example-based unit tests by asserting
the universal minimization invariant the max-pain strike must satisfy:

  * Property 5 (2.3) — Max pain minimizes total intrinsic payout: for any chain
                       snapshot with a non-empty strike ladder, the strike
                       returned by ``compute_max_pain`` achieves the minimum
                       total intrinsic payout to holders over the discrete
                       ladder (the lowest strike on a tie), so no other ladder
                       strike has a strictly smaller total payout.
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
    ChainSnapshot,
    StrikeQuote,
    compute_max_pain,
)


# ── Smart generators constrained to a non-empty, well-defined strike ladder ───
# Open interest spans the realistic input space the analytic must tolerate: a
# finite non-negative number (the normal case) OR a non-finite / absent value
# (None, NaN, ±inf) which the implementation excludes from the payout sum.
_finite_oi = st.floats(
    min_value=0.0, max_value=1_000_000.0,
    allow_nan=False, allow_infinity=False,
)
_oi_value = st.one_of(
    st.none(),
    _finite_oi,
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
)

# Strike prices: distinct finite positives drawn from a realistic ladder. We pull
# a set of distinct strikes, then attach independently-generated OI to each.
_strike_value = st.floats(
    min_value=1.0, max_value=100_000.0,
    allow_nan=False, allow_infinity=False,
)


@st.composite
def _chain_snapshots(draw):
    """A ChainSnapshot with a non-empty ladder of distinct finite strikes.

    Each strike carries independently-generated CE/PE open interest spanning
    finite values and the non-finite / absent cases the analytic must exclude.
    """
    strikes = draw(
        st.lists(_strike_value, min_size=1, max_size=12, unique=True)
    )
    quotes = []
    for k in sorted(strikes):
        quotes.append(
            StrikeQuote(
                strike=k,
                ce_price=None,
                pe_price=None,
                ce_oi=draw(_oi_value),
                pe_oi=draw(_oi_value),
                ce_volume=None,
                pe_volume=None,
            )
        )
    return ChainSnapshot(
        underlying="TEST",
        expiry="2025-12-25",
        snapshot_ts=draw(st.integers(min_value=0, max_value=2_000_000_000_000)),
        strikes=tuple(quotes),
    )


def _payout(K, ladder):
    """Independently recompute total intrinsic payout to holders at settlement K.

        payout(K) = Σ_k call_OI(k)·max(0, K − k) + put_OI(k)·max(0, k − K)

    Non-finite / absent OI is excluded (treated as zero contribution), matching
    the implementation's handling (Requirement 9.3).
    """
    total = 0.0
    for q in ladder:
        k = q.strike
        if _is_finite(q.ce_oi):
            total += float(q.ce_oi) * max(0.0, K - k)
        if _is_finite(q.pe_oi):
            total += float(q.pe_oi) * max(0.0, k - K)
    return total


def _is_finite(x):
    """True iff x is a real, finite number (mirrors options._is_finite)."""
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(x)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 5 (2.3): Max pain minimizes total intrinsic payout
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 5: Max pain minimizes total intrinsic payout
@settings(max_examples=100)
@given(snapshot=_chain_snapshots())
def test_property_5_max_pain_minimizes_total_intrinsic_payout(snapshot):
    """Feature: options-analytics-engine, Property 5: Max pain minimizes total
    intrinsic payout — for any chain snapshot with a non-empty strike ladder,
    the strike returned by ``compute_max_pain`` achieves the minimum total
    intrinsic payout to holders over the discrete ladder (lowest strike on a
    tie), so no other ladder strike has a strictly smaller total payout.

    Validates: Requirements 2.3
    """
    result = compute_max_pain(snapshot)

    # The ladder is non-empty with finite strikes, so a max-pain strike exists.
    ladder = snapshot.strikes
    candidate_strikes = sorted(q.strike for q in ladder)
    assert result is not None
    assert result in candidate_strikes

    # Independently recompute the payout for every ladder strike.
    payouts = {K: _payout(K, ladder) for K in candidate_strikes}
    result_payout = payouts[result]

    # Global minimizer: no other ladder strike has a strictly smaller payout.
    min_payout = min(payouts.values())
    assert result_payout == min_payout
    for K, p in payouts.items():
        assert not (p < result_payout)

    # Lowest-strike tie-break: the returned strike is the smallest strike that
    # attains the minimum payout.
    minimizers = [K for K in candidate_strikes if payouts[K] == min_payout]
    assert result == min(minimizers)
