"""Property-based test for regime conditioning of the standardized drift (forecaster.py, task 3.2).

Feature: volatility-aware-forecaster

This module implements design **Property 4: Regime conditioning weights
continuation in trends and reversion in ranges**:

    For any finite Drift_Estimate and any strictly-positive Volatility_Estimate,
    the regime-conditioned standardized drift produced by
    ``forecaster.conditioned_drift(drift, volatility, trend_state, config)``:

      * amplifies the unweighted standardized drift ``drift / volatility`` when
        the regime ``trend_state`` is ``'trending'`` (trend-continuation, a
        weight >= 1) (R2.2),
      * dampens it toward zero when the regime ``trend_state`` is ``'ranging'``
        (mean-reversion, a weight in ``[0, 1]``) (R2.3),
      * leaves it unchanged for a ``'transitional'`` / unavailable regime (a
        neutral, unweighted blend) (R2.4 context).

    Concretely, magnitudes are ordered

        |trending| >= |transitional| >= |ranging|,

    the trending and neutral (transitional) results carry the SAME sign as the
    raw drift (continuation never flips direction), the ranging result dampens
    the magnitude toward zero (reversion), and the neutral (transitional) result
    equals the unweighted standardized drift ``drift / volatility`` exactly.

Validates: Requirements 2.2, 2.3.

The sys.path / import pattern mirrors the sibling
``test_forecaster_estimation_measures_properties.py`` and
``test_forecaster_config_default_fallback_properties.py`` modules, and the
config is obtained from ``resolve_forecaster_config()`` exactly as the live
tool and backtest paths do.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from forecaster import (  # noqa: E402
    conditioned_drift,
    resolve_forecaster_config,
)

# Finite drift values spanning negative, zero, and positive momentum.
_DRIFT = st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False)
# Strictly-positive volatility (a flat / zero-variance window is covered by a
# separate zero-variance property); kept away from 0 so drift / volatility is
# well-defined and finite.
_VOLATILITY = st.floats(
    min_value=1e-6, max_value=1e3, allow_nan=False, allow_infinity=False
)


def _is_finite_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ─────────────────────────────────────────────────────────────────────────────
# Property 4 (task 3.2): Regime conditioning weights continuation in trends and
# reversion in ranges
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 4: Regime conditioning weights continuation in trends and reversion in ranges
@settings(max_examples=200, deadline=None)
@given(drift=_DRIFT, volatility=_VOLATILITY)
def test_property_4_regime_conditioning_continuation_and_reversion(drift, volatility):
    """Feature: volatility-aware-forecaster, Property 4: Regime conditioning
    weights continuation in trends and reversion in ranges.

    For finite drift and positive volatility, the trending regime amplifies the
    standardized drift, the transitional/unavailable regime is neutral
    (unweighted), and the ranging regime dampens it toward zero — giving the
    magnitude ordering |trending| >= |transitional| >= |ranging|, a sign that
    matches the raw drift for the continuation (trending/neutral) cases, and a
    neutral result equal to the unweighted standardized drift drift / volatility.

    Validates: Requirements 2.2, 2.3
    """
    config = resolve_forecaster_config()

    trending = conditioned_drift(drift, volatility, "trending", config)
    transitional = conditioned_drift(drift, volatility, "transitional", config)
    ranging = conditioned_drift(drift, volatility, "ranging", config)

    # Every conditioned drift is a finite number and never raises (R2.2-R2.4).
    assert _is_finite_number(trending)
    assert _is_finite_number(transitional)
    assert _is_finite_number(ranging)

    base = drift / volatility

    # ── Neutral (transitional) equals the unweighted standardized drift ──────
    # A transitional / unavailable regime applies a neutral (weight 1) blend, so
    # the conditioned drift is exactly the raw standardized drift drift / vol.
    assert math.isclose(transitional, base, rel_tol=1e-9, abs_tol=1e-12), (
        f"transitional {transitional!r} != drift/vol {base!r}"
    )

    # ── Magnitude ordering: trending amplifies, ranging dampens (R2.2, R2.3) ─
    # abs(trending) >= abs(transitional) >= abs(ranging).
    assert abs(trending) >= abs(transitional) - 1e-12, (
        f"trending magnitude {abs(trending)!r} < transitional {abs(transitional)!r}"
    )
    assert abs(transitional) >= abs(ranging) - 1e-12, (
        f"transitional magnitude {abs(transitional)!r} < ranging {abs(ranging)!r}"
    )

    # ── Continuation: trending and neutral keep the raw drift's sign ─────────
    # (Trend-continuation never flips direction; reversion dampens toward zero
    # without overshooting past zero into the opposite sign.)
    drift_sign = (drift > 0) - (drift < 0)  # -1, 0, or +1
    for label, value in (("trending", trending), ("transitional", transitional), ("ranging", ranging)):
        value_sign = (value > 0) - (value < 0)
        if drift_sign == 0:
            # Zero drift -> zero standardized drift in every regime.
            assert value == 0.0, f"{label} sign mismatch: drift 0 but value {value!r}"
        else:
            # Continuation/reversion may dampen to exactly zero but never flips sign.
            assert value_sign == drift_sign or value == 0.0, (
                f"{label} flipped sign relative to drift: drift {drift!r}, value {value!r}"
            )

    # ── Reversion dampens magnitude toward zero (R2.3) ───────────────────────
    # The ranging (mean-reversion) result is no larger in magnitude than the raw
    # standardized drift — it pulls the move back toward the recent mean.
    assert abs(ranging) <= abs(base) + 1e-12, (
        f"ranging magnitude {abs(ranging)!r} exceeds unweighted {abs(base)!r}"
    )
