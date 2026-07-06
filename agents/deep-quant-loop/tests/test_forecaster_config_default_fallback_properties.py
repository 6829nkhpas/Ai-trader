"""Property-based test for per-parameter default fallback (forecaster.py, task 1.2).

Feature: volatility-aware-forecaster

This module implements design **Property 36: Each parameter falls back to its
documented default**:

    When a parameter's environment variable is unset, empty/whitespace-only,
    unparseable as its expected numeric type, or parses but falls outside the
    parameter's valid range, ``resolve_forecaster_config`` applies that
    parameter's own documented default — independently for every parameter — and
    never raises.

Validates: Requirements 14.1, 14.2, 14.3, 14.4.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_rs_config_default_fallback_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import forecaster  # noqa: E402
from forecaster import (  # noqa: E402
    DEFAULT_FORECAST_ATR_PERIOD,
    DEFAULT_FORECAST_DRIFT_LOOKBACK,
    DEFAULT_FORECAST_FLAT_BAND,
    DEFAULT_FORECAST_MIN_CANDLES,
    DEFAULT_FORECAST_PROB_BINS,
    DEFAULT_FORECAST_PROB_SCALE,
    DEFAULT_FORECAST_VOL_LOOKBACK,
    resolve_forecaster_config,
)

# Every FORECAST_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_FORECAST_ENV_VARS = (
    forecaster.ENV_FORECAST_DRIFT_LOOKBACK,
    forecaster.ENV_FORECAST_VOL_LOOKBACK,
    forecaster.ENV_FORECAST_ATR_PERIOD,
    forecaster.ENV_FORECAST_FLAT_BAND,
    forecaster.ENV_FORECAST_MIN_CANDLES,
    forecaster.ENV_FORECAST_PROB_BINS,
    forecaster.ENV_FORECAST_PROB_SCALE,
)


@contextmanager
def _forecast_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every FORECAST_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_FORECAST_ENV_VARS}
    try:
        for name in _ALL_FORECAST_ENV_VARS:
            os.environ.pop(name, None)
        for name, value in overrides.items():
            os.environ[name] = value
        yield
    finally:
        for name, prior in saved.items():
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior


# ── "Bad value" strategies (each should force the documented default) ─────────
# Shared categories that are bad for ANY parameter type: unset, empty,
# whitespace-only, and unparseable non-numeric garbage. ``None`` means "leave the
# var unset".
_shared_bad = st.one_of(
    st.none(),                                                # unset (R14.2)
    st.just(""),                                              # empty (R14.2)
    st.just("   "),                                           # whitespace-only (R14.2)
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),  # unparseable garbage (R14.3)
)

# A reusable "non-integer text" generator: float-like strings parse fine as
# floats but FAIL ``int()``, so they force the default for an int parameter.
_float_text = st.floats(
    min_value=2.0, max_value=50.0, allow_nan=False, allow_infinity=False
).map(lambda f: f"{f:.3f}")

# Integer params with a minimum of 2 (drift_lookback, vol_lookback, min_candles):
# out-of-range bad values are integers <= 1 (incl. zero/negatives) (R14.4).
_int_bad_min2 = st.one_of(
    _shared_bad,
    st.integers(min_value=-1000, max_value=1).map(str),  # below min 2 (R14.4)
    _float_text,                                          # non-int text (R14.3)
)

# ATR period has a minimum of 1: out-of-range bad values are integers <= 0.
_int_bad_min1 = st.one_of(
    _shared_bad,
    st.integers(min_value=-1000, max_value=0).map(str),  # below min 1 (R14.4)
    _float_text,                                          # non-int text (R14.3)
)

# Calibration bin count is valid only in [1, 100]: out-of-range bad values are
# integers <= 0 OR > 100 (the upper bound is enforced in the resolver) (R14.4).
_prob_bins_bad = st.one_of(
    _shared_bad,
    st.integers(min_value=-1000, max_value=0).map(str),   # below min 1 (R14.4)
    st.integers(min_value=101, max_value=100000).map(str),  # above max 100 (R14.4)
    _float_text,                                           # non-int text (R14.3)
)

# Flat band is valid only in [0.0, 5.0]: out-of-range bad values are negative or
# above 5.0, plus non-finite floats.
_flat_band_bad = st.one_of(
    _shared_bad,
    st.floats(min_value=5.0001, max_value=1e6, allow_nan=False, allow_infinity=False).map(repr),    # > 5.0 (R14.4)
    st.floats(min_value=-1e6, max_value=-0.0001, allow_nan=False, allow_infinity=False).map(repr),  # < 0.0 (R14.4)
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),                                            # non-finite (R14.3/14.4)
)

# Logistic probability scale is valid only in [0.0, 50.0]: out-of-range bad
# values are negative or above 50.0, plus non-finite floats.
_prob_scale_bad = st.one_of(
    _shared_bad,
    st.floats(min_value=50.0001, max_value=1e6, allow_nan=False, allow_infinity=False).map(repr),   # > 50.0 (R14.4)
    st.floats(min_value=-1e6, max_value=-0.0001, allow_nan=False, allow_infinity=False).map(repr),   # < 0.0 (R14.4)
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),                                            # non-finite (R14.3/14.4)
)

# A complete assignment of a bad value for every parameter at once. Because every
# parameter is bad, every one must independently fall back to its own documented
# default.
_bad_assignment = st.fixed_dictionaries(
    {
        forecaster.ENV_FORECAST_DRIFT_LOOKBACK: _int_bad_min2,
        forecaster.ENV_FORECAST_VOL_LOOKBACK: _int_bad_min2,
        forecaster.ENV_FORECAST_MIN_CANDLES: _int_bad_min2,
        forecaster.ENV_FORECAST_ATR_PERIOD: _int_bad_min1,
        forecaster.ENV_FORECAST_PROB_BINS: _prob_bins_bad,
        forecaster.ENV_FORECAST_FLAT_BAND: _flat_band_bad,
        forecaster.ENV_FORECAST_PROB_SCALE: _prob_scale_bad,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 36 (task 1.2): Each parameter falls back to its documented default
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 36: Each parameter falls back to its documented default
@settings(max_examples=200, deadline=None)
@given(assignment=_bad_assignment)
def test_property_36_each_parameter_falls_back_to_its_default(assignment):
    """Feature: volatility-aware-forecaster, Property 36: Each parameter falls
    back to its documented default — when a parameter's env var is unset, empty/
    whitespace, unparseable as its expected numeric type, or parses but is out of
    range, ``resolve_forecaster_config`` applies that parameter's documented
    default and never raises.

    Validates: Requirements 14.1, 14.2, 14.3, 14.4
    """
    # Only set the vars the assignment marks as present; ``None`` leaves the var
    # unset so the unset-fallback path (R14.2) is exercised too.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _forecast_env(overrides):
        config = resolve_forecaster_config()

    # The resolver never raised and produced a fully-formed ForecasterConfig.
    assert isinstance(config, forecaster.ForecasterConfig)

    # Every parameter independently fell back to its own documented default.
    assert config.drift_lookback == DEFAULT_FORECAST_DRIFT_LOOKBACK
    assert config.vol_lookback == DEFAULT_FORECAST_VOL_LOOKBACK
    assert config.atr_period == DEFAULT_FORECAST_ATR_PERIOD
    assert config.min_candles == DEFAULT_FORECAST_MIN_CANDLES
    assert config.prob_bins == DEFAULT_FORECAST_PROB_BINS
    assert config.flat_band == DEFAULT_FORECAST_FLAT_BAND
    assert config.prob_scale == DEFAULT_FORECAST_PROB_SCALE
