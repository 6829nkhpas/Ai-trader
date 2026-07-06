"""Property-based test for pressure-threshold ordering enforcement (order_flow.py, task 1.3).

Feature: order-flow-context

This module implements design **Property 35: Pressure-threshold ordering is
enforced**:

    For any environment in which the resolved selling-pressure threshold is not
    strictly less than the resolved buying-pressure threshold,
    ``resolve_order_flow_config`` applies the documented default values for both
    pressure thresholds without raising.

Validates: Requirements 13.5.

Strategy: the buying/selling pressure env vars are assigned valid in-range
floats (in ``[0.0, 1.0]``) constrained so the *selling* value is greater than or
equal to the *buying* value (``sell >= buy``). Because both values are in range,
the per-parameter resolution step keeps them verbatim, so the resolved selling
threshold is not strictly less than the resolved buying threshold — exactly the
condition Property 35 guards. The ordering guard must then revert BOTH pressure
thresholds to their documented defaults together. The remaining OF_* parameters
are assigned arbitrary values (including unset/garbage/out-of-range) to show the
pressure-ordering enforcement is independent of the other parameters.

The sys.path / import pattern and the ``os.environ`` isolation context mirror
``tests/test_rs_config_properties.py``.
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


# An arbitrary value for the *other* (non-pressure) parameters: ``None`` leaves
# the var unset; any string spans the realistic input space (valid, empty,
# whitespace, non-finite, out-of-range, garbage). These must not affect whether
# the pressure-ordering guard fires.
_other_value = st.one_of(
    st.none(),
    st.just(""),
    st.just("   "),
    st.floats(allow_nan=True, allow_infinity=True).map(repr),
    st.integers(min_value=-500, max_value=500).map(str),
    st.text(max_size=6),
)


@st.composite
def _sell_not_below_buy(draw):
    """Draw an in-range (buy, sell) pressure pair with ``sell >= buy``.

    Both values are valid (finite, in ``[0.0, 1.0]``) so per-parameter resolution
    keeps them verbatim; constraining ``sell >= buy`` guarantees the resolved
    selling threshold is *not strictly less than* the resolved buying threshold —
    the precondition of Property 35. Equality is allowed so the ``==`` boundary
    (still "not strictly less than") is exercised.
    """
    buy = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    sell = draw(st.floats(min_value=buy, max_value=1.0, allow_nan=False, allow_infinity=False))
    return buy, sell


# ─────────────────────────────────────────────────────────────────────────────
# Property 35 (task 1.3): Pressure-threshold ordering is enforced
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 35: Pressure-threshold ordering is enforced
@settings(max_examples=200, deadline=None)
@given(
    pressure_pair=_sell_not_below_buy(),
    lookback=_other_value,
    min_candles=_other_value,
    ofi_buy=_other_value,
    ofi_sell=_other_value,
    min_ticks=_other_value,
)
def test_property_35_pressure_threshold_ordering_is_enforced(
    pressure_pair, lookback, min_candles, ofi_buy, ofi_sell, min_ticks
):
    """Feature: order-flow-context, Property 35: Pressure-threshold ordering is
    enforced — for any environment in which the resolved selling-pressure
    threshold is not strictly less than the resolved buying-pressure threshold,
    ``resolve_order_flow_config`` reverts BOTH pressure thresholds to their
    documented defaults and never raises.

    Validates: Requirements 13.5
    """
    buy_value, sell_value = pressure_pair

    candidate = {
        order_flow.ENV_OF_BUY_PRESSURE_THRESHOLD: repr(buy_value),
        order_flow.ENV_OF_SELL_PRESSURE_THRESHOLD: repr(sell_value),
        order_flow.ENV_OF_LOOKBACK: lookback,
        order_flow.ENV_OF_MIN_CANDLES: min_candles,
        order_flow.ENV_OF_OFI_BUY_THRESHOLD: ofi_buy,
        order_flow.ENV_OF_OFI_SELL_THRESHOLD: ofi_sell,
        order_flow.ENV_OF_MIN_TICKS: min_ticks,
    }
    # ``None`` means "leave unset"; everything else is set verbatim.
    overrides = {name: value for name, value in candidate.items() if value is not None}

    with _of_env(overrides):
        config = resolve_order_flow_config()

    # The resolver never raised and produced a fully-formed OrderFlowConfig.
    assert isinstance(config, order_flow.OrderFlowConfig)

    # Sanity: the precondition we constructed (resolved sell >= resolved buy)
    # truly holds for these in-range inputs before the guard reverts them.
    assert sell_value >= buy_value

    # The ordering guard reverted BOTH pressure thresholds to their documented
    # defaults together — never just one, never the supplied out-of-order values.
    assert config.buy_pressure_threshold == DEFAULT_OF_BUY_PRESSURE_THRESHOLD
    assert config.sell_pressure_threshold == DEFAULT_OF_SELL_PRESSURE_THRESHOLD

    # And the documented defaults themselves satisfy the strict ordering.
    assert config.sell_pressure_threshold < config.buy_pressure_threshold
