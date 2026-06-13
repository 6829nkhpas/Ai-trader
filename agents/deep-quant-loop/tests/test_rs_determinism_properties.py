"""Property-based test for relative-strength classification determinism (rs.py, task 3.2).

Feature: relative-strength-context

This module implements design **Property 1: Classification is deterministic**:

    For any symbol candle sequence, benchmark candle sequence, and resolved
    ``RSConfig``, invoking ``classify_relative_strength`` two or more times with
    element-wise identical inputs returns results (a Relative_Strength_Label or
    an Unavailable_Marker — including the Index_Direction, the
    Relative_Strength_State, the Alignment, every named measure, and the
    benchmark used) that are element-wise identical across all invocations.

Validates: Requirements 1.2.

The strategies generate arbitrary symbol/benchmark candle sequences (mixing
clean OHLCV records with candles carrying non-finite / non-numeric fields, short
and long sequences, overlapping and non-overlapping timestamps) together with
arbitrary ``RSConfig`` values and proposed trade directions, so the property
exercises the Relative_Strength_Label path, the Unavailable_Marker path, and the
non-finite-exclusion path. Determinism is asserted by classifying the *same*
inputs repeatedly and requiring deep equality of the results.

The sys.path / import pattern mirrors the sibling ``test_rs_*_properties.py``
and ``test_regime_*_properties.py`` modules: the service directory (one level
up) is prepended to ``sys.path`` so ``rs`` is importable when pytest runs from
anywhere.
"""

import copy
import math
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
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Finite price values kept in a sane, non-degenerate band so generated sequences
# frequently reach the Relative_Strength_Label path (rather than only ever
# degenerating to an Unavailable_Marker). NaN / inf are injected separately.
_finite_price = st.floats(
    min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False
)

# Values that make a candle "dirty" — a non-finite or non-numeric OHLCV field
# that must be excluded from every measure computation (Requirement 3.2). The
# determinism property must hold regardless of how many of these appear.
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
    paths. Order is ascending; ``classify_relative_strength`` time-aligns
    internally so any order is acceptable, but ascending mirrors real candle feeds.
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
    leader_cutoff`` ordering (which ``resolve_rs_config`` enforces) is preserved
    here, but the property only needs a valid ``RSConfig`` object.
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


# A proposed trade direction (or its absence), so determinism is exercised across
# the BUY / SELL / HOLD / absent Alignment branches.
_proposed_direction = st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", "", None])


def _deep_equal(a, b):
    """Structural equality that treats NaN as equal to NaN.

    Relative_Strength_Measures are always a finite number or ``None`` by
    construction, so a plain ``==`` suffices; this helper additionally treats two
    NaNs as equal purely as a defensive guard so a (non-)deterministic NaN would
    still be caught as a *difference* rather than masked by ``nan != nan``.
    """
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return a == b
    return a == b


# ─────────────────────────────────────────────────────────────────────────────
# Property 1: Classification is deterministic
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 1: Classification is deterministic
@settings(max_examples=100, deadline=None)
@given(
    symbol_candles=_candle_sequence(),
    benchmark_candles=_candle_sequence(),
    config=_config(),
    proposed_direction=_proposed_direction,
)
def test_property_1_classification_is_deterministic(
    symbol_candles, benchmark_candles, config, proposed_direction
):
    """Validates: Requirements 1.2

    Invoking ``classify_relative_strength`` repeatedly with element-wise
    identical symbol candles, benchmark candles, config, and proposed direction
    returns element-wise identical results (whether a Relative_Strength_Label or
    an Unavailable_Marker), including the Index_Direction, the
    Relative_Strength_State, the Alignment, every named measure, and the
    benchmark used.
    """
    # Snapshot the inputs so we can confirm the calls did not mutate them (a
    # mutation would be a hidden source of non-determinism across invocations).
    symbol_snapshot = copy.deepcopy(symbol_candles)
    benchmark_snapshot = copy.deepcopy(benchmark_candles)

    first = classify_relative_strength(
        symbol_candles, benchmark_candles, config,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", benchmark="NIFTY 50", timeframe="15m",
    )
    second = classify_relative_strength(
        symbol_candles, benchmark_candles, config,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", benchmark="NIFTY 50", timeframe="15m",
    )
    third = classify_relative_strength(
        symbol_candles, benchmark_candles, config,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", benchmark="NIFTY 50", timeframe="15m",
    )

    assert _deep_equal(first, second), (
        f"non-deterministic across invocations:\n first={first!r}\n second={second!r}"
    )
    assert _deep_equal(second, third), (
        f"non-deterministic across invocations:\n second={second!r}\n third={third!r}"
    )

    # Determinism must also hold for the bare (no symbol/benchmark/timeframe)
    # call shape: the only difference between the two result families is the
    # optional context keys, never the states, measures, or alignment.
    bare_first = classify_relative_strength(
        symbol_candles, benchmark_candles, config,
        proposed_direction=proposed_direction,
    )
    bare_second = classify_relative_strength(
        symbol_candles, benchmark_candles, config,
        proposed_direction=proposed_direction,
    )
    assert _deep_equal(bare_first, bare_second), (
        f"non-deterministic (bare call):\n first={bare_first!r}\n "
        f"second={bare_second!r}"
    )

    # Inputs must be left unmodified across all invocations (purity underpins
    # determinism — Requirement 1.2).
    assert symbol_candles == symbol_snapshot, (
        "classify_relative_strength mutated its symbol candle input"
    )
    assert benchmark_candles == benchmark_snapshot, (
        "classify_relative_strength mutated its benchmark candle input"
    )
