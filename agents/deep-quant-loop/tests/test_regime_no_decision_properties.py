"""Property-based test that the classifier never emits a trade decision (task 3.9).

Feature: regime-detection-gate

This module implements design **Property 31: The classifier never emits a trade
decision**:

    The classifier output (a Regime_Label or an Unavailable_Marker) never
    contains a BUY / SELL / HOLD action, a conviction, or any other decision
    field. Its result is a pure *filter / calibration* artifact — never a trade
    generator.

Validates: Requirements 12.1, 12.3.

The classifier output is constrained to regime fields only:
  * Regime_Label:        ``trend_state`` / ``volatility_state`` / ``favorability``
                         / ``measures`` / ``symbol`` / ``timeframe`` /
                         ``candles_used``
  * Unavailable_Marker:  ``unavailable`` / ``reason`` / ``symbol`` / ``timeframe``

So the property asserts, recursively over the whole result structure, that:
  * no key is one of the forbidden decision keys
    (``action`` / ``decision`` / ``conviction`` / ``signal`` / ``side`` /
     ``order`` / ``trade``), and
  * no string value equals ``BUY`` / ``SELL`` / ``HOLD`` (case-insensitive),
and that classifying never raises.

The strategies below generate arbitrary candle sequences (mixing clean OHLCV
records with candles carrying non-finite / non-numeric fields, short and long
sequences) together with arbitrary ``RegimeConfig`` values, so the property
exercises BOTH the Regime_Label path and the Unavailable_Marker path.

The sys.path / import pattern mirrors the sibling regime property tests: the
service directory (one level up) is prepended to ``sys.path`` so ``regime`` is
importable when pytest is run from anywhere.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (regime.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from regime import RegimeConfig, classify_regime  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Forbidden decision artifacts (Requirement 12.1, 12.3)
# ─────────────────────────────────────────────────────────────────────────────

# Keys that would betray a trade decision / generator leaking into the regime
# output. The classifier is a filter, so NONE of these may appear anywhere in
# its result structure. Compared case-insensitively by exact key name.
_FORBIDDEN_KEYS = {
    "action",
    "decision",
    "conviction",
    "signal",
    "side",
    "order",
    "trade",
}

# String values that would constitute an emitted trade action. Compared
# case-insensitively against every string value in the result structure.
_FORBIDDEN_ACTION_VALUES = {"buy", "sell", "hold"}


# ─────────────────────────────────────────────────────────────────────────────
# Strategies (mirror the sibling regime property tests)
# ─────────────────────────────────────────────────────────────────────────────

# Finite price values kept in a sane, non-degenerate band so generated sequences
# frequently reach the Regime_Label path. NaN / inf are injected separately.
_finite_price = st.floats(
    min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False
)

# Values that make a candle "dirty" — a non-finite or non-numeric OHLCV field
# that must be excluded from every measure computation (drives the marker path).
_bad_field = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), "x", None, "12.5", True]
)


@st.composite
def _clean_candle(draw):
    """A well-formed OHLCV candle dict with finite numeric fields."""
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
        "volume": draw(st.floats(min_value=0.0, max_value=1e9, allow_nan=False,
                                 allow_infinity=False)),
    }


@st.composite
def _dirty_candle(draw):
    """A candle dict carrying at least one non-finite / non-numeric OHLCV field."""
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


# Variable-length sequences: short ones drive the Unavailable_Marker path, long
# ones drive the Regime_Label path. Both must be free of decision artifacts.
_candles = st.lists(_candle(), min_size=0, max_size=160)


@st.composite
def _config(draw):
    """An arbitrary, internally consistent ``RegimeConfig``.

    Lookback periods and the percentile window are kept small so the configured
    ``largest_lookback`` is frequently reachable by the generated sequences,
    letting the property cover both the label and the marker paths.
    """
    vol_low = draw(st.floats(min_value=0.0, max_value=80.0, allow_nan=False,
                             allow_infinity=False))
    vol_high = draw(st.floats(min_value=vol_low + 1.0, max_value=100.0,
                              allow_nan=False, allow_infinity=False))
    return RegimeConfig(
        adx_period=draw(st.integers(min_value=2, max_value=20)),
        chop_period=draw(st.integers(min_value=2, max_value=20)),
        vol_period=draw(st.integers(min_value=1, max_value=20)),
        vol_pctl_window=draw(st.integers(min_value=1, max_value=40)),
        bb_period=draw(st.integers(min_value=1, max_value=20)),
        adx_trend_cutoff=draw(st.floats(min_value=0.0, max_value=100.0,
                                        allow_nan=False, allow_infinity=False)),
        chop_ranging_cutoff=draw(st.floats(min_value=0.0, max_value=100.0,
                                           allow_nan=False, allow_infinity=False)),
        vol_low_pctl=vol_low,
        vol_high_pctl=vol_high,
        min_candles=draw(st.integers(min_value=1, max_value=60)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Recursive inspection helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_forbidden_key(obj) -> str | None:
    """Return the first forbidden decision key found anywhere in ``obj``, else None.

    Walks dicts (keys + values), lists, and tuples recursively. Key comparison is
    case-insensitive and by exact key name (so legitimate regime keys such as
    ``trend_state`` / ``favorability`` / ``candles_used`` are never flagged).
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key.strip().lower() in _FORBIDDEN_KEYS:
                return key
            found = _find_forbidden_key(value)
            if found is not None:
                return found
        return None
    if isinstance(obj, (list, tuple)):
        for item in obj:
            found = _find_forbidden_key(item)
            if found is not None:
                return found
    return None


def _find_forbidden_action_value(obj) -> str | None:
    """Return the first BUY/SELL/HOLD string value found in ``obj``, else None.

    Walks dicts (values), lists, and tuples recursively. String comparison is
    case-insensitive on the whole stripped string.
    """
    if isinstance(obj, str):
        if obj.strip().lower() in _FORBIDDEN_ACTION_VALUES:
            return obj
        return None
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_forbidden_action_value(value)
            if found is not None:
                return found
        return None
    if isinstance(obj, (list, tuple)):
        for item in obj:
            found = _find_forbidden_action_value(item)
            if found is not None:
                return found
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Property 31: The classifier never emits a trade decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 31
@settings(max_examples=200, deadline=None)
@given(candles=_candles, config=_config())
def test_property_31_classifier_never_emits_a_trade_decision(candles, config):
    """Validates: Requirements 12.1, 12.3

    For any candle sequence and configuration, ``classify_regime`` returns a
    pure regime artifact (Regime_Label or Unavailable_Marker) that contains no
    decision field (``action`` / ``decision`` / ``conviction`` / ``signal`` /
    ``side`` / ``order`` / ``trade``) and no BUY / SELL / HOLD action value
    anywhere in its structure, and never raises.
    """
    # Classifying must never raise (the classifier is a pure, total filter).
    result = classify_regime(candles, config, symbol="RELIANCE", timeframe="15m")

    assert isinstance(result, dict), f"result is not a dict: {result!r}"

    # No decision/trade-generator key may appear anywhere in the result.
    forbidden_key = _find_forbidden_key(result)
    assert forbidden_key is None, (
        f"classifier output leaked a decision key {forbidden_key!r}: {result!r}"
    )

    # No string value may be a BUY / SELL / HOLD trade action.
    forbidden_value = _find_forbidden_action_value(result)
    assert forbidden_value is None, (
        f"classifier output leaked a trade action value {forbidden_value!r}: "
        f"{result!r}"
    )

    # Also exercise the bare (no symbol/timeframe) call shape — same guarantee.
    bare = classify_regime(candles, config)
    assert _find_forbidden_key(bare) is None, (
        f"classifier output (bare call) leaked a decision key: {bare!r}"
    )
    assert _find_forbidden_action_value(bare) is None, (
        f"classifier output (bare call) leaked a trade action value: {bare!r}"
    )
