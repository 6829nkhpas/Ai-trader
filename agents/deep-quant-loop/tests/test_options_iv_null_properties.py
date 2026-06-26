"""Property-based tests for null IV/Greeks when unsolvable (options.py, task 2.5).

Feature: options-analytics-engine

These Hypothesis properties exercise the deterministic Black-Scholes core
(:func:`options.bs_implied_vol` and :func:`options.bs_greeks`) across the
*unsolvable* input space — the region where no in-bounds implied volatility
exists. They complement the round-trip property (Property 1) by asserting the
honest-degradation invariant the engine must satisfy:

  * Property 2 (1.3, 1.5) — for any option whose price is strictly below
                            intrinsic value or strictly above the no-arbitrage
                            upper bound, or whose time-to-expiry is zero or
                            negative, ``bs_implied_vol`` returns null and every
                            Greek is null — never raising, never fabricating.
"""

import math
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
    bs_greeks,
    bs_implied_vol,
    resolve_options_config,
)

# The resolved default configuration: iv_min_vol=0.005, iv_max_vol=5.0,
# iv_tolerance=1e-6, iv_max_iterations=100. Resolution is deterministic under a
# fixed environment, so a single module-level config is safe to reuse.
_CONFIG: OptionsConfig = resolve_options_config()

_GREEK_KEYS = ("delta", "gamma", "theta", "vega")


# ── Smart generators constrained to the unsolvable input space ────────────────
# Option type spans the CE/PE tags the F1 chain snapshot stores.
_option_type = st.sampled_from(["CE", "PE"])

# Spot in a realistic index range; strike pinned to a moderate moneyness band so
# the no-arbitrage bounds below are well-defined finite numbers.
_spot = st.floats(min_value=50.0, max_value=100_000.0,
                  allow_nan=False, allow_infinity=False)
_moneyness = st.floats(min_value=0.5, max_value=1.5,
                       allow_nan=False, allow_infinity=False)

# Risk-free rate within the configured [0.0, 1.0] range, kept to a realistic band.
_rate = st.floats(min_value=0.0, max_value=0.15,
                  allow_nan=False, allow_infinity=False)

# Strictly positive time-to-expiry (~1 day to 2 years) for the unsolvable-price
# property, where T is valid but the *price* admits no in-bounds solution.
_tte_pos = st.floats(min_value=1.0 / 365.0, max_value=2.0,
                     allow_nan=False, allow_infinity=False)

# Time-to-expiry that is zero or negative — the degenerate-time region.
_tte_nonpos = st.floats(min_value=-100.0, max_value=0.0,
                        allow_nan=False, allow_infinity=False)

# A finite margin (well above the default 1e-6 tolerance) used to push a price
# strictly outside the no-arbitrage bracket.
_margin = st.floats(min_value=1.0, max_value=10_000.0,
                    allow_nan=False, allow_infinity=False)

# Which side of the bracket to violate: below intrinsic / above the no-arb ceiling.
_side = st.sampled_from(["below_intrinsic", "above_noarb"])

# An arbitrary in-bounds volatility to hand to ``bs_greeks`` for the T<=0 case,
# proving that even a perfectly valid sigma cannot produce Greeks when T<=0.
_sigma_valid = st.floats(min_value=0.02, max_value=2.0,
                         allow_nan=False, allow_infinity=False)


def _no_arb_bounds(option_type, S, K, T, r):
    """Return the (lower, upper) no-arbitrage price bounds for a European option.

    Call:  lower = max(0, S - K*e^{-rT}),  upper = S
    Put:   lower = max(0, K*e^{-rT} - S),  upper = K*e^{-rT}

    A price strictly below ``lower`` is below intrinsic value; a price strictly
    above ``upper`` is above the no-arbitrage ceiling. Both lie outside the
    solver's [bs_price(min_vol), bs_price(max_vol)] bracket (since min_vol>0 and
    max_vol is finite), so the solver must return null for either.
    """
    discount = math.exp(-r * T)
    if option_type in ("CE", "C", "CALL"):
        return max(0.0, S - K * discount), S
    return max(0.0, K * discount - S), K * discount


def _assert_all_greeks_null(greeks):
    """Assert the Greeks dict has every key present and every value ``None``."""
    assert set(greeks.keys()) == set(_GREEK_KEYS)
    for key in _GREEK_KEYS:
        assert greeks[key] is None, f"expected null {key}, got {greeks[key]!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Property 2 (1.3): Price outside the no-arbitrage bracket → null IV and Greeks
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 2: IV and Greeks are null when
# unsolvable or non-positive time
@settings(max_examples=100)
@given(
    option_type=_option_type,
    spot=_spot,
    moneyness=_moneyness,
    tte=_tte_pos,
    rate=_rate,
    margin=_margin,
    side=_side,
)
def test_property_2_unsolvable_price_yields_null_iv_and_greeks(
    option_type, spot, moneyness, tte, rate, margin, side
):
    """Feature: options-analytics-engine, Property 2: IV and Greeks are null when
    unsolvable or non-positive time — for any option whose price is strictly
    below intrinsic value or strictly above the no-arbitrage upper bound,
    ``bs_implied_vol`` returns null and every Greek (computed from that null IV)
    is null, never raising and never fabricating a value.

    Validates: Requirements 1.3, 1.5
    """
    strike = spot * moneyness
    lower, upper = _no_arb_bounds(option_type, spot, strike, tte, rate)

    if side == "below_intrinsic":
        # Strictly below intrinsic value (and below the solver's lower bracket,
        # which sits at or above this bound for any min_vol > 0). May be negative.
        price = lower - margin
    else:
        # Strictly above the no-arbitrage ceiling (and above the solver's upper
        # bracket, which sits at or below this bound for any finite max_vol).
        price = upper + margin

    assume(math.isfinite(price))

    # The IV solver must report no in-bounds solution (null), not raise or clamp.
    implied = bs_implied_vol(option_type, spot, strike, tte, rate, price, _CONFIG)
    assert implied is None

    # Greeks computed from the null IV are all null — never raising, never
    # fabricating a sensitivity for an option with no defined volatility.
    greeks = bs_greeks(option_type, spot, strike, tte, rate, implied)
    _assert_all_greeks_null(greeks)


# ─────────────────────────────────────────────────────────────────────────────
# Property 2 (1.5): Zero or negative time-to-expiry → null IV and Greeks
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 2: IV and Greeks are null when
# unsolvable or non-positive time
@settings(max_examples=100)
@given(
    option_type=_option_type,
    spot=_spot,
    moneyness=_moneyness,
    tte=_tte_nonpos,
    rate=_rate,
    price=st.floats(min_value=0.0, max_value=100_000.0,
                    allow_nan=False, allow_infinity=False),
    sigma=_sigma_valid,
)
def test_property_2_nonpositive_time_yields_null_iv_and_greeks(
    option_type, spot, moneyness, tte, rate, price, sigma
):
    """Feature: options-analytics-engine, Property 2: IV and Greeks are null when
    unsolvable or non-positive time — for any option whose time-to-expiry is
    zero or negative, ``bs_implied_vol`` returns null for any observed price, and
    ``bs_greeks`` returns all-null Greeks even when handed a perfectly valid
    in-bounds volatility, never raising and never fabricating a value.

    Validates: Requirements 1.3, 1.5
    """
    strike = spot * moneyness

    # No implied volatility is defined once time-to-expiry is non-positive: the
    # bracket prices cannot be formed, so the solver returns null for any price.
    implied = bs_implied_vol(option_type, spot, strike, tte, rate, price, _CONFIG)
    assert implied is None

    # Greeks are all null from the null IV ...
    _assert_all_greeks_null(bs_greeks(option_type, spot, strike, tte, rate, implied))

    # ... and remain all null even when a valid in-bounds sigma is supplied,
    # because T <= 0 leaves every Greek undefined.
    _assert_all_greeks_null(bs_greeks(option_type, spot, strike, tte, rate, sigma))
