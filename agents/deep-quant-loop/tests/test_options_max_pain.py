"""Unit tests for the max-pain analytic (options.py, task 4.7).

Feature: options-analytics-engine

These plain ``pytest`` unit tests anchor ``options.compute_max_pain`` against
hand-constructed chains whose max-pain strike is known by inspection
(Requirement 2.3, design "Max-pain worked example"). Max pain is the strike on
the discrete ladder that **minimizes** the total intrinsic payout to option
*holders* at a settlement of ``S = K``::

    payout(K) = Σ_k  call_OI(k)·max(0, K − k)  +  put_OI(k)·max(0, k − K)

(the per-leg payouts follow the design's mapping table — a call at ``k`` pays
``max(0, K − k)`` per unit of OI, a put at ``k`` pays ``max(0, k − K)``).

Unlike the property-based suite (Property 5), these are example-based: each
``payout(K)`` is worked out by hand in the test so the expected minimizing
strike is unambiguous. They also pin the documented tie-break (lowest strike
wins) and the degenerate-chain contract (``None`` on an empty / all-non-finite
ladder). Mirrors the import bootstrap and module-doc convention of
``test_options_config.py``.
"""

import math
import os
import sys

import pytest

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options import ChainSnapshot, StrikeQuote, compute_max_pain  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _quote(strike, ce_oi=None, pe_oi=None):
    """A StrikeQuote carrying only the fields max-pain reads (strike + OI).

    Price / volume columns are irrelevant to ``compute_max_pain`` and are left
    ``None`` so the example chains stay focused on the open-interest ladder.
    """
    return StrikeQuote(
        strike=float(strike),
        ce_price=None,
        pe_price=None,
        ce_oi=ce_oi,
        pe_oi=pe_oi,
        ce_volume=None,
        pe_volume=None,
    )


def _chain(strikes):
    """A ChainSnapshot wrapping ``strikes`` with arbitrary-but-fixed metadata."""
    return ChainSnapshot(
        underlying="ACME",
        expiry="2024-12-26",
        snapshot_ts=1_700_000_000_000,
        strikes=tuple(strikes),
    )


def _payout(chain, K):
    """Reference payout(K) = Σ_k callOI(k)·max(0,K−k) + putOI(k)·max(0,k−K).

    Independent re-implementation of the design's payout formula used purely to
    document and cross-check the by-hand expected values in the assertions.
    """
    total = 0.0
    for q in chain.strikes:
        k = q.strike
        if q.ce_oi is not None:
            total += q.ce_oi * max(0.0, K - k)
        if q.pe_oi is not None:
            total += q.pe_oi * max(0.0, k - K)
    return total


# ─────────────────────────────────────────────────────────────────────────────
# Worked example: a 3-strike chain whose max-pain strike is known by inspection
# Strikes 100 / 110 / 120 with OI:
#   100: CE 100, PE 200
#   110: CE 150, PE 150
#   120: CE 200, PE 100
# Hand-computed payouts:
#   payout(100) = PE(110)·10 + PE(120)·20 = 150·10 + 100·20 = 3500
#   payout(110) = CE(100)·10 + PE(120)·10 = 100·10 + 100·10 = 2000   <- minimum
#   payout(120) = CE(100)·20 + CE(110)·10 = 100·20 + 150·10 = 3500
# Minimum total payout is at K = 110.
# ─────────────────────────────────────────────────────────────────────────────

def test_worked_example_returns_minimizing_strike():
    """The hand-computed minimum-payout strike (110) is returned (R2.3)."""
    chain = _chain([
        _quote(100, ce_oi=100, pe_oi=200),
        _quote(110, ce_oi=150, pe_oi=150),
        _quote(120, ce_oi=200, pe_oi=100),
    ])

    assert compute_max_pain(chain) == 110.0


def test_worked_example_payouts_match_by_hand_values():
    """Each candidate's payout equals the by-hand figure, and 110 is the min."""
    chain = _chain([
        _quote(100, ce_oi=100, pe_oi=200),
        _quote(110, ce_oi=150, pe_oi=150),
        _quote(120, ce_oi=200, pe_oi=100),
    ])

    # Cross-check the worked payouts spelled out in the module comment above.
    assert _payout(chain, 100.0) == 3500.0
    assert _payout(chain, 110.0) == 2000.0
    assert _payout(chain, 120.0) == 3500.0

    payouts = {q.strike: _payout(chain, q.strike) for q in chain.strikes}
    expected_min_strike = min(payouts, key=lambda s: payouts[s])
    assert expected_min_strike == 110.0
    assert compute_max_pain(chain) == expected_min_strike


def test_strikes_given_out_of_order_still_finds_minimum():
    """Max pain is independent of input strike order (engine sorts the ladder)."""
    chain = _chain([
        _quote(120, ce_oi=200, pe_oi=100),
        _quote(100, ce_oi=100, pe_oi=200),
        _quote(110, ce_oi=150, pe_oi=150),
    ])

    assert compute_max_pain(chain) == 110.0


# ─────────────────────────────────────────────────────────────────────────────
# Tie-break: lowest strike wins
# Strikes 100 / 200 with CE(100)=50 and PE(200)=50 (all other OI zero):
#   payout(100) = PE(200)·(200−100) = 50·100 = 5000
#   payout(200) = CE(100)·(200−100) = 50·100 = 5000
# Both ladder strikes tie at 5000; the documented tie-break selects the LOWEST
# strike, so K = 100.
# ─────────────────────────────────────────────────────────────────────────────

def test_tie_breaks_toward_lowest_strike():
    """On equal minimal payout, the lowest strike is chosen (R2.3 tie-break)."""
    chain = _chain([
        _quote(100, ce_oi=50, pe_oi=0),
        _quote(200, ce_oi=0, pe_oi=50),
    ])

    # Confirm the tie really exists before asserting the tie-break direction.
    assert _payout(chain, 100.0) == 5000.0
    assert _payout(chain, 200.0) == 5000.0

    assert compute_max_pain(chain) == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Degenerate ladders → None (no fabricated strike, never raises) — R2.5, R9.3
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_ladder_returns_none():
    """An empty strike ladder yields None rather than a fabricated strike."""
    assert compute_max_pain(_chain([])) is None


def test_all_non_finite_strikes_returns_none():
    """A ladder whose every strike value is non-finite has no candidate → None."""
    chain = _chain([
        _quote(math.nan, ce_oi=100, pe_oi=100),
        _quote(math.inf, ce_oi=100, pe_oi=100),
    ])

    assert compute_max_pain(chain) is None


def test_missing_open_interest_treated_as_zero_payout():
    """With all OI absent every payout is 0 (a flat tie) → lowest strike wins."""
    chain = _chain([
        _quote(100),
        _quote(110),
        _quote(120),
    ])

    # No OI anywhere ⇒ payout(K) == 0 for every candidate ⇒ tie ⇒ lowest strike.
    assert _payout(chain, 100.0) == 0.0
    assert _payout(chain, 120.0) == 0.0
    assert compute_max_pain(chain) == 100.0
