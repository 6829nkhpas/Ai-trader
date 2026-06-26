"""Property-based tests for OI walls over the strike ladder (options.py, task 6.3).

Feature: options-analytics-engine

This Hypothesis property exercises the deterministic OI-wall analytic
(:func:`options.compute_oi_walls`) across arbitrary chain snapshots, finite
spots, and a varying ``oi_wall_min_oi`` threshold. It complements example-based
unit tests by asserting the universal selection invariant the resistance/support
walls must satisfy:

  * Property 9 (4.1, 4.2) — OI walls are the extreme-OI strikes on the correct
                            side of spot: for any chain snapshot and finite spot,
                            the OI-wall resistance is the strike with the greatest
                            qualifying call OI at or above spot, and support is
                            the strike with the greatest qualifying put OI at or
                            below spot; when no strike qualifies on a side, that
                            wall is null.

The documented, deterministic tie-break is honored: on an equal greatest OI the
strike nearest spot wins — the LOWEST qualifying strike for resistance (all
candidates are ``>= spot``) and the HIGHEST qualifying strike for support (all
candidates are ``<= spot``). A strike qualifies on a side only when its relevant
OI is finite and ``>= config.oi_wall_min_oi``.
"""

import dataclasses
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
    compute_oi_walls,
    resolve_options_config,
)


# ── Smart generators constrained to a well-defined strike ladder ──────────────
# Open interest spans the realistic input space the analytic must tolerate: a
# finite non-negative number (the normal case) OR a non-finite / absent value
# (None, NaN, ±inf) which the implementation excludes from wall qualification.
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

# Strike prices: distinct finite positives drawn from a realistic ladder.
_strike_value = st.floats(
    min_value=1.0, max_value=100_000.0,
    allow_nan=False, allow_infinity=False,
)

# Spot: a finite value drawn from the same range so it lands inside, at, and
# outside the ladder across examples (so each side is sometimes empty).
_spot_value = st.floats(
    min_value=1.0, max_value=100_000.0,
    allow_nan=False, allow_infinity=False,
)

# A qualifying-OI threshold spanning zero (default) and positive cutoffs, so the
# property exercises strikes being filtered out by the configured minimum.
_min_oi_value = st.floats(
    min_value=0.0, max_value=1_000_000.0,
    allow_nan=False, allow_infinity=False,
)


@st.composite
def _chain_snapshots(draw):
    """A ChainSnapshot with a ladder of distinct finite strikes.

    Each strike carries independently-generated CE/PE open interest spanning
    finite values and the non-finite / absent cases the analytic must exclude.
    """
    strikes = draw(
        st.lists(_strike_value, min_size=0, max_size=12, unique=True)
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


def _is_finite(x):
    """True iff x is a real, finite number (mirrors options._is_finite)."""
    return (
        isinstance(x, (int, float))
        and not isinstance(x, bool)
        and math.isfinite(x)
    )


def _expected_resistance(ladder, spot, min_oi):
    """Independently select the resistance wall per the documented rule.

    Resistance = the strike at/above spot (``strike >= spot``) carrying the
    greatest qualifying call OI (finite and ``>= min_oi``). On an OI tie the
    LOWEST such strike (nearest spot from above) wins; None when none qualifies.
    """
    best_strike = None
    best_oi = None
    for k in sorted(q.strike for q in ladder):  # ascending → lowest on a tie
        q = _quote_at(ladder, k)
        if k >= spot and _is_finite(q.ce_oi):
            oi = float(q.ce_oi)
            if oi >= min_oi and (best_oi is None or oi > best_oi):
                best_oi = oi
                best_strike = k
    return best_strike


def _expected_support(ladder, spot, min_oi):
    """Independently select the support wall per the documented rule.

    Support = the strike at/below spot (``strike <= spot``) carrying the
    greatest qualifying put OI (finite and ``>= min_oi``). On an OI tie the
    HIGHEST such strike (nearest spot from below) wins; None when none qualifies.
    """
    best_strike = None
    best_oi = None
    for k in sorted(q.strike for q in ladder):  # ascending; >= keeps highest on tie
        q = _quote_at(ladder, k)
        if k <= spot and _is_finite(q.pe_oi):
            oi = float(q.pe_oi)
            if oi >= min_oi and (best_oi is None or oi >= best_oi):
                best_oi = oi
                best_strike = k
    return best_strike


def _quote_at(ladder, strike):
    """Return the (unique) StrikeQuote whose strike equals ``strike``."""
    for q in ladder:
        if q.strike == strike:
            return q
    raise AssertionError("strike not found in ladder")


# ─────────────────────────────────────────────────────────────────────────────
# Property 9 (4.1, 4.2): OI walls are the extreme-OI strikes on the correct side
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 9: OI walls are the extreme-OI strikes on the correct side of spot
@settings(max_examples=100)
@given(
    snapshot=_chain_snapshots(),
    spot=_spot_value,
    min_oi=_min_oi_value,
)
def test_property_9_oi_walls_are_extreme_oi_strikes_on_correct_side(
    snapshot, spot, min_oi
):
    """Feature: options-analytics-engine, Property 9: OI walls are the extreme-OI
    strikes on the correct side of spot — for any chain snapshot and finite spot,
    the OI-wall resistance is the strike with the greatest qualifying call OI at
    or above spot, and support is the strike with the greatest qualifying put OI
    at or below spot; when no strike qualifies on a side, that wall is null.

    The documented tie-break is honored: resistance keeps the LOWEST strike on an
    OI tie (nearest spot above) and support keeps the HIGHEST strike on a tie
    (nearest spot below); the ``oi_wall_min_oi`` threshold gates qualification.

    Validates: Requirements 4.1, 4.2
    """
    config = dataclasses.replace(resolve_options_config(), oi_wall_min_oi=min_oi)

    result = compute_oi_walls(snapshot, spot, config)

    # The result is always the two-wall mapping.
    assert set(result.keys()) == {"support", "resistance"}

    ladder = snapshot.strikes
    expected_resistance = _expected_resistance(ladder, spot, min_oi)
    expected_support = _expected_support(ladder, spot, min_oi)

    assert result["resistance"] == expected_resistance
    assert result["support"] == expected_support

    # Cross-check the qualification + correct-side invariants directly.
    if result["resistance"] is not None:
        rq = _quote_at(ladder, result["resistance"])
        assert result["resistance"] >= spot          # at or above spot
        assert _is_finite(rq.ce_oi) and float(rq.ce_oi) >= min_oi
        # No qualifying call strike at/above spot carries strictly more OI.
        for q in ladder:
            if q.strike >= spot and _is_finite(q.ce_oi) and float(q.ce_oi) >= min_oi:
                assert float(q.ce_oi) <= float(rq.ce_oi)
        # Lowest strike on an OI tie.
        for q in ladder:
            if (q.strike >= spot and _is_finite(q.ce_oi)
                    and float(q.ce_oi) == float(rq.ce_oi)
                    and float(q.ce_oi) >= min_oi):
                assert q.strike >= result["resistance"]

    if result["support"] is not None:
        sq = _quote_at(ladder, result["support"])
        assert result["support"] <= spot             # at or below spot
        assert _is_finite(sq.pe_oi) and float(sq.pe_oi) >= min_oi
        # No qualifying put strike at/below spot carries strictly more OI.
        for q in ladder:
            if q.strike <= spot and _is_finite(q.pe_oi) and float(q.pe_oi) >= min_oi:
                assert float(q.pe_oi) <= float(sq.pe_oi)
        # Highest strike on an OI tie.
        for q in ladder:
            if (q.strike <= spot and _is_finite(q.pe_oi)
                    and float(q.pe_oi) == float(sq.pe_oi)
                    and float(q.pe_oi) >= min_oi):
                assert q.strike <= result["support"]
