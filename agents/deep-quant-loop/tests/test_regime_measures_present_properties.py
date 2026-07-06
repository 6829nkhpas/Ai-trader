"""Property-based test for present, finite-or-null measures (regime.py, task 3.4).

Feature: regime-detection-gate

This Hypothesis property exercises ``classify_regime`` from ``regime.py``. It
covers design Property 3: for any candle sequence containing at least the
largest configured lookback of valid candles, the resulting Regime_Label
includes each named Regime_Measure (directional_strength, choppiness,
efficiency_ratio, atr_percentile, bb_width), and each measure is either a finite
number or ``null`` (None).

Candles are dict OHLCV records with keys ``open`` / ``high`` / ``low`` /
``close`` / ``volume`` (matching how ``regime.py`` reads candles via
``c.get(...)``). The generator below produces a random price walk of more than
the default largest configured lookback of valid candles, so ``classify_regime``
reliably returns a Regime_Label (not an Unavailable_Marker) and the
measures-present guarantee is exercised across varied, realistic price paths.
"""

import math
import os
import sys

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from regime import (  # noqa: E402
    REGIME_MEASURE_FIELDS,
    classify_regime,
    resolve_regime_config,
)

# The default resolved config gates on ``max(min_candles=50, largest_lookback)``
# where ``largest_lookback = vol_period + vol_pctl_window = 14 + 100 = 114``.
# Generate comfortably more than that many valid candles so a Regime_Label is
# reliably produced (not an Unavailable_Marker).
_MIN_CANDLES = 120
_MAX_CANDLES = 160


@st.composite
def _label_candles(draw):
    """A sequence of valid OHLCV candle dicts following a random price walk.

    Every candle's OHLCV fields are finite numbers, and consecutive closes vary
    (the walk uses non-trivial steps), so the price path has real movement and
    a real high-low range. This guarantees the measure denominators are
    generally non-zero and that ``classify_regime`` returns a Regime_Label for a
    sequence of at least the largest configured lookback of valid candles.
    """
    n = draw(st.integers(min_value=_MIN_CANDLES, max_value=_MAX_CANDLES))
    price = draw(
        st.floats(min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)
    )
    candles = []
    for _ in range(n):
        step = draw(
            st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False)
        )
        new_price = max(price + step, 1.0)
        open_ = price
        close = new_price
        high = max(open_, close) + draw(
            st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)
        )
        low = max(
            min(open_, close)
            - draw(
                st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)
            ),
            0.5,
        )
        candles.append(
            {"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}
        )
        price = new_price
    return candles


def _is_finite_or_null(value) -> bool:
    """True when ``value`` is None or a finite real number (bool excluded)."""
    if value is None:
        return True
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 3: Computed measures are present and finite-or-null
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 3
@settings(max_examples=150, deadline=None)
@given(candles=_label_candles())
def test_property_3_measures_present_and_finite_or_null(candles):
    """Feature: regime-detection-gate, Property 3: Computed measures are present
    and finite-or-null — for any candle sequence containing at least the largest
    configured lookback of valid candles, the resulting Regime_Label includes
    each named Regime_Measure, and each is either a finite number or null (None).

    Validates: Requirements 1.4, 1.5, 1.6, 1.7, 2.4
    """
    config = resolve_regime_config()
    result = classify_regime(candles, config)

    # The property concerns the Regime_Label case (sufficient valid candles with
    # real movement). If the classifier reports unavailable (e.g. a degenerate
    # all-null window — a separate property), skip: this property only asserts
    # over produced labels.
    assume("unavailable" not in result)

    # A Regime_Label must carry a ``measures`` mapping.
    assert "measures" in result, f"Regime_Label missing 'measures': {result!r}"
    measures = result["measures"]
    assert isinstance(measures, dict), f"'measures' is not a dict: {measures!r}"

    # Each named Regime_Measure must be present and be a finite number or null.
    for field in REGIME_MEASURE_FIELDS:
        assert field in measures, f"measure '{field}' missing from {measures!r}"
        value = measures[field]
        assert _is_finite_or_null(value), (
            f"measure '{field}' is neither a finite number nor null: {value!r}"
        )
