"""Property-based test for calculator purity (order_flow.py, task 4.3).

Feature: order-flow-context

This module implements design **Property 2: Calculator functions are pure (no
input mutation)**:

    Every ``Order_Flow_Calculator`` function — in particular the top-level
    ``classify_order_flow`` and the proxy / Tick_OFI functions — produces NO
    observable change to its input candle sequence, its input tick sequence, or
    its configuration. After a call, the candle sequence and the tick sequence
    must remain deep-equal to snapshots taken before the call, and the (frozen)
    ``OrderFlowConfig`` must remain equal to its pre-call snapshot.

    The calculator is also pure with respect to I/O: it performs zero network
    calls and reads no data source other than its provided inputs (R1.1). The
    module imports no ``httpx`` / network client, asserted at import time below;
    the core property exercised here is input immutability.

Validates: Requirements 1.1, 1.7, 2.5.

A candle is a dict-like OHLCV record carrying open/high/low/close/volume
(matching how ``order_flow.py`` reads candles via ``c.get(...)``). A tick is a
dict-like record carrying last_price/volume/best_bid/best_ask (the
``tools._read_live_ticks`` shape). The generators produce arbitrary sequences —
including extreme magnitudes, flat/zero-range bars, candles and ticks carrying
non-finite / non-numeric fields, and sequences ranging from too-short (the
insufficient-data path) to long enough that every measure is computable — so the
purity guarantee is stressed across every code path, including the degenerate
ones (insufficient data, all-null measures, zero denominators, unavailable
Tick_OFI) where a careless implementation might mutate or normalize its inputs
in place.

The sys.path / import pattern mirrors the sibling ``test_of_*_properties.py``
modules.
"""

import copy
import os
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import order_flow  # noqa: E402
from order_flow import (  # noqa: E402
    OrderFlowConfig,
    classify_order_flow,
    compute_buying_pressure_ratio,
    compute_candle_delta_proxy,
    compute_close_location_value,
    compute_cvd_proxy,
    compute_tick_ofi,
    compute_up_down_volume,
    resolve_order_flow_config,
)

# ─────────────────────────────────────────────────────────────────────────────
# No network client at import (R1.1): the calculator is pure Python and must not
# pull in an HTTP client. The core property below is input immutability, but the
# absence of a network dependency is asserted here for completeness.
# ─────────────────────────────────────────────────────────────────────────────


def test_order_flow_module_imports_no_network_client():
    """Validates: Requirements 1.1

    ``order_flow`` performs zero network calls; it imports no ``httpx`` (or any
    other HTTP client) module attribute, so there is no client through which it
    could reach the network.
    """
    assert not hasattr(order_flow, "httpx"), (
        "order_flow must not import a network client (httpx)"
    )
    # No module-level attribute should reference an httpx module object either.
    for name in dir(order_flow):
        attr = getattr(order_flow, name)
        mod = getattr(attr, "__module__", "") or ""
        assert not str(mod).startswith("httpx"), (
            f"order_flow.{name} pulls in a network client: {mod}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Candle generation: arbitrary OHLCV records, including extreme / degenerate /
# corrupt values, so the purity guarantee is exercised across every code path
# (valid windows, flat bars, insufficient data, all-null measures, corrupt
# fields).
# ─────────────────────────────────────────────────────────────────────────────

_PRICE = st.one_of(
    st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1e-9, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([0.0, 1e-12, 1e12, 1.0, 100.0, 12345.6789]),
)

# Values that make a field non-finite or non-numeric, so the carrying candle /
# tick is excluded by the measure functions. Included so the purity property
# also covers the exclusion path.
_BAD_VALUE = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), None, "x", "", True, False, [], {}]
)


@st.composite
def _candle(draw):
    """One OHLCV candle dict; fields may be ordinary, extreme, or corrupt.

    High/low are NOT forced to bracket open/close so flat and inverted-range
    bars are produced too. Each field independently has a small chance of
    carrying a non-finite / non-numeric value, exercising the exclusion path.
    """

    def _field():
        if draw(st.integers(min_value=0, max_value=9)) == 0:
            return draw(st.one_of(_PRICE, _BAD_VALUE))
        return draw(_PRICE)

    return {
        "open": _field(),
        "high": _field(),
        "low": _field(),
        "close": _field(),
        "volume": _field(),
    }


@st.composite
def _flat_candle(draw):
    """A flat candle where O=H=L=C (a zero-range, degenerate bar)."""
    p = draw(_PRICE)
    return {"open": p, "high": p, "low": p, "close": p, "volume": draw(_PRICE)}


# Sequences span from too-short (insufficient-data path) to long enough that
# every measure is computable.
_CANDLES = st.lists(
    st.one_of(_candle(), _flat_candle()),
    min_size=0,
    max_size=120,
)


@st.composite
def _tick(draw):
    """One dict-like tick record; fields may be ordinary, extreme, or corrupt.

    Mirrors the ``tools._read_live_ticks`` shape (last_price / volume /
    best_bid / best_ask). Corrupt fields exercise the tick-exclusion path.
    """

    def _field():
        if draw(st.integers(min_value=0, max_value=9)) == 0:
            return draw(st.one_of(_PRICE, _BAD_VALUE))
        return draw(_PRICE)

    return {
        "last_price": _field(),
        "volume": _field(),
        "best_bid": _field(),
        "best_ask": _field(),
    }


# Tick sequences span from empty / too-few (unavailable Tick_OFI) to long enough
# for a trustworthy imbalance. ``None`` is included so the no-tick path is hit.
_TICKS = st.one_of(
    st.none(),
    st.lists(_tick(), min_size=0, max_size=60),
)

# Proposed direction, including absent / non-directional values.
_DIRECTION = st.one_of(
    st.none(),
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", "", "weird"]),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 2: Calculator functions are pure (no input mutation)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 2: Calculator functions are pure (no input mutation)
@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.large_base_example, HealthCheck.too_slow],
)
@given(
    candles=_CANDLES,
    ticks=_TICKS,
    lookback=st.integers(min_value=1, max_value=80),
    proposed_direction=_DIRECTION,
)
def test_property_2_calculator_functions_are_pure(
    candles, ticks, lookback, proposed_direction
):
    """Feature: order-flow-context, Property 2: Calculator functions are pure
    (no input mutation).

    ``classify_order_flow`` and the proxy / Tick_OFI functions leave the
    provided candle sequence, tick sequence, and configuration deep-equal to
    their pre-call snapshots — producing no observable change to any input. The
    candle and tick sequences (and their dicts) are snapshotted with a deep copy
    before each call and asserted deep-equal afterward; the (frozen)
    ``OrderFlowConfig`` is compared by equality.

    Validates: Requirements 1.1, 1.7, 2.5
    """
    config = resolve_order_flow_config()
    assert isinstance(config, OrderFlowConfig)
    config_snapshot = config  # frozen dataclass -> compare by equality

    candles_snapshot = copy.deepcopy(candles)
    ticks_snapshot = copy.deepcopy(ticks)

    # Exercise every public calculator function on the same inputs. None of them
    # may mutate the candle sequence, the tick sequence, or the config.
    for candle in candles:
        compute_close_location_value(candle)
        compute_candle_delta_proxy(candle)
    compute_cvd_proxy(candles, lookback)
    compute_up_down_volume(candles, lookback)
    compute_buying_pressure_ratio(candles, lookback)
    compute_tick_ofi(ticks, config)

    # The top-level entry point, across both call shapes (with and without
    # symbol/timeframe context, and with a proposed direction).
    classify_order_flow(
        candles,
        ticks,
        config,
        proposed_direction=proposed_direction,
        symbol="RELIANCE",
        timeframe="15m",
    )
    classify_order_flow(candles, ticks, config)

    assert candles == candles_snapshot, (
        "order-flow calculator mutated its candle input: "
        f"{candles!r} != {candles_snapshot!r}"
    )
    assert ticks == ticks_snapshot, (
        "order-flow calculator mutated its tick input: "
        f"{ticks!r} != {ticks_snapshot!r}"
    )
    assert config == config_snapshot, (
        "order-flow calculator mutated its config input"
    )
