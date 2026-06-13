"""Property-based test for per-parameter default fallback (order_flow.py, task 1.2).

Feature: order-flow-context

This module implements design **Property 34: Each parameter falls back to its
documented default**:

    When a parameter's environment variable is unset, empty/whitespace-only,
    unparseable as its expected numeric type, or parses but falls outside the
    parameter's valid range, ``resolve_order_flow_config`` applies that
    parameter's own documented default — independently for every parameter — and
    never raises.

Validates: Requirements 13.1, 13.2, 13.3, 13.4.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_rs_config_default_fallback_properties.py`` and
``tests/test_regime_config_properties.py``.
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
from order_flow import (  # noqa: E402
    DEFAULT_OF_BUY_PRESSURE_THRESHOLD,
    DEFAULT_OF_LOOKBACK,
    DEFAULT_OF_MIN_CANDLES,
    DEFAULT_OF_MIN_TICKS,
    DEFAULT_OF_OFI_BUY_THRESHOLD,
    DEFAULT_OF_OFI_SELL_THRESHOLD,
    DEFAULT_OF_SELL_PRESSURE_THRESHOLD,
    resolve_order_flow_config,
)

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


# ── "Bad value" strategies (each should force the documented default) ─────────
# Shared categories that are bad for ANY parameter type: empty, whitespace-only,
# and unparseable non-numeric garbage. ``None`` means "leave the var unset".
_shared_bad = st.one_of(
    st.none(),                                                # unset (R13.2)
    st.just(""),                                              # empty (R13.2)
    st.just("   "),                                           # whitespace-only (R13.2)
    st.text(alphabet="abcXYZ#@/-_", min_size=1, max_size=6),  # unparseable garbage (R13.3)
)

# Integer params (lookback, min_candles, min_ticks) are valid only at >= 2.
# Out-of-range bad values are integers <= 1 (incl. zero/negatives); float-like
# text is unparseable as an int and so also forces the default.
_int_bad = st.one_of(
    _shared_bad,
    st.integers(min_value=-1000, max_value=1).map(str),                 # below min 2 (R13.4)
    st.floats(min_value=2.0, max_value=50.0).map(lambda f: f"{f:.3f}"),  # non-int text (R13.3)
)

# The pressure thresholds are valid only in [0.0, 1.0]. Out-of-range bad values
# fall strictly outside that band, plus non-finite floats.
_pressure_bad = st.one_of(
    _shared_bad,
    st.floats(min_value=1.0001, max_value=1e6, allow_nan=False, allow_infinity=False).map(repr),    # > 1.0 (R13.4)
    st.floats(min_value=-1e6, max_value=-0.0001, allow_nan=False, allow_infinity=False).map(repr),  # < 0.0 (R13.4)
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),                                            # non-finite (R13.3/13.4)
)

# The Tick_OFI thresholds are valid only in [-1.0, 1.0]. Out-of-range bad values
# fall strictly outside that band, plus non-finite floats.
_ofi_bad = st.one_of(
    _shared_bad,
    st.floats(min_value=1.0001, max_value=1e6, allow_nan=False, allow_infinity=False).map(repr),    # > 1.0 (R13.4)
    st.floats(min_value=-1e6, max_value=-1.0001, allow_nan=False, allow_infinity=False).map(repr),  # < -1.0 (R13.4)
    st.sampled_from(["nan", "inf", "-inf", "Infinity"]),                                            # non-finite (R13.3/13.4)
)

# A complete assignment of a bad value for every parameter at once. Because every
# parameter is bad, every one must fall back to its own documented default; the
# documented defaults satisfy the threshold ordering guards (sell < buy for both
# the pressure and the Tick_OFI thresholds), so the ordering revert is a no-op.
_bad_assignment = st.fixed_dictionaries(
    {
        order_flow.ENV_OF_LOOKBACK: _int_bad,
        order_flow.ENV_OF_MIN_CANDLES: _int_bad,
        order_flow.ENV_OF_MIN_TICKS: _int_bad,
        order_flow.ENV_OF_BUY_PRESSURE_THRESHOLD: _pressure_bad,
        order_flow.ENV_OF_SELL_PRESSURE_THRESHOLD: _pressure_bad,
        order_flow.ENV_OF_OFI_BUY_THRESHOLD: _ofi_bad,
        order_flow.ENV_OF_OFI_SELL_THRESHOLD: _ofi_bad,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 34 (task 1.2): Each parameter falls back to its documented default
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 34: Each parameter falls back to its documented default
@settings(max_examples=200, deadline=None)
@given(assignment=_bad_assignment)
def test_property_34_each_parameter_falls_back_to_its_default(assignment):
    """Feature: order-flow-context, Property 34: Each parameter falls back to its
    documented default — when a parameter's env var is unset, empty/whitespace,
    unparseable as its expected numeric type, or parses but is out of range,
    ``resolve_order_flow_config`` applies that parameter's documented default and
    never raises.

    Validates: Requirements 13.1, 13.2, 13.3, 13.4
    """
    # Only set the vars the assignment marks as present; ``None`` leaves the var
    # unset so the unset-fallback path (R13.2) is exercised too.
    overrides = {name: value for name, value in assignment.items() if value is not None}

    with _of_env(overrides):
        config = resolve_order_flow_config()

    # The resolver never raised and produced a fully-formed OrderFlowConfig.
    assert isinstance(config, order_flow.OrderFlowConfig)

    # Every parameter independently fell back to its own documented default.
    assert config.lookback == DEFAULT_OF_LOOKBACK
    assert config.min_candles == DEFAULT_OF_MIN_CANDLES
    assert config.min_ticks == DEFAULT_OF_MIN_TICKS
    assert config.buy_pressure_threshold == DEFAULT_OF_BUY_PRESSURE_THRESHOLD
    assert config.sell_pressure_threshold == DEFAULT_OF_SELL_PRESSURE_THRESHOLD
    assert config.ofi_buy_threshold == DEFAULT_OF_OFI_BUY_THRESHOLD
    assert config.ofi_sell_threshold == DEFAULT_OF_OFI_SELL_THRESHOLD
