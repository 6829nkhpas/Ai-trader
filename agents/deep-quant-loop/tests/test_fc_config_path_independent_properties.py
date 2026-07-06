"""Property-based test for deterministic, path-independent resolution (forecaster.py, task 1.3).

Feature: volatility-aware-forecaster

This module implements design **Property 37: Parameter resolution is
deterministic and path-independent**:

    For identical environment-variable values, ``resolve_forecaster_config``
    returns identical resolved ``ForecasterConfig`` values no matter how many
    times it is called — in particular the live Forecast_Tool path and the
    Backtest_Seeder path (both of which call the same function) resolve to the
    same configuration and the same documented defaults.

Validates: Requirements 14.5.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_of_config_path_independent_properties.py``.
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
from forecaster import ForecasterConfig, resolve_forecaster_config  # noqa: E402

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


# ── Value strategies ──────────────────────────────────────────────────────────
# A deliberately broad mix of values per env var: valid in-range values,
# out-of-range values, unparseable garbage, empty/whitespace, and "unset"
# (``None``). The resolution must be identical no matter which category a given
# value falls into, so we exercise all of them.
_int_value = st.one_of(
    st.none(),                                                  # unset
    st.just(""),                                                # empty
    st.just("   "),                                             # whitespace-only
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),    # unparseable garbage
    st.integers(min_value=-1000, max_value=0).map(str),         # below min
    st.integers(min_value=1, max_value=500).map(str),           # valid / spans bounds
    st.floats(min_value=1.0, max_value=50.0).map(lambda f: f"{f:.3f}"),  # non-int text
)

# Decimal parameters: flat band in [0.0, 5.0], prob scale in [0.0, 50.0]. A
# broad shared float strategy exercises both in-range and out-of-range fallback;
# resolution must be identical regardless of the category.
_float_value = st.one_of(
    st.none(),                                                  # unset
    st.just(""),                                                # empty
    st.just("   "),                                             # whitespace-only
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),    # unparseable garbage
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),        # non-finite
    st.floats(min_value=-10.0, max_value=60.0, allow_nan=False,
              allow_infinity=False).map(repr),                  # spans valid + out-of-range
)

_env_assignment = st.fixed_dictionaries(
    {
        forecaster.ENV_FORECAST_DRIFT_LOOKBACK: _int_value,
        forecaster.ENV_FORECAST_VOL_LOOKBACK: _int_value,
        forecaster.ENV_FORECAST_ATR_PERIOD: _int_value,
        forecaster.ENV_FORECAST_MIN_CANDLES: _int_value,
        forecaster.ENV_FORECAST_PROB_BINS: _int_value,
        forecaster.ENV_FORECAST_FLAT_BAND: _float_value,
        forecaster.ENV_FORECAST_PROB_SCALE: _float_value,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 37 (task 1.3): Parameter resolution is deterministic and path-independent
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 37: Parameter resolution is deterministic and path-independent
@settings(max_examples=200, deadline=None)
@given(assignment=_env_assignment)
def test_property_37_resolution_is_deterministic_and_path_independent(assignment):
    """Feature: volatility-aware-forecaster, Property 37: Parameter resolution is
    deterministic and path-independent — for identical environment-variable
    values, ``resolve_forecaster_config`` returns identical resolved
    ``ForecasterConfig`` values across repeated calls (simulating the live
    Forecast_Tool path and the Backtest_Seeder path), with identical documented
    defaults.

    Validates: Requirements 14.5
    """
    # ``None`` means "leave the var unset" so the unset-fallback path is exercised.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _forecast_env(overrides):
        # The live Forecast_Tool path resolves the config.
        tool_path_config = resolve_forecaster_config()
        # The Backtest_Seeder path resolves the config from the SAME environment.
        backtest_path_config = resolve_forecaster_config()
        # A third call guards general determinism / idempotency.
        third_config = resolve_forecaster_config()

    # The resolver never raised and produced fully-formed configs.
    assert isinstance(tool_path_config, ForecasterConfig)
    assert isinstance(backtest_path_config, ForecasterConfig)
    assert isinstance(third_config, ForecasterConfig)

    # Path-independence: the tool path and the backtest path resolve identically.
    assert tool_path_config == backtest_path_config
    # Determinism: every call returns the same value.
    assert tool_path_config == third_config

    # Field-level equality (covers every resolved parameter explicitly, so a
    # failure pinpoints the divergent field rather than the whole dataclass).
    assert tool_path_config.drift_lookback == backtest_path_config.drift_lookback
    assert tool_path_config.vol_lookback == backtest_path_config.vol_lookback
    assert tool_path_config.atr_period == backtest_path_config.atr_period
    assert tool_path_config.flat_band == backtest_path_config.flat_band
    assert tool_path_config.min_candles == backtest_path_config.min_candles
    assert tool_path_config.prob_bins == backtest_path_config.prob_bins
    assert tool_path_config.prob_scale == backtest_path_config.prob_scale
    # The derived property is identical too.
    assert tool_path_config.largest_lookback == backtest_path_config.largest_lookback
