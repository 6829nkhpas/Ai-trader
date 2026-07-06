"""Property-based test for order-flow classification determinism (order_flow.py, task 4.2).

Feature: order-flow-context

This module implements design **Property 1: Classification is deterministic**:

    For any candle sequence, tick sequence, resolved ``OrderFlowConfig``, and
    proposed trade direction, invoking ``classify_order_flow`` two or more times
    with element-wise-identical inputs returns results (an Order_Flow_Label or an
    Unavailable_Marker — including the Order_Flow_State, the Alignment, every
    named measure, the Tick_OFI, and the live-tick-contributed flag) that are
    element-wise identical across all invocations.

Validates: Requirements 1.6, 2.5.

The strategies generate arbitrary candle sequences (mixing clean OHLCV records
with candles carrying non-finite / non-numeric fields, short and long
sequences) and arbitrary tick sequences (dict-like ``last_price`` / ``volume`` /
``best_bid`` / ``best_ask`` records, some present-quote and some absent-quote,
some dirty), together with arbitrary proposed trade directions, so the property
exercises the Order_Flow_Label path (proxy-only and tick-contributed), the
Unavailable_Marker path, and the non-finite-exclusion path. Determinism is
asserted by classifying the *same* inputs repeatedly and requiring deep equality
of the results.

Candles and ticks are generated as dict-like records exactly as
``order_flow.py`` reads them via ``.get(...)``; the resolved configuration comes
from ``resolve_order_flow_config()``. The sys.path / import pattern mirrors the
sibling ``test_of_*_properties.py`` and ``test_rs_determinism_properties.py``
modules.
"""

import copy
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import classify_order_flow, resolve_order_flow_config  # noqa: E402

# Resolve config once (identical on the tool and backtest paths). The same
# resolved configuration is reused on every invocation so determinism is
# attributable to the classifier alone.
_CONFIG = resolve_order_flow_config()

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Finite price values in a sane, non-degenerate band so generated sequences
# frequently reach the Order_Flow_Label path (rather than only ever degenerating
# to an Unavailable_Marker). NaN / inf / non-numeric values are injected
# separately to drive the exclusion path (Requirement 4.2).
_finite_price = st.floats(
    min_value=0.5, max_value=10_000.0, allow_nan=False, allow_infinity=False
)
_finite_volume = st.floats(
    min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False
)

# Values that make a candle/tick "dirty" — a non-finite or non-numeric field
# that must be excluded from every computation (Requirement 4.2). Determinism
# must hold regardless of how many of these appear.
_bad_field = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), "x", None, "12.5", True]
)


@st.composite
def _clean_candle(draw):
    """A well-formed dict-like OHLCV candle with finite fields and ``high >= low``."""
    a = draw(_finite_price)
    b = draw(_finite_price)
    c = draw(_finite_price)
    d = draw(_finite_price)
    low = min(a, b, c, d)
    high = max(a, b, c, d)
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False,
                           allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False,
                           allow_infinity=False))
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": draw(_finite_volume),
    }


@st.composite
def _dirty_candle(draw):
    """A candle dict carrying at least one non-finite/non-numeric OHLCV field."""
    candle = draw(_clean_candle())
    field = draw(st.sampled_from(["open", "high", "low", "close", "volume"]))
    candle[field] = draw(_bad_field)
    return candle


@st.composite
def _candle(draw):
    """Mostly clean candles, occasionally dirty ones (exercise exclusion path)."""
    if draw(st.integers(min_value=0, max_value=9)) == 0:
        return draw(_dirty_candle())
    return draw(_clean_candle())


# Sequences range from too-short (drives the Unavailable_Marker path) to longer
# than the configured lookback (drives the Order_Flow_Label path).
_candle_sequence = st.lists(_candle(), min_size=0, max_size=60)


@st.composite
def _clean_tick(draw):
    """A well-formed dict-like tick with ``last_price`` / ``volume`` / quotes.

    The quote is either present (``bid > 0`` and ``ask >= bid``) so the Lee-Ready
    refinement engages, or absent (``0.0``) so it is skipped — both paths covered.
    ``volume`` is a per-tick cumulative value (the sequence builder keeps the
    sequence non-decreasing).
    """
    last_price = draw(_finite_price)
    if draw(st.booleans()):
        bid = draw(_finite_price)
        ask = bid + draw(st.floats(min_value=0.0, max_value=50.0,
                                   allow_nan=False, allow_infinity=False))
    else:
        bid = 0.0
        ask = 0.0
    return {"last_price": last_price, "best_bid": bid, "best_ask": ask, "volume": 0.0}


@st.composite
def _dirty_tick(draw):
    """A tick dict carrying at least one non-finite/non-numeric field."""
    tick = draw(_clean_tick())
    field = draw(st.sampled_from(["last_price", "volume", "best_bid", "best_ask"]))
    tick[field] = draw(_bad_field)
    return tick


@st.composite
def _tick_sequence(draw):
    """A chronological (oldest-first) tick sequence, mostly clean with some dirty.

    The cumulative ``volume`` is built as a running, non-decreasing sum of
    non-negative increments so the sequence is a realistic day's cumulative
    volume; dirty ticks (which carry a bad ``volume``) are excluded by the
    calculator and do not break the cumulative chain of the clean ticks.
    """
    n = draw(st.integers(min_value=0, max_value=40))
    cumulative = draw(_finite_price)
    ticks = []
    for _ in range(n):
        if draw(st.integers(min_value=0, max_value=9)) == 0:
            ticks.append(draw(_dirty_tick()))
            continue
        tick = draw(_clean_tick())
        cumulative += draw(st.floats(min_value=0.0, max_value=1e6,
                                     allow_nan=False, allow_infinity=False))
        tick["volume"] = cumulative
        ticks.append(tick)
    return ticks


# An optional tick sequence (``None`` is the backtest path's proxy-only input).
_optional_ticks = st.one_of(st.none(), _tick_sequence())

# A proposed trade direction (or its absence), so determinism is exercised across
# the BUY / SELL / HOLD / absent Alignment branches.
_proposed_direction = st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", "", None])


def _deep_equal(a, b):
    """Structural equality that treats NaN as equal to NaN.

    Order_Flow_Measures and the Tick_OFI are always a finite number or ``None``
    by construction, so a plain ``==`` suffices; this helper additionally treats
    two NaNs as equal purely as a defensive guard so a (non-)deterministic NaN
    would still be caught as a *difference* rather than masked by ``nan != nan``.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_deep_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return a == b
    return a == b


# ─────────────────────────────────────────────────────────────────────────────
# Property 1: Classification is deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 1: Classification is deterministic
@settings(max_examples=200, deadline=None)
@given(
    candles=_candle_sequence,
    ticks=_optional_ticks,
    proposed_direction=_proposed_direction,
)
def test_property_1_classification_is_deterministic(candles, ticks, proposed_direction):
    """Feature: order-flow-context, Property 1: Classification is deterministic.

    Invoking ``classify_order_flow`` repeatedly with element-wise-identical
    candles, ticks, config, and proposed direction returns element-wise-identical
    results (whether an Order_Flow_Label or an Unavailable_Marker), including the
    Order_Flow_State, the Alignment, every named measure, the Tick_OFI, and the
    live-tick-contributed flag.

    Validates: Requirements 1.6, 2.5
    """
    # Snapshot the inputs so we can confirm the calls did not mutate them (a
    # mutation would be a hidden source of non-determinism across invocations).
    candles_snapshot = copy.deepcopy(candles)
    ticks_snapshot = copy.deepcopy(ticks)

    first = classify_order_flow(
        candles, ticks, _CONFIG,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", timeframe="15m",
    )
    second = classify_order_flow(
        candles, ticks, _CONFIG,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", timeframe="15m",
    )
    third = classify_order_flow(
        candles, ticks, _CONFIG,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", timeframe="15m",
    )

    assert _deep_equal(first, second), (
        f"non-deterministic across invocations:\n first={first!r}\n second={second!r}"
    )
    assert _deep_equal(second, third), (
        f"non-deterministic across invocations:\n second={second!r}\n third={third!r}"
    )

    # Determinism must also hold for the bare (no symbol/timeframe) call shape:
    # the only difference between the two result families is the optional context
    # keys, never the states, measures, Tick_OFI, or alignment.
    bare_first = classify_order_flow(
        candles, ticks, _CONFIG, proposed_direction=proposed_direction,
    )
    bare_second = classify_order_flow(
        candles, ticks, _CONFIG, proposed_direction=proposed_direction,
    )
    assert _deep_equal(bare_first, bare_second), (
        f"non-deterministic (bare call):\n first={bare_first!r}\n "
        f"second={bare_second!r}"
    )

    # Inputs must be left unmodified across all invocations (purity underpins
    # determinism — Requirements 1.6, 2.5).
    assert _deep_equal(candles, candles_snapshot), (
        "classify_order_flow mutated its candle input"
    )
    assert _deep_equal(ticks, ticks_snapshot), (
        "classify_order_flow mutated its tick input"
    )
