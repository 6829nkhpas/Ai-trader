"""Property-based test for futures basis (options.py, task 6.4).

Feature: options-analytics-engine

This Hypothesis property exercises the pure futures-basis helper
(:func:`options.compute_futures_basis`) across the full ``(future_price, spot)``
input space — finite values, ``None``, and non-finite (``NaN`` / ``±inf``) — and
asserts the universal basis invariant:

  * Property 10 (4.3) — Futures basis is future minus spot, null when no future:
                        for any spot and any future price, the basis equals
                        ``future_price - spot`` and is null **exactly** when the
                        future price is unavailable (``None`` / non-finite) or
                        the spot itself is non-finite.
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

from options import compute_futures_basis  # noqa: E402


def _is_finite_number(x):
    """Mirror of options._is_finite: a real, finite, non-bool number."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# A finite price/spot value: ordinary floats plus integer-likes, spanning a wide
# magnitude range so the finite subtraction branch is well exercised.
_finite = st.one_of(
    st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-10_000_000, max_value=10_000_000),
)

# Non-finite numeric inputs the helper must degrade to null rather than propagate.
_non_finite = st.sampled_from([float("nan"), float("inf"), float("-inf")])

# future_price spans finite / None (no near-month future) / non-finite.
_future_price = st.one_of(_finite, st.none(), _non_finite)

# spot spans finite / non-finite (signature is ``float``; non-finite must degrade).
_spot = st.one_of(_finite, _non_finite)


@settings(max_examples=100)
@given(future_price=_future_price, spot=_spot)
def test_property_10_futures_basis_is_future_minus_spot_null_when_no_future(future_price, spot):
    # Feature: options-analytics-engine, Property 10: Futures basis is future minus spot, null when no future
    """Feature: options-analytics-engine, Property 10: Futures basis is future
    minus spot, null when no future — for ANY ``future_price`` (finite, ``None``,
    or non-finite) and ANY ``spot`` (finite or non-finite),
    ``compute_futures_basis`` never raises; it returns ``None`` exactly when the
    future price is unavailable (``None`` / non-finite) or the spot is non-finite,
    and otherwise returns the finite difference ``future_price - spot``.

    Validates: Requirements 4.3
    """
    # Totality: the helper NEVER raises for any input combination.
    result = compute_futures_basis(future_price, spot)

    if future_price is None:
        # No future subscribed/stored → null basis is never fabricated.
        assert result is None
    elif not (_is_finite_number(future_price) and _is_finite_number(spot)):
        # Non-finite future or spot makes the spread undefined → null.
        assert result is None
    else:
        # Both finite → basis is the finite future-minus-spot difference.
        expected = float(future_price) - float(spot)
        assert result is not None
        assert math.isfinite(result)
        assert result == expected
