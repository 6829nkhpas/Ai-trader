"""Property-based tests for Black-Scholes IV round-trip and convergence (options.py, task 2.4).

Feature: options-analytics-engine

This Hypothesis property exercises the deterministic Black-Scholes core
(:func:`options.bs_price` inverted by :func:`options.bs_implied_vol`) across the
well-conditioned input space. It complements example-based unit tests by
asserting the universal round-trip invariant the IV solver must satisfy:

  * Property 1 (1.1, 1.2) — pricing at a volatility within the configured bounds
                           and then inverting that price returns a volatility
                           that is itself within bounds and re-prices to within
                           the configured tolerance.
"""

import os
import sys

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options import (  # noqa: E402
    OptionsConfig,
    bs_implied_vol,
    bs_price,
    resolve_options_config,
)

# The resolved default configuration: iv_min_vol=0.005, iv_max_vol=5.0,
# iv_tolerance=1e-6, iv_max_iterations=100. Resolution is deterministic under a
# fixed environment, so a single module-level config is safe to reuse.
_CONFIG: OptionsConfig = resolve_options_config()


# ── Smart generators constrained to a well-conditioned input space ────────────
# Option type spans the CE/PE tags the F1 chain snapshot stores (the core also
# accepts C/P/CALL/PUT, but CE/PE are the native tags the engine sees).
_option_type = st.sampled_from(["CE", "PE"])

# Spot in a realistic index range. Strike is pinned to a moderate moneyness band
# around spot (0.7x–1.3x) so the option is neither so deep ITM nor so far OTM
# that vega collapses and the price falls below tolerance — keeping the round
# trip well-conditioned per the design's bracketing solver notes.
_spot = st.floats(min_value=50.0, max_value=100_000.0,
                  allow_nan=False, allow_infinity=False)
_moneyness = st.floats(min_value=0.7, max_value=1.3,
                       allow_nan=False, allow_infinity=False)

# Time-to-expiry strictly positive: ~1 day to 2 years (in years).
_tte = st.floats(min_value=1.0 / 365.0, max_value=2.0,
                 allow_nan=False, allow_infinity=False)

# Risk-free rate within the configured [0.0, 1.0] range, kept to a realistic band.
_rate = st.floats(min_value=0.0, max_value=0.15,
                  allow_nan=False, allow_infinity=False)

# Volatility strictly inside the configured bounds [iv_min_vol, iv_max_vol], held
# a margin away from both edges so monotonicity keeps the priced value strictly
# within the solver's [bs_price(min_vol), bs_price(max_vol)] bracket.
_sigma = st.floats(min_value=0.02, max_value=2.0,
                   allow_nan=False, allow_infinity=False)


# ─────────────────────────────────────────────────────────────────────────────
# Property 1 (1.1, 1.2): Implied volatility round-trip and convergence
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 1: Implied volatility round-trip
# and convergence
@settings(max_examples=100)
@given(
    option_type=_option_type,
    spot=_spot,
    moneyness=_moneyness,
    tte=_tte,
    rate=_rate,
    sigma=_sigma,
)
def test_property_1_iv_roundtrip_and_convergence(
    option_type, spot, moneyness, tte, rate, sigma
):
    """Feature: options-analytics-engine, Property 1: Implied volatility
    round-trip and convergence — for any option type and finite spot, strike,
    positive time-to-expiry, risk-free rate, and volatility within the
    configured bounds, pricing with ``bs_price`` at that volatility and then
    inverting with ``bs_implied_vol`` returns a volatility within bounds that
    re-prices to within the configured tolerance.

    Validates: Requirements 1.1, 1.2
    """
    strike = spot * moneyness

    # Price the option at a volatility that lies strictly within the configured
    # bounds. The chosen ranges make this a finite, computable price.
    price = bs_price(option_type, spot, strike, tte, rate, sigma)
    assume(price is not None)

    # Invert that price back to an implied volatility via the bounded solver.
    implied = bs_implied_vol(option_type, spot, strike, tte, rate, price, _CONFIG)

    # The round trip must succeed: a price produced from an in-bounds sigma lies
    # within the solver's price bracket, so an in-bounds solution always exists.
    assert implied is not None

    # The returned volatility lies within the configured bounds.
    assert _CONFIG.iv_min_vol <= implied <= _CONFIG.iv_max_vol

    # Re-pricing at the implied volatility reproduces the observed price to
    # within the configured convergence tolerance.
    repriced = bs_price(option_type, spot, strike, tte, rate, implied)
    assert repriced is not None
    assert abs(repriced - price) <= _CONFIG.iv_tolerance
