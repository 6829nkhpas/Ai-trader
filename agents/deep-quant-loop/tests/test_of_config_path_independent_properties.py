"""Property-based test for deterministic, path-independent resolution (order_flow.py, task 1.4).

Feature: order-flow-context

This module implements design **Property 36: Parameter resolution is
deterministic and path-independent**:

    For identical environment-variable values, ``resolve_order_flow_config``
    returns identical resolved ``OrderFlowConfig`` values no matter how many
    times it is called — in particular the live Order_Flow_Tool path and the
    Backtest_Seeder path (both of which call the same function) resolve to the
    same configuration and the same documented defaults.

Validates: Requirements 13.6.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_rs_config_default_fallback_properties.py``.
"""

import os
import sys
from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import order_flow  # noqa: E402
from order_flow import OrderFlowConfig, resolve_order_flow_config  # noqa: E402

# Every OF_* env var the resolver reads. We clear all of them inside the
# isolation context so only the values under test influence the result and the
# environment never leaks across Hypothesis re-runs.
_ALL_OF_ENV_VARS = (
    order_flow.ENV_OF_LOOKBACK,
    order_flow.ENV_OF_MIN_CANDLES,
    order_flow.ENV_OF_BUY_PRESSURE_THRESHOLD,
    order_flow.ENV_OF_SELL_PRESSURE_THRESHOLD,
    order_flow.ENV_OF_OFI_BUY_THRESHOLD,
    order_flow.ENV_OF_OFI_SELL_THRESHOLD,
    order_flow.ENV_OF_MIN_TICKS,
)


@contextmanager
def _of_env(overrides):
    """Isolate ``os.environ`` for the resolver.

    Removes every OF_* var, applies ``overrides``, and restores the prior
    environment exactly on exit (so Hypothesis re-runs never leak state). Used
    instead of the ``monkeypatch`` fixture because Hypothesis re-invokes the test
    body many times within one function-scoped fixture lifetime.
    """
    saved = {name: os.environ.get(name) for name in _ALL_OF_ENV_VARS}
    try:
        for name in _ALL_OF_ENV_VARS:
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
    st.integers(min_value=-1000, max_value=1).map(str),         # below min (2)
    st.integers(min_value=2, max_value=500).map(str),           # valid
    st.floats(min_value=2.0, max_value=50.0).map(lambda f: f"{f:.3f}"),  # non-int text
)

# Pressure thresholds are valid in [0.0, 1.0]; OFI thresholds in [-1.0, 1.0].
# We share a broad float strategy across both — values outside one range are
# still useful (they exercise out-of-range fallback) and resolution must be
# identical regardless.
_float_value = st.one_of(
    st.none(),                                                  # unset
    st.just(""),                                                # empty
    st.just("   "),                                             # whitespace-only
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),    # unparseable garbage
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),        # non-finite
    st.floats(min_value=-2.0, max_value=2.0, allow_nan=False,
              allow_infinity=False).map(repr),                  # spans valid + out-of-range
)

_env_assignment = st.fixed_dictionaries(
    {
        order_flow.ENV_OF_LOOKBACK: _int_value,
        order_flow.ENV_OF_MIN_CANDLES: _int_value,
        order_flow.ENV_OF_MIN_TICKS: _int_value,
        order_flow.ENV_OF_BUY_PRESSURE_THRESHOLD: _float_value,
        order_flow.ENV_OF_SELL_PRESSURE_THRESHOLD: _float_value,
        order_flow.ENV_OF_OFI_BUY_THRESHOLD: _float_value,
        order_flow.ENV_OF_OFI_SELL_THRESHOLD: _float_value,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 36 (task 1.4): Parameter resolution is deterministic and path-independent
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 36: Parameter resolution is deterministic and path-independent
@settings(max_examples=200, deadline=None)
@given(assignment=_env_assignment)
def test_property_36_resolution_is_deterministic_and_path_independent(assignment):
    """Feature: order-flow-context, Property 36: Parameter resolution is
    deterministic and path-independent — for identical environment-variable
    values, ``resolve_order_flow_config`` returns identical resolved
    ``OrderFlowConfig`` values across repeated calls (simulating the live tool
    path and the backtest-seeder path), with identical documented defaults.

    Validates: Requirements 13.6
    """
    # ``None`` means "leave the var unset" so the unset-fallback path is exercised.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _of_env(overrides):
        # The live Order_Flow_Tool path resolves the config.
        tool_path_config = resolve_order_flow_config()
        # The Backtest_Seeder path resolves the config from the SAME environment.
        backtest_path_config = resolve_order_flow_config()
        # A third call guards general determinism / idempotency.
        third_config = resolve_order_flow_config()

    # The resolver never raised and produced fully-formed configs.
    assert isinstance(tool_path_config, OrderFlowConfig)
    assert isinstance(backtest_path_config, OrderFlowConfig)
    assert isinstance(third_config, OrderFlowConfig)

    # Path-independence: the tool path and the backtest path resolve identically.
    assert tool_path_config == backtest_path_config
    # Determinism: every call returns the same value.
    assert tool_path_config == third_config

    # Field-level equality (covers every resolved parameter explicitly, so a
    # failure pinpoints the divergent field rather than the whole dataclass).
    assert tool_path_config.lookback == backtest_path_config.lookback
    assert tool_path_config.min_candles == backtest_path_config.min_candles
    assert tool_path_config.min_ticks == backtest_path_config.min_ticks
    assert tool_path_config.buy_pressure_threshold == backtest_path_config.buy_pressure_threshold
    assert tool_path_config.sell_pressure_threshold == backtest_path_config.sell_pressure_threshold
    assert tool_path_config.ofi_buy_threshold == backtest_path_config.ofi_buy_threshold
    assert tool_path_config.ofi_sell_threshold == backtest_path_config.ofi_sell_threshold
    # The derived property is identical too.
    assert tool_path_config.largest_lookback == backtest_path_config.largest_lookback
