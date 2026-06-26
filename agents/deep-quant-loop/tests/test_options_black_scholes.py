"""Unit tests for Black-Scholes reference values (options.py, task 2.7).

Feature: options-analytics-engine

These plain ``pytest`` unit tests (no Hypothesis) pin the pure Black-Scholes
core — ``options.bs_price`` and ``options.bs_greeks`` — to published textbook
reference values, covering Requirements 1.1 (correct closed-form price) and 1.4
(correct first-order Greeks).

The canonical worked example is the at-the-money, zero-rate one-year option used
throughout the options-pricing literature::

    S = K = 100,   T = 1 (year),   r = 0,   sigma = 0.20

At a zero risk-free rate the discount factor is 1 and put-call parity collapses
to ``call == put``, so the ATM call and put share the same price ≈ 7.9656. The
intermediate quantities are::

    d1 = (ln(1) + (0 + 0.20^2 / 2) * 1) / (0.20 * sqrt(1)) =  0.10
    d2 = d1 - 0.20 * sqrt(1)                                = -0.10

    Phi(0.1)  = 0.5398278...     Phi(-0.1) = 0.4601721...
    phi(0.1)  = 0.3969525...     (standard-normal density at d1)

    call = put = 100 * (Phi(0.1) - Phi(-0.1)) = 7.9655674...

The Greeks follow from the documented ``bs_greeks`` convention (no dividend
yield; theta is **per year**; vega is **per 1.0 of volatility**, i.e. NOT scaled
to a 1% move)::

    delta_call =  Phi(d1)              =  0.5398278...
    delta_put  =  Phi(d1) - 1          = -0.4601721...
    gamma      =  phi(d1) / (S*sigma*sqrt(T)) = 0.3969525 / 20 = 0.0198476...
    vega       =  S * phi(d1) * sqrt(T)       = 39.695254...
    theta_call = -S*phi(d1)*sigma/(2*sqrt(T)) - r*K*e^(-rT)*Phi(d2)
               = -3.9695254...   (the r-term vanishes at r = 0)
    theta_put  = -S*phi(d1)*sigma/(2*sqrt(T)) + r*K*e^(-rT)*Phi(-d2)
               = -3.9695254...   (equals theta_call at r = 0)

Mirrors the example-based convention of ``test_options_config.py`` (sys.path
shim so ``options`` imports cleanly; plain ``pytest`` asserts) while targeting
the deterministic numeric core rather than config resolution.
"""

import math
import os
import sys

import pytest

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options import bs_greeks, bs_price  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Canonical textbook example: S = K = 100, T = 1, r = 0, sigma = 0.20.
# Reference values recomputed from the closed form to full double precision so
# the assertions can use a tight absolute tolerance.
# ─────────────────────────────────────────────────────────────────────────────

S = 100.0
K = 100.0
T = 1.0
R = 0.0
SIGMA = 0.20

# Published / recomputed reference values for the example above.
EXPECTED_PRICE = 7.9655674554057995      # ATM call == put at r = 0
EXPECTED_DELTA_CALL = 0.5398278372770290
EXPECTED_DELTA_PUT = -0.4601721627229710
EXPECTED_GAMMA = 0.0198476273738506
EXPECTED_VEGA = 39.6952547477011810      # per 1.0 of volatility, per design
EXPECTED_THETA = -3.9695254747701181     # per year; same for call & put at r = 0

# Tolerances: prices/Greeks are pinned to a tight absolute tolerance; the
# coarser ``PUBLISHED_TOL`` matches the 4-decimal value quoted in textbooks.
ABS_TOL = 1e-9
PUBLISHED_TOL = 1e-3


# ─────────────────────────────────────────────────────────────────────────────
# Price (Requirement 1.1)
# ─────────────────────────────────────────────────────────────────────────────

def test_atm_call_price_matches_published_value():
    """ATM zero-rate call price matches the textbook value 7.9656 to tolerance."""
    price = bs_price("CE", S, K, T, R, SIGMA)
    assert price is not None
    assert price == pytest.approx(7.9656, abs=PUBLISHED_TOL)
    assert price == pytest.approx(EXPECTED_PRICE, abs=ABS_TOL)


def test_atm_put_price_matches_published_value():
    """ATM zero-rate put price matches the textbook value 7.9656 to tolerance."""
    price = bs_price("PE", S, K, T, R, SIGMA)
    assert price is not None
    assert price == pytest.approx(7.9656, abs=PUBLISHED_TOL)
    assert price == pytest.approx(EXPECTED_PRICE, abs=ABS_TOL)


def test_atm_call_equals_put_at_zero_rate():
    """At r = 0 put-call parity collapses to call == put for the ATM strike."""
    call = bs_price("CE", S, K, T, R, SIGMA)
    put = bs_price("PE", S, K, T, R, SIGMA)
    assert call is not None and put is not None
    assert call == pytest.approx(put, abs=ABS_TOL)


def test_put_call_parity_with_nonzero_rate():
    """C - P = S - K*e^(-rT) holds for a non-zero rate (parity sanity check)."""
    r = 0.05
    call = bs_price("CALL", S, K, T, r, SIGMA)
    put = bs_price("PUT", S, K, T, r, SIGMA)
    assert call is not None and put is not None
    expected_diff = S - K * math.exp(-r * T)
    assert (call - put) == pytest.approx(expected_diff, abs=1e-9)


# ─────────────────────────────────────────────────────────────────────────────
# Greeks (Requirement 1.4)
# ─────────────────────────────────────────────────────────────────────────────

def test_call_greeks_match_reference_values():
    """Call delta/gamma/theta/vega match the recomputed reference values."""
    greeks = bs_greeks("CE", S, K, T, R, SIGMA)
    assert greeks["delta"] == pytest.approx(EXPECTED_DELTA_CALL, abs=ABS_TOL)
    assert greeks["gamma"] == pytest.approx(EXPECTED_GAMMA, abs=ABS_TOL)
    assert greeks["theta"] == pytest.approx(EXPECTED_THETA, abs=ABS_TOL)
    assert greeks["vega"] == pytest.approx(EXPECTED_VEGA, abs=ABS_TOL)


def test_put_greeks_match_reference_values():
    """Put delta/gamma/theta/vega match the recomputed reference values.

    gamma and vega are identical for a call and a put at the same strike; the
    put delta is the call delta minus 1; and at r = 0 the put theta equals the
    call theta.
    """
    greeks = bs_greeks("PE", S, K, T, R, SIGMA)
    assert greeks["delta"] == pytest.approx(EXPECTED_DELTA_PUT, abs=ABS_TOL)
    assert greeks["gamma"] == pytest.approx(EXPECTED_GAMMA, abs=ABS_TOL)
    assert greeks["theta"] == pytest.approx(EXPECTED_THETA, abs=ABS_TOL)
    assert greeks["vega"] == pytest.approx(EXPECTED_VEGA, abs=ABS_TOL)


def test_atm_call_delta_near_half():
    """The ATM (slightly ITM at r=0, sigma>0) call delta is just above 0.5."""
    greeks = bs_greeks("CALL", S, K, T, R, SIGMA)
    assert greeks["delta"] == pytest.approx(0.5398, abs=PUBLISHED_TOL)


def test_delta_parity_call_minus_put_is_one():
    """delta_call - delta_put = 1 for the same strike (Black-Scholes invariant)."""
    call = bs_greeks("CE", S, K, T, R, SIGMA)
    put = bs_greeks("PE", S, K, T, R, SIGMA)
    assert (call["delta"] - put["delta"]) == pytest.approx(1.0, abs=ABS_TOL)


def test_gamma_and_vega_are_call_put_identical():
    """gamma and vega do not depend on option type (call == put)."""
    call = bs_greeks("CE", S, K, T, R, SIGMA)
    put = bs_greeks("PE", S, K, T, R, SIGMA)
    assert call["gamma"] == pytest.approx(put["gamma"], abs=ABS_TOL)
    assert call["vega"] == pytest.approx(put["vega"], abs=ABS_TOL)
