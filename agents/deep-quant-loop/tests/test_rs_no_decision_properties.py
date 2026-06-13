"""Property-based test that the calculator never emits a trade decision (rs.py, task 3.11).

Feature: relative-strength-context

This module implements design **Property 34: The calculator never emits a trade
decision**:

    The Relative_Strength_Calculator output (a Relative_Strength_Label or an
    Unavailable_Marker) never contains a BUY / SELL / HOLD action, a conviction,
    an order/side, or any other decision field. Its result is a pure *filter /
    context* artifact — never a trade generator (Requirement 13).

Validates: Requirements 13.1, 13.3.

The calculator output is constrained to relative-strength fields only:
  * Relative_Strength_Label: ``index_direction`` / ``relative_strength_state`` /
                             ``alignment`` / ``measures`` / ``benchmark`` /
                             ``symbol`` / ``timeframe`` / ``aligned_candles``
  * Unavailable_Marker:      ``unavailable`` / ``reason`` / ``symbol`` /
                             ``timeframe`` / ``benchmark``

So the property asserts, recursively over the whole result structure, that:
  * no key is one of the forbidden decision keys
    (``action`` / ``decision`` / ``conviction`` / ``signal`` / ``order`` /
     ``side`` / ``buy`` / ``sell`` / ``hold`` / ``trade``), and
  * no string value equals ``BUY`` / ``SELL`` / ``HOLD`` (case-insensitive),
and that classifying never raises.

Crucially the calculator is fed an arbitrary ``proposed_direction`` (including
``BUY`` / ``SELL`` / ``HOLD``): it MAY use that input to derive an Alignment
label, but it must never echo it back as an emitted action / decision anywhere
in its output.

The strategies generate arbitrary symbol/benchmark candle sequences (mixing
clean OHLCV records with candles carrying non-finite / non-numeric fields, short
and long sequences, overlapping and non-overlapping timestamps) together with
arbitrary ``RSConfig`` values and proposed trade directions, so the property
exercises BOTH the Relative_Strength_Label path and the Unavailable_Marker path.

The sys.path / import pattern mirrors the sibling ``test_rs_*_properties.py``
modules: the service directory (one level up) is prepended to ``sys.path`` so
``rs`` is importable when pytest runs from anywhere.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from rs import RSConfig, classify_relative_strength  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Forbidden decision artifacts (Requirements 13.1, 13.3)
# ─────────────────────────────────────────────────────────────────────────────

# Keys that would betray a trade decision / generator leaking into the
# relative-strength output. The calculator is a pure context filter, so NONE of
# these may appear anywhere in its result structure. Compared case-insensitively
# and by exact key name (so legitimate keys such as ``alignment`` /
# ``relative_strength_state`` / ``aligned_candles`` are never flagged).
_FORBIDDEN_KEYS = {
    "action",
    "decision",
    "conviction",
    "signal",
    "order",
    "side",
    "buy",
    "sell",
    "hold",
    "trade",
}

# String values that would constitute an emitted trade action. Compared
# case-insensitively against every string value in the result structure.
_FORBIDDEN_ACTION_VALUES = {"buy", "sell", "hold"}


# ─────────────────────────────────────────────────────────────────────────────
# Strategies (mirror the sibling rs property tests)
# ─────────────────────────────────────────────────────────────────────────────

# Finite price values kept in a sane, non-degenerate band so generated sequences
# frequently reach the Relative_Strength_Label path. NaN / inf are injected
# separately via the dirty-candle strategy.
_finite_price = st.floats(
    min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False
)

# Values that make a candle "dirty" — a non-finite or non-numeric OHLCV field
# that must be excluded from every measure computation (drives the marker path).
_bad_field = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), "x", None, "12.5", True]
)


@st.composite
def _clean_candle(draw, ts):
    """A well-formed OHLCV candle dict at timestamp ``ts`` with finite fields."""
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
        "timestamp_ms": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": draw(st.floats(min_value=0.0, max_value=1e9, allow_nan=False,
                                 allow_infinity=False)),
    }


@st.composite
def _dirty_candle(draw, ts):
    """A candle dict at ``ts`` carrying at least one non-finite/non-numeric field."""
    candle = draw(_clean_candle(ts))
    field = draw(st.sampled_from(["open", "high", "low", "close", "volume"]))
    candle[field] = draw(_bad_field)
    return candle


@st.composite
def _candle_at(draw, ts):
    """Mostly clean candles, occasionally dirty ones (exercise exclusion path)."""
    if draw(st.integers(min_value=0, max_value=9)) == 0:
        return draw(_dirty_candle(ts))
    return draw(_clean_candle(ts))


@st.composite
def _candle_sequence(draw):
    """A candle sequence over a set of (mostly shared) ascending timestamps.

    Timestamps are drawn from a shared pool so the symbol and benchmark sequences
    frequently overlap (driving the Relative_Strength_Label path), while short
    sequences and dirty candles still drive the Unavailable_Marker / exclusion
    paths.
    """
    timestamps = draw(
        st.lists(
            st.integers(min_value=0, max_value=200_000),
            min_size=0,
            max_size=80,
            unique=True,
        )
    )
    timestamps.sort()
    return [draw(_candle_at(ts)) for ts in timestamps]


@st.composite
def _config(draw):
    """An arbitrary, internally consistent ``RSConfig``.

    Lookbacks / windows / ``min_candles`` are kept small so the configured gate
    is frequently reachable by the generated sequences, letting the property
    cover both the label and the marker paths. The strict ``laggard_cutoff <
    leader_cutoff`` ordering is preserved here.
    """
    leader = draw(st.floats(min_value=-0.5, max_value=1.0, allow_nan=False,
                            allow_infinity=False))
    laggard = draw(st.floats(min_value=-1.0, max_value=leader - 0.001,
                             allow_nan=False, allow_infinity=False))
    return RSConfig(
        lookback=draw(st.integers(min_value=2, max_value=30)),
        corr_window=draw(st.integers(min_value=2, max_value=30)),
        leader_cutoff=leader,
        laggard_cutoff=laggard,
        index_flat_band=draw(st.floats(min_value=0.0, max_value=1.0,
                                       allow_nan=False, allow_infinity=False)),
        min_candles=draw(st.integers(min_value=2, max_value=40)),
    )


# A proposed trade direction (or its absence), so the no-decision guarantee is
# exercised across the BUY / SELL / HOLD / absent Alignment branches. Feeding a
# real action in confirms the calculator never echoes it back as an emitted
# decision.
_proposed_direction = st.sampled_from(
    ["BUY", "SELL", "HOLD", "buy", "sell", "hold", "", None, "LONG", "SHORT"]
)


# ─────────────────────────────────────────────────────────────────────────────
# Recursive inspection helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_forbidden_key(obj):
    """Return the first forbidden decision key found anywhere in ``obj``, else None.

    Walks dicts (keys + values), lists, and tuples recursively. Key comparison is
    case-insensitive and by exact key name.
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


def _find_forbidden_action_value(obj):
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
# Property 34: The calculator never emits a trade decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 34: The calculator never emits a trade decision
@settings(max_examples=100, deadline=None)
@given(
    symbol_candles=_candle_sequence(),
    benchmark_candles=_candle_sequence(),
    config=_config(),
    proposed_direction=_proposed_direction,
)
def test_property_34_calculator_never_emits_a_trade_decision(
    symbol_candles, benchmark_candles, config, proposed_direction
):
    """Validates: Requirements 13.1, 13.3

    For any symbol/benchmark candle pair, configuration, and proposed direction
    (including BUY / SELL / HOLD), ``classify_relative_strength`` returns a pure
    relative-strength artifact (Relative_Strength_Label or Unavailable_Marker)
    that contains no decision field (``action`` / ``decision`` / ``conviction`` /
    ``signal`` / ``order`` / ``side`` / ``buy`` / ``sell`` / ``hold`` /
    ``trade``) and no BUY / SELL / HOLD action value anywhere in its structure,
    and never raises.
    """
    # Classifying must never raise (the calculator is a pure, total filter).
    result = classify_relative_strength(
        symbol_candles, benchmark_candles, config,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", benchmark="NIFTY 50", timeframe="15m",
    )

    assert isinstance(result, dict), f"result is not a dict: {result!r}"

    # No decision/trade-generator key may appear anywhere in the result.
    forbidden_key = _find_forbidden_key(result)
    assert forbidden_key is None, (
        f"calculator output leaked a decision key {forbidden_key!r}: {result!r}"
    )

    # No string value may be a BUY / SELL / HOLD trade action — the calculator
    # must never echo the proposed_direction back as an emitted action.
    forbidden_value = _find_forbidden_action_value(result)
    assert forbidden_value is None, (
        f"calculator output leaked a trade action value {forbidden_value!r}: "
        f"{result!r}"
    )

    # Also exercise the bare (no symbol/benchmark/timeframe) call shape — same
    # guarantee must hold.
    bare = classify_relative_strength(
        symbol_candles, benchmark_candles, config,
        proposed_direction=proposed_direction,
    )
    assert _find_forbidden_key(bare) is None, (
        f"calculator output (bare call) leaked a decision key: {bare!r}"
    )
    assert _find_forbidden_action_value(bare) is None, (
        f"calculator output (bare call) leaked a trade action value: {bare!r}"
    )
