"""Property-based test that the forecaster never emits a trade decision (forecaster.py, task 4.9).

Feature: volatility-aware-forecaster

This module implements design **Property 38: The forecaster never emits a trade
decision**:

    ``forecaster.forecast(candles, config, ...)`` produces ONLY a Forecast_Label
    or an Unavailable_Marker. Its result NEVER carries a trade-decision field —
    no ``action`` (BUY/SELL/HOLD), no ``conviction`` / ``conviction_score``, no
    ``decision`` / ``trade``, no ``entry`` / ``stop_loss`` / ``take_profit`` /
    ``execution_plan`` — and no string value anywhere within the result equals a
    BUY / SELL / HOLD action (even when a BUY/SELL/HOLD ``proposed_direction`` is
    supplied as input). The Volatility_Aware_Forecaster is a predictive
    cross-check and calibration aid, never a trade generator.

    Additionally, when present, the categorical ``projected_direction`` is only
    ever one of ``up`` / ``down`` / ``flat`` — never a BUY/SELL/HOLD action.

Validates: Requirements 15.1, 15.2, 15.3.

A candle is a dict-like OHLCV record carrying open/high/low/close/volume
(matching how ``forecaster.py`` reads candles through ``regime``'s validation
helpers). The generators produce sequences ranging from too-short (the
insufficient-data Unavailable_Marker path), through flat / zero-variance windows
(the short-circuit path), to long enough that every measure is computable (the
full Forecast_Label path), paired with a wide variety of ``proposed_direction``
values (absent, the three real actions in mixed case, blanks, order sides, and
junk). The resolved configuration comes from ``resolve_forecaster_config()``.

The sys.path / import pattern mirrors the sibling
``test_forecaster_*_properties.py`` and ``test_of_no_trade_decision_properties.py``
modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from forecaster import forecast, resolve_forecaster_config  # noqa: E402

# Resolve config once (identical on the tool / backtest / calibration paths). Its
# sufficiency gate decides whether a given candle count yields a Forecast_Label
# or an Unavailable_Marker; generating counts on both sides of the gate drives
# both result paths.
_CONFIG = resolve_forecaster_config()

# Trade-decision fields that a label or marker must NEVER carry (Requirements
# 15.1, 15.3). The forecaster emits a label/marker only — never a decision.
_FORBIDDEN_KEYS = frozenset(
    {
        "action",
        "conviction",
        "conviction_score",
        "decision",
        "entry",
        "stop_loss",
        "take_profit",
        "execution_plan",
        "trade",
    }
)

# BUY / SELL / HOLD action words that must not appear as a value anywhere in the
# result (compared case-insensitively after stripping).
_ACTION_WORDS = frozenset({"BUY", "SELL", "HOLD"})

# The only legal Projected_Direction values (Requirement 15.1 — never an action).
_PROJECTED_DIRECTIONS = frozenset({"up", "down", "flat"})


# ── Generators ────────────────────────────────────────────────────────────────
# Candles are dict-like OHLCV records; ``high >= low`` and open/close inside the
# range mirrors real data, while a zero span yields a degenerate (flat,
# zero-variance) candle exercising the volatility short-circuit path.

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


@st.composite
def _flat_candle(draw):
    """A flat candle where O=H=L=C (a zero-range, zero-variance bar)."""
    p = draw(_PRICE)
    return {"open": p, "high": p, "low": p, "close": p, "volume": draw(_VOLUME)}


# Candle counts straddle the sufficiency gate so both the label path (>= gate)
# and the insufficient-data marker path (< gate) are generated; flat candles mix
# in to exercise the zero-variance short-circuit.
_CANDLES = st.lists(
    st.one_of(_candle(), _flat_candle()),
    min_size=0,
    max_size=_CONFIG.largest_lookback + 15,
)

# A wide variety of proposed directions: absent, the three real actions in mixed
# case, blanks, order sides, and arbitrary junk. None of these may leak into the
# output as a decision value.
_PROPOSED_DIRECTION = st.one_of(
    st.none(),
    st.sampled_from(
        ["BUY", "SELL", "HOLD", "buy", "sell", "hold", "Buy", "", "   ", "long", "short"]
    ),
    st.text(max_size=8),
)

# Symbol / timeframe context the caller may attach. Kept to non-action-word
# values (the property concerns values the forecaster produces, not arbitrary
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
# Property 38 (task 4.9): the forecaster never emits a trade decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 38: The forecaster never emits a trade decision
@settings(max_examples=200, deadline=None)
@given(
    candles=_CANDLES,
    proposed_direction=_PROPOSED_DIRECTION,
    symbol=_SYMBOL,
    timeframe=_TIMEFRAME,
)
def test_property_38_forecaster_never_emits_a_trade_decision(
    candles, proposed_direction, symbol, timeframe
):
    """Feature: volatility-aware-forecaster, Property 38: The forecaster never
    emits a trade decision.

    For any candle / direction input (driving both the Forecast_Label and the
    Unavailable_Marker paths), ``forecast`` returns a dict that carries no
    trade-decision key and no BUY/SELL/HOLD action value anywhere within it, and
    any ``projected_direction`` present is only ever ``up`` / ``down`` / ``flat``.

    Validates: Requirements 15.1, 15.2, 15.3
    """
    result = forecast(
        candles,
        _CONFIG,
        proposed_direction=proposed_direction,
        symbol=symbol,
        timeframe=timeframe,
    )

    # The forecaster only ever emits a dict (a label or an Unavailable_Marker).
    assert isinstance(result, dict), f"result is not a dict: {result!r}"

    # It is exactly one of the two allowed shapes: an Unavailable_Marker (carries
    # ``unavailable``) OR a Forecast_Label (carries ``projected_direction``).
    is_marker = result.get("unavailable") is True
    is_label = "projected_direction" in result
    assert is_marker ^ is_label, (
        f"result is neither a clean marker nor a clean label: {result!r}"
    )

    # No trade-decision field appears at any nesting level (Requirements 15.1,
    # 15.3).
    for kind, item in _walk_strings_and_keys(result):
        if kind == "key" and isinstance(item, str):
            assert item.lower() not in _FORBIDDEN_KEYS, (
                f"forbidden trade-decision key {item!r} present in result: {result!r}"
            )

    # No string value anywhere within the result equals a BUY/SELL/HOLD action
    # (Requirements 15.1, 15.2, 15.3) — even though a BUY/SELL/HOLD
    # proposed_direction may have been supplied as input, it never leaks out as a
    # decision value.
    for kind, item in _walk_strings_and_keys(result):
        if kind == "value" and isinstance(item, str):
            assert item.strip().upper() not in _ACTION_WORDS, (
                f"BUY/SELL/HOLD action value {item!r} present in result: {result!r}"
            )

    if is_marker:
        # A marker omits every projection field (no fabricated decision-like
        # fields).
        assert "projected_direction" not in result
        assert "up_probability" not in result
        assert "expected_move_atr" not in result
        assert "forecast_confidence" not in result
        assert "forecast_alignment" not in result
    else:
        # A label's categorical direction is only ever up/down/flat — never an
        # action (Requirement 15.1).
        assert result["projected_direction"] in _PROJECTED_DIRECTIONS, (
            f"projected_direction is not up/down/flat: {result['projected_direction']!r}"
        )
