"""Property-based test for the Order_Flow_State threshold mapping (order_flow.py, task 4.5).

Feature: order-flow-context

This module implements design **Property 9: Order_Flow_State is well-formed and
matches the threshold mapping**:

    For any Order_Flow_Label produced from sufficient candles, the
    Order_Flow_State is exactly one of ``buying`` / ``selling`` / ``balanced``
    and equals the value dictated by comparing the deciding signal against the
    configured thresholds per the specified mapping tables — the Tick_OFI
    against the Tick_OFI buy/sell thresholds when a usable (finite) Tick_OFI is
    present (tick-first priority), otherwise the candle-derived buying-pressure
    ratio against the pressure thresholds; a ``None`` deciding signal yields
    ``balanced``.

Validates: Requirements 3.1.

The design's Order_Flow_State classification (total mapping) tables are:

  When a usable Tick_OFI is present (tick-first):
    tick_ofi >= ofi_buy_threshold   -> buying
    tick_ofi <= ofi_sell_threshold  -> selling
    otherwise (between thresholds)  -> balanced

  Otherwise (proxy layer):
    buying_pressure_ratio >= buy_pressure_threshold  -> buying
    buying_pressure_ratio <= sell_pressure_threshold -> selling
    otherwise (between thresholds, or None)          -> balanced

This test exercises ``order_flow.classify_order_flow_state`` directly across
finite Tick_OFI values in ``[-1, 1]`` (and ``None``) and buying-pressure-ratio
values in ``[0, 1]`` (and ``None``), using the resolved config. It additionally
asserts that a full ``classify_order_flow`` Order_Flow_Label (built from
sufficient candles) carries the same state the mapping dictates. The
sys.path / import pattern mirrors the sibling ``test_of_*_properties.py``
modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import (  # noqa: E402
    classify_order_flow,
    classify_order_flow_state,
    resolve_order_flow_config,
)

# Resolve config once (identical on the tool and backtest paths). Drives the
# thresholds the mapping compares against.
_CONFIG = resolve_order_flow_config()

_VALID_STATES = {"buying", "selling", "balanced"}

# A usable finite Tick_OFI spans its bounded range [-1, 1]; ``None`` exercises
# the "no usable Tick_OFI -> fall back to proxy" branch.
_TICK_OFI = st.one_of(
    st.none(),
    st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
# A buying-pressure ratio spans its bounded range [0, 1]; ``None`` exercises the
# zero-directional-volume (deciding signal is None -> balanced) branch.
_PRESSURE = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)


def _expected_state(tick_ofi, buying_pressure_ratio, config):
    """Independent recomputation of the Order_Flow_State per the mapping tables."""
    # Tick-first priority: a usable finite Tick_OFI decides.
    if tick_ofi is not None:
        if tick_ofi >= config.ofi_buy_threshold:
            return "buying"
        if tick_ofi <= config.ofi_sell_threshold:
            return "selling"
        return "balanced"
    # Otherwise the candle-derived buying-pressure ratio decides.
    if buying_pressure_ratio is None:
        return "balanced"
    if buying_pressure_ratio >= config.buy_pressure_threshold:
        return "buying"
    if buying_pressure_ratio <= config.sell_pressure_threshold:
        return "selling"
    return "balanced"


# ─────────────────────────────────────────────────────────────────────────────
# Property 9 (task 4.5): Order_Flow_State is well-formed and matches the mapping
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 9: Order_Flow_State is well-formed and matches the threshold mapping
@settings(max_examples=300, deadline=None)
@given(tick_ofi=_TICK_OFI, buying_pressure_ratio=_PRESSURE)
def test_property_9_state_well_formed_and_matches_mapping(tick_ofi, buying_pressure_ratio):
    """Feature: order-flow-context, Property 9: Order_Flow_State is well-formed
    and matches the threshold mapping.

    ``classify_order_flow_state`` returns exactly one of buying/selling/balanced
    and equals the value dictated by the deciding-signal threshold comparison
    (tick-first, otherwise buying-pressure ratio; None deciding signal ->
    balanced).

    Validates: Requirements 3.1
    """
    state = classify_order_flow_state(tick_ofi, buying_pressure_ratio, _CONFIG)

    # Well-formed: exactly one of the three states (Requirement 3.1).
    assert state in _VALID_STATES, f"Order_Flow_State {state!r} not in {_VALID_STATES}"

    # Matches the mapping tables (Requirement 3.1).
    expected = _expected_state(tick_ofi, buying_pressure_ratio, _CONFIG)
    assert state == expected, (
        f"classify_order_flow_state({tick_ofi!r}, {buying_pressure_ratio!r}) = "
        f"{state!r} != mapping-dictated {expected!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# A full Order_Flow_Label (from sufficient candles) carries the mapped state.
# ─────────────────────────────────────────────────────────────────────────────

# Each generated candle is a clean OHLCV dict with a non-zero range so the proxy
# layer is computable. ``low <= open/close <= high`` keeps the candle valid.
@st.composite
def _candle(draw):
    low = draw(st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False))
    high = low + draw(st.floats(min_value=0.5, max_value=1e4, allow_nan=False, allow_infinity=False))
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    volume = draw(st.floats(min_value=1.0, max_value=1e6, allow_nan=False, allow_infinity=False))
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


# At least ``largest_lookback`` valid candles so the label is produced (no ticks
# -> proxy layer decides; tick_ofi is None).
@st.composite
def _sufficient_candles(draw):
    n = draw(st.integers(min_value=_CONFIG.largest_lookback, max_value=_CONFIG.largest_lookback + 25))
    return draw(st.lists(_candle(), min_size=n, max_size=n))


# Feature: order-flow-context, Property 9: Order_Flow_State is well-formed and matches the threshold mapping
@settings(max_examples=150, deadline=None)
@given(candles=_sufficient_candles())
def test_property_9_label_state_matches_mapping(candles):
    """Feature: order-flow-context, Property 9: Order_Flow_State is well-formed
    and matches the threshold mapping.

    A full Order_Flow_Label built from sufficient candles (proxy layer only,
    ``ticks=None``) carries a well-formed Order_Flow_State that equals the value
    the mapping tables dictate for its deciding signal.

    Validates: Requirements 3.1
    """
    result = classify_order_flow(candles, None, _CONFIG)

    # Sufficient clean candles -> a usable label (not an Unavailable_Marker).
    assert "order_flow_state" in result, f"expected a label, got {result!r}"
    state = result["order_flow_state"]
    assert state in _VALID_STATES, f"Order_Flow_State {state!r} not in {_VALID_STATES}"

    # No ticks were provided, so the proxy layer's buying-pressure ratio is the
    # deciding signal and tick_ofi is null.
    assert result["tick_ofi"] is None
    bpr = result["measures"]["buying_pressure_ratio"]
    expected = _expected_state(None, bpr, _CONFIG)
    assert state == expected, (
        f"label Order_Flow_State {state!r} != mapping-dictated {expected!r} "
        f"(buying_pressure_ratio={bpr!r})"
    )
