"""Property-based test for absent-direction neutral alignment (order_flow.py, task 4.8).

Feature: order-flow-context

This module implements design **Property 12: Absent proposed direction yields a
neutral alignment with the other fields present**:

    For any candle sequence (and optional tick sequence) sufficient to produce
    an Order_Flow_Label, invoking ``classify_order_flow`` with no proposed trade
    direction (``proposed_direction=None`` or an empty/whitespace string)
    returns a label whose ``alignment == "neutral"`` while still carrying the
    Order_Flow_State, the named Order_Flow_Proxy_Measures, the Tick_OFI (a finite
    number or null), and the ``live_tick_contributed`` flag — i.e. the other
    fields are present and well-formed.

Validates: Requirements 3.4, 3.5.

The strategy biases toward *classifiable* inputs — clean OHLCV records over a
sufficiently long sequence (and, on a fraction of examples, a clean tick
sequence) with the resolved config — so the Order_Flow_Label path is reached on
essentially every example. The sys.path / import pattern mirrors the sibling
``test_of_*_properties.py`` modules.
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
    resolve_order_flow_config,
)

# Resolve config once (identical on the tool and backtest paths). Drives the
# sufficiency gate and the thresholds.
_CONFIG = resolve_order_flow_config()

_VALID_STATES = {"buying", "selling", "balanced"}
_OF_MEASURE_FIELDS = (
    "candle_delta",
    "cvd_proxy",
    "up_volume",
    "down_volume",
    "buying_pressure_ratio",
)


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
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


# At least ``largest_lookback`` valid candles so a label (not a marker) is
# produced.
@st.composite
def _sufficient_candles(draw):
    n = draw(st.integers(min_value=_CONFIG.largest_lookback,
                         max_value=_CONFIG.largest_lookback + 25))
    return draw(st.lists(_candle(), min_size=n, max_size=n))


# A clean tick sequence with a monotonically increasing cumulative volume so a
# usable Tick_OFI can be produced (exercising the ``live_tick_contributed=True``
# branch). ``None`` exercises the proxy-only branch.
@st.composite
def _ticks(draw):
    n = draw(st.integers(min_value=_CONFIG.min_ticks, max_value=_CONFIG.min_ticks + 30))
    price = draw(st.floats(min_value=10.0, max_value=1000.0, allow_nan=False,
                           allow_infinity=False))
    cumulative = 0.0
    ticks = []
    for _ in range(n):
        price += draw(st.floats(min_value=-5.0, max_value=5.0, allow_nan=False,
                                allow_infinity=False))
        price = max(price, 1.0)
        cumulative += draw(st.floats(min_value=1.0, max_value=100.0,
                                     allow_nan=False, allow_infinity=False))
        spread = draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False,
                                allow_infinity=False))
        ticks.append({
            "last_price": price,
            "volume": cumulative,
            "best_bid": max(price - spread, 0.0),
            "best_ask": price + spread,
        })
    return ticks


# An "absent" proposed direction: ``None`` or an empty / whitespace string
# (all of which the calculator treats as no proposed direction -> neutral).
_ABSENT_DIRECTION = st.sampled_from([None, "", "   ", "\t"])


# ─────────────────────────────────────────────────────────────────────────────
# Property 12: Absent proposed direction yields a neutral alignment with the
#              other fields present
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 12: Absent proposed direction yields a neutral alignment with the other fields present
@settings(max_examples=150, deadline=None)
@given(
    candles=_sufficient_candles(),
    ticks=st.one_of(st.none(), _ticks()),
    direction=_ABSENT_DIRECTION,
)
def test_property_12_absent_direction_yields_neutral_alignment(candles, ticks, direction):
    """Feature: order-flow-context, Property 12: Absent proposed direction yields
    a neutral alignment with the other fields present.

    When ``classify_order_flow`` is invoked with no proposed trade direction and
    it returns an Order_Flow_Label, the Alignment is reported as ``neutral``
    while the Order_Flow_State, the named measures, the Tick_OFI, and the
    ``live_tick_contributed`` flag remain present and well-formed.

    Validates: Requirements 3.4, 3.5
    """
    result = classify_order_flow(
        candles,
        ticks,
        _CONFIG,
        proposed_direction=direction,
        symbol="RELIANCE",
        timeframe="15m",
    )

    assert isinstance(result, dict)

    # The property concerns the label path; the (rare) Unavailable_Marker path
    # satisfies it vacuously (no alignment/state present, by design).
    if result.get("unavailable"):
        return

    # Absent proposed direction must yield a neutral alignment (Requirement 3.4).
    assert result["alignment"] == "neutral", (
        f"absent proposed direction ({direction!r}) must yield neutral "
        f"alignment, got {result['alignment']!r}"
    )

    # ...while the Order_Flow_State remains present and well-formed (R3.4).
    assert result["order_flow_state"] in _VALID_STATES, (
        f"order_flow_state {result.get('order_flow_state')!r} not in "
        f"{_VALID_STATES}"
    )

    # The named Order_Flow_Proxy_Measures are present, each finite-or-null (R3.4).
    measures = result["measures"]
    assert isinstance(measures, dict)
    for field in _OF_MEASURE_FIELDS:
        assert field in measures, f"measure {field!r} missing from label"
        value = measures[field]
        assert value is None or isinstance(value, (int, float)), (
            f"measure {field!r} must be a finite number or null, got {value!r}"
        )

    # The Tick_OFI is present as a finite number or null (R3.4).
    tick_ofi = result["tick_ofi"]
    assert tick_ofi is None or isinstance(tick_ofi, (int, float)), (
        f"tick_ofi must be a finite number or null, got {tick_ofi!r}"
    )

    # The live-tick-contributed flag is present and a boolean (Requirement 3.5).
    assert isinstance(result["live_tick_contributed"], bool), (
        f"live_tick_contributed must be a boolean, got "
        f"{result.get('live_tick_contributed')!r}"
    )
    # The flag is true exactly when a usable Tick_OFI was produced (R3.5).
    assert result["live_tick_contributed"] == (tick_ofi is not None), (
        "live_tick_contributed must be true exactly when a usable Tick_OFI is "
        f"present (tick_ofi={tick_ofi!r}, flag="
        f"{result['live_tick_contributed']!r})"
    )
