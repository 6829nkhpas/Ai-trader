"""Property-based test that the calculator never emits a trade decision (order_flow.py, task 4.12).

Feature: order-flow-context

This module implements design **Property 37: The calculator never emits a trade
decision**:

    ``order_flow.classify_order_flow(...)`` produces ONLY an Order_Flow_Label or
    an Unavailable_Marker. Its result NEVER carries a trade-decision field — no
    ``action`` (BUY/SELL/HOLD), no ``conviction`` / ``conviction_score``, no
    ``entry`` / ``stop_loss`` / ``take_profit``, no ``decision`` / ``trade``
    field — and no string value anywhere within the result equals a BUY / SELL /
    HOLD action. Order Flow Context is a filter / context aid, not a trade
    generator.

Validates: Requirements 14.1, 14.3.

Candles and ticks are generated as dict-like records exactly as ``order_flow.py``
reads them via ``.get(...)``; a wide variety of candle counts, tick sequences,
and proposed directions is generated so that BOTH the Order_Flow_Label path and
the Unavailable_Marker path are exercised. The resolved configuration comes from
``resolve_order_flow_config()``. The sys.path / import pattern mirrors the
sibling ``test_of_*_properties.py`` modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (order_flow.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from order_flow import classify_order_flow, resolve_order_flow_config  # noqa: E402

# Resolve config once (identical on the tool and backtest paths). Its
# ``largest_lookback`` gate decides whether a given candle count yields a label
# or an Unavailable_Marker; generating counts on both sides of the gate drives
# both result paths.
_CONFIG = resolve_order_flow_config()

# Trade-decision fields that a label or marker must NEVER carry (Requirement
# 14.1, 14.3). Order flow emits a label/marker only — never a decision.
_FORBIDDEN_KEYS = frozenset(
    {
        "action",
        "conviction",
        "conviction_score",
        "entry",
        "stop_loss",
        "take_profit",
        "decision",
        "trade",
    }
)

# BUY / SELL / HOLD action words that must not appear as a value anywhere in the
# result (compared case-insensitively after stripping).
_ACTION_WORDS = frozenset({"BUY", "SELL", "HOLD"})


# ── Generators ────────────────────────────────────────────────────────────────
# Candles are dict-like OHLCV records; ``high >= low`` and open/close inside the
# range mirrors real data, while a zero span yields a degenerate (high == low)
# candle exercising the null close-location path.

_PRICE = st.floats(min_value=0.5, max_value=1e6, allow_nan=False, allow_infinity=False)
_SPAN = st.floats(min_value=0.0, max_value=1e5, allow_nan=False, allow_infinity=False)
_VOLUME = st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)


@st.composite
def _candle(draw):
    low = draw(_PRICE)
    high = low + draw(_SPAN)
    if high <= low:
        o = c = low
    else:
        o = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
        c = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    return {"open": o, "high": high, "low": low, "close": c, "volume": draw(_VOLUME)}


# Candle counts straddle the sufficiency gate so both the label path (>= gate)
# and the insufficient-data marker path (< gate) are generated.
_CANDLES = st.lists(
    _candle(),
    min_size=0,
    max_size=_CONFIG.largest_lookback + 10,
)


@st.composite
def _tick(draw):
    return {
        "last_price": draw(st.floats(min_value=0.5, max_value=1e6, allow_nan=False, allow_infinity=False)),
        "volume": draw(st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)),
        "best_bid": draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)),
        "best_ask": draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)),
    }


# Tick sequences range from absent/empty (Tick_OFI unavailable -> proxy-only or
# marker) through enough usable ticks to produce a Tick_OFI.
_TICKS = st.one_of(
    st.none(),
    st.lists(_tick(), min_size=0, max_size=_CONFIG.min_ticks + 15),
)

# A wide variety of proposed directions: absent, the three real actions in mixed
# case, blanks, and arbitrary junk. None of these may leak into the output as a
# decision.
_PROPOSED_DIRECTION = st.one_of(
    st.none(),
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", "hold", "Buy", "", "   ", "long", "short"]),
    st.text(max_size=8),
)

# Symbol / timeframe context the caller may attach. Kept to non-action-word
# values (the property concerns values the calculator produces, not arbitrary
# caller-echoed action strings).
_SYMBOL = st.one_of(st.none(), st.sampled_from(["RELIANCE", "TCS", "INFY", "NIFTY"]))
_TIMEFRAME = st.one_of(st.none(), st.sampled_from(["1m", "5m", "15m", "1h", "1d"]))


def _walk_strings_and_keys(obj):
    """Yield ``("key", k)`` for every mapping key and ``("value", v)`` for every
    leaf value reached by recursively walking dicts / lists / tuples in ``obj``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield ("key", k)
            yield from _walk_strings_and_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_strings_and_keys(item)
    else:
        yield ("value", obj)


# ─────────────────────────────────────────────────────────────────────────────
# Property 37 (task 4.12): the calculator never emits a trade decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 37: The calculator never emits a trade decision
@settings(max_examples=200, deadline=None)
@given(
    candles=_CANDLES,
    ticks=_TICKS,
    proposed_direction=_PROPOSED_DIRECTION,
    symbol=_SYMBOL,
    timeframe=_TIMEFRAME,
)
def test_property_37_calculator_never_emits_a_trade_decision(
    candles, ticks, proposed_direction, symbol, timeframe
):
    """Feature: order-flow-context, Property 37: The calculator never emits a
    trade decision.

    For any candle / tick / direction input (driving both the label and the
    Unavailable_Marker paths), ``classify_order_flow`` returns a dict that
    carries no trade-decision key and no BUY/SELL/HOLD action value anywhere
    within it.

    Validates: Requirements 14.1, 14.3
    """
    result = classify_order_flow(
        candles,
        ticks,
        _CONFIG,
        proposed_direction=proposed_direction,
        symbol=symbol,
        timeframe=timeframe,
    )

    # The calculator only ever emits a dict (a label or an Unavailable_Marker).
    assert isinstance(result, dict), f"result is not a dict: {result!r}"

    # It is exactly one of the two allowed shapes: an Unavailable_Marker (carries
    # ``unavailable``) OR an Order_Flow_Label (carries ``order_flow_state``).
    is_marker = result.get("unavailable") is True
    is_label = "order_flow_state" in result
    assert is_marker ^ is_label, (
        f"result is neither a clean marker nor a clean label: {result!r}"
    )

    # No trade-decision field appears at any nesting level (Requirement 14.1).
    for kind, item in _walk_strings_and_keys(result):
        if kind == "key" and isinstance(item, str):
            assert item.lower() not in _FORBIDDEN_KEYS, (
                f"forbidden trade-decision key {item!r} present in result: {result!r}"
            )

    # No string value anywhere within the result equals a BUY/SELL/HOLD action
    # (Requirements 14.1, 14.3) — even though a BUY/SELL/HOLD proposed_direction
    # may have been supplied as input, it never leaks out as a decision value.
    for kind, item in _walk_strings_and_keys(result):
        if kind == "value" and isinstance(item, str):
            assert item.strip().upper() not in _ACTION_WORDS, (
                f"BUY/SELL/HOLD action value {item!r} present in result: {result!r}"
            )

    # A marker omits state/alignment (no fabricated decision-like fields); a label
    # carries only the categorical context fields, none of which is an action.
    if is_marker:
        assert "order_flow_state" not in result
        assert "alignment" not in result
    else:
        assert result["order_flow_state"] in {"buying", "selling", "balanced"}
        assert result["alignment"] in {"aligned", "misaligned", "neutral"}
