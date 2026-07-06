"""Property-based test for Black-Scholes Greeks invariants (options.py, task 2.6).

Feature: options-analytics-engine

This Hypothesis property exercises the deterministic Greeks core
(:func:`options.bs_greeks`) across the valid option-parameter input space with an
in-bounds volatility. It complements the example-based reference-value unit tests
by asserting the universal Black-Scholes sign/parity invariants:

  * Property 3 (1.4) — for any valid option with an in-bounds volatility, the
                       computed Greeks satisfy: call delta in [0, 1], put delta in
                       [-1, 0], gamma and vega non-negative, and the call/put delta
                       parity ``delta_call − delta_put = 1`` holds (to tolerance)
                       for the same strike and expiry.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the options module importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options import bs_greeks, resolve_options_config  # noqa: E402

# Resolve the engine configuration once; its volatility bounds [iv_min_vol,
# iv_max_vol] define the in-bounds volatility window for this property. Under a
# clean environment these are the documented defaults (0.005 .. 5.0).
_CONFIG = resolve_options_config()

# ── Smart generators constrained to the valid option-parameter input space ────
# Finite, strictly-positive spot and strike kept to a wide but realistic band so
# the closed-form Greeks stay finite (extreme magnitudes can overflow vega/gamma
# to non-finite, which the core honestly reports as None — handled below).
_positive = st.floats(
    min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False
)
# Time-to-expiry strictly positive (years): minutes out to a few decades.
_time = st.floats(
    min_value=1e-4, max_value=30.0, allow_nan=False, allow_infinity=False
)
# Risk-free rate: any finite real in a realistic band (may be negative).
_rate = st.floats(
    min_value=-0.5, max_value=1.0, allow_nan=False, allow_infinity=False
)
# Volatility strictly within the configured bounds (the property's precondition).
_sigma = st.floats(
    min_value=_CONFIG.iv_min_vol,
    max_value=_CONFIG.iv_max_vol,
    allow_nan=False,
    allow_infinity=False,
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 3 (1.4): Greeks satisfy Black-Scholes invariants
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 3: Greeks satisfy Black-Scholes invariants
@settings(max_examples=100)
@given(S=_positive, K=_positive, T=_time, r=_rate, sigma=_sigma)
def test_property_3_greeks_satisfy_black_scholes_invariants(S, K, T, r, sigma):
    """Feature: options-analytics-engine, Property 3: Greeks satisfy
    Black-Scholes invariants — for any valid option with an in-bounds
    volatility, call delta lies in [0, 1], put delta lies in [-1, 0], gamma and
    vega are non-negative, and the call/put delta parity
    ``delta_call − delta_put = 1`` holds (to tolerance) for the same strike and
    expiry.

    Validates: Requirements 1.4
    """
    call = bs_greeks("CE", S, K, T, r, sigma)
    put = bs_greeks("PE", S, K, T, r, sigma)

    # Both legs return the four-key Greeks dict (the core never raises).
    assert set(call) == {"delta", "gamma", "theta", "vega"}
    assert set(put) == {"delta", "gamma", "theta", "vega"}

    tol = 1e-9

    # Call delta ∈ [0, 1] (finite-or-None: a non-finite component degrades to
    # None, which the core is permitted to do; the sign bound holds otherwise).
    if call["delta"] is not None:
        assert -tol <= call["delta"] <= 1.0 + tol

    # Put delta ∈ [-1, 0].
    if put["delta"] is not None:
        assert -1.0 - tol <= put["delta"] <= tol

    # Gamma and vega are non-negative on both legs (and identical across legs,
    # being type-independent in Black-Scholes).
    for greeks in (call, put):
        if greeks["gamma"] is not None:
            assert greeks["gamma"] >= -tol
        if greeks["vega"] is not None:
            assert greeks["vega"] >= -tol

    # Call/put delta parity: delta_call − delta_put = 1 for the same strike and
    # expiry, to tolerance — when both deltas are well-defined.
    if call["delta"] is not None and put["delta"] is not None:
        assert math.isclose(
            call["delta"] - put["delta"], 1.0, rel_tol=0.0, abs_tol=1e-9
        )
