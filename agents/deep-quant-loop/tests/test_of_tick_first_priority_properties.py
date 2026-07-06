"""Property-based test for tick-first priority over the candle proxies (order_flow.py, task 4.6).

Feature: order-flow-context

This module implements design **Property 10: A usable Tick_OFI takes priority
over the candle proxies**:

    When a usable (finite) Tick_OFI is present, ``classify_order_flow`` classifies
    the Order_Flow_State from the Tick_OFI thresholds (tick-first), OVERRIDING the
    state the candle-derived buying-pressure ratio alone would have produced; and
    the resulting label's ``live_tick_contributed`` flag is true.

Validates: Requirements 3.2, 3.5.

Strategy: construct cases where the live tick layer and the candle proxy layer
*disagree*, then assert the tick layer wins. Candle sequences are engineered so
the buying-pressure ratio dictates one Order_Flow_State (all-up candles ->
``buying``, all-down candles -> ``selling``, balanced alternating candles ->
``balanced``), while the tick sequence is engineered so the Tick_OFI dictates a
*different* state (strictly rising last-price upticks -> Tick_OFI ~ +1 ->
``buying``; strictly falling downticks -> Tick_OFI ~ -1 -> ``selling``). The tick
and proxy layers are paired so their states differ. The test then asserts the
produced label's Order_Flow_State equals the tick-derived state (not the
proxy-only state) and that ``live_tick_contributed`` is true.

The sys.path / import pattern mirrors the sibling ``test_of_*_properties.py``
modules. Candle sequences carry at least ``largest_lookback`` valid candles and
tick sequences carry at least ``min_ticks`` usable ticks, as resolved by
``resolve_order_flow_config()``.
"""

import math
import os
import sys

from hypothesis import assume, given, settings
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

# Resolve config once (identical on the tool and backtest paths). Drives both the
# Tick_OFI thresholds and the candle-derived pressure thresholds.
_CONFIG = resolve_order_flow_config()

_VALID_STATES = {"buying", "selling", "balanced"}


# ── Candle builders (proxy layer) ─────────────────────────────────────────────
# A candle closing above its open contributes its volume to up-volume; one
# closing below its open contributes to down-volume. The buying-pressure ratio is
# up_volume / (up_volume + down_volume) over the lookback window, so:
#   * all-up candles      -> ratio 1.0 -> proxy state ``buying``
#   * all-down candles    -> ratio 0.0 -> proxy state ``selling``
#   * balanced alternating-> ratio 0.5 -> proxy state ``balanced``


def _up_candle(vol):
    return {"open": 100.0, "high": 110.0, "low": 99.0, "close": 109.0, "volume": vol}


def _down_candle(vol):
    return {"open": 109.0, "high": 110.0, "low": 99.0, "close": 100.0, "volume": vol}


@st.composite
def _candles_for_proxy(draw, proxy_kind):
    """Build >= largest_lookback valid candles whose buying-pressure ratio
    dictates ``proxy_kind`` (one of buying/selling/balanced)."""
    n = draw(
        st.integers(
            min_value=_CONFIG.largest_lookback,
            max_value=_CONFIG.largest_lookback + 12,
        )
    )
    vol = draw(
        st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False)
    )
    if proxy_kind == "buying":
        return [_up_candle(vol) for _ in range(n)]
    if proxy_kind == "selling":
        return [_down_candle(vol) for _ in range(n)]
    # balanced: alternating up/down with equal per-candle volume. Any contiguous
    # window of a period-2 alternating sequence has an equal up/down split, so the
    # ratio over the lookback window is exactly 0.5.
    n2 = n if n % 2 == 0 else n + 1
    candles = []
    for i in range(n2):
        candles.append(_up_candle(vol) if i % 2 == 0 else _down_candle(vol))
    return candles


# ── Tick builders (live tick layer) ───────────────────────────────────────────
# Strictly rising last-price with strictly rising cumulative volume and no usable
# quote (best_bid == best_ask == 0 so the Lee-Ready refinement is skipped) makes
# every tick an uptick -> Tick_OFI = +1.0 -> tick state ``buying``. Strictly
# falling last-price makes every tick a downtick -> Tick_OFI = -1.0 -> ``selling``.


@st.composite
def _ticks_for(draw, tick_kind):
    """Build >= min_ticks usable ticks whose Tick_OFI dictates ``tick_kind``
    (one of buying/selling)."""
    n = draw(
        st.integers(min_value=_CONFIG.min_ticks + 1, max_value=_CONFIG.min_ticks + 20)
    )
    step = draw(
        st.floats(min_value=0.5, max_value=5.0, allow_nan=False, allow_infinity=False)
    )
    vstep = draw(
        st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False)
    )
    price = 500.0  # high enough to stay strictly positive while falling
    vol = 1000.0
    ticks = []
    for _ in range(n):
        price = price + step if tick_kind == "buying" else price - step
        vol += vstep
        ticks.append(
            {"last_price": price, "volume": vol, "best_bid": 0.0, "best_ask": 0.0}
        )
    return ticks


# ── Disagreement-case strategy ────────────────────────────────────────────────
# Pair a tick layer and a proxy layer whose dictated states differ, so the
# tick-first override is observable.


@st.composite
def _disagreement_case(draw):
    tick_kind = draw(st.sampled_from(["buying", "selling"]))
    if tick_kind == "buying":
        proxy_kind = draw(st.sampled_from(["selling", "balanced"]))
    else:
        proxy_kind = draw(st.sampled_from(["buying", "balanced"]))
    candles = draw(_candles_for_proxy(proxy_kind))
    ticks = draw(_ticks_for(tick_kind))
    return proxy_kind, tick_kind, candles, ticks


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (task 4.6): a usable Tick_OFI takes priority over the candle proxies
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 10: A usable Tick_OFI takes priority over the candle proxies
@settings(max_examples=200, deadline=None)
@given(case=_disagreement_case())
def test_property_10_tick_first_overrides_proxy(case):
    """Feature: order-flow-context, Property 10: A usable Tick_OFI takes priority
    over the candle proxies.

    With the tick layer and the candle proxy layer engineered to disagree, the
    produced Order_Flow_Label's Order_Flow_State equals the value dictated by the
    Tick_OFI thresholds (tick-first) and NOT the value the candle-derived
    buying-pressure ratio alone would have produced, and ``live_tick_contributed``
    is true.

    Validates: Requirements 3.2, 3.5
    """
    _proxy_kind, _tick_kind, candles, ticks = case
    config = _CONFIG

    result = classify_order_flow(candles, ticks, config)

    # Sufficient clean candles + usable ticks -> a usable label, not a marker.
    assert "order_flow_state" in result, f"expected a label, got {result!r}"
    state = result["order_flow_state"]
    assert state in _VALID_STATES, f"Order_Flow_State {state!r} not in {_VALID_STATES}"

    # The live tick layer contributed (Requirement 3.5).
    assert result["live_tick_contributed"] is True
    tick_ofi = result["tick_ofi"]
    assert tick_ofi is not None and math.isfinite(tick_ofi), (
        f"expected a usable finite Tick_OFI, got {tick_ofi!r}"
    )

    bpr = result["measures"]["buying_pressure_ratio"]

    # What the candle proxy layer ALONE would have produced (no tick layer).
    proxy_only_state = classify_order_flow_state(None, bpr, config)
    # What the tick layer dictates (tick-first ignores the proxy when finite).
    tick_state = classify_order_flow_state(tick_ofi, bpr, config)

    # Precondition of the property: the two layers genuinely disagree.
    assume(tick_state != proxy_only_state)

    # Tick-first priority (Requirement 3.2): the label follows the tick layer and
    # overrides what the proxy alone would have said.
    assert state == tick_state, (
        f"label Order_Flow_State {state!r} != tick-dictated {tick_state!r} "
        f"(tick_ofi={tick_ofi!r})"
    )
    assert state != proxy_only_state, (
        f"tick-first priority failed: label state {state!r} matched the "
        f"proxy-only state {proxy_only_state!r} (buying_pressure_ratio={bpr!r})"
    )
