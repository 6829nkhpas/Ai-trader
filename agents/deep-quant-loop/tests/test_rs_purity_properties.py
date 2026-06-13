"""Property-based test for calculator purity (rs.py, task 3.3).

Feature: relative-strength-context

This module implements design **Property 2: Calculator functions are pure (no
input mutation)**:

    Every ``Relative_Strength_Calculator`` function — in particular the
    top-level ``classify_relative_strength`` — produces NO observable change to
    its input candle sequences or configuration. After a call, both the symbol
    and benchmark candle sequences must remain deep-equal to snapshots taken
    before the call, and the (frozen) ``RSConfig`` must remain equal to its
    pre-call snapshot.

Validates: Requirements 1.1, 1.10.

A candle is a dict-like OHLCV record carrying ``timestamp_ms`` plus
open/high/low/close/volume (matching how ``rs.py`` reads candles via
``c.get(...)``). The generator produces arbitrary sequences — including extreme
magnitudes, flat/zero-range bars, candles carrying non-finite / non-numeric
OHLCV fields, overlapping and non-overlapping timestamps, and sequences ranging
from too-short (the insufficient-data path) to long enough that every measure is
computable — so the purity guarantee is stressed across every code path,
including the degenerate ones (insufficient data, all-null measures, zero
denominators) where a careless implementation might mutate or normalize its
inputs in place.

The sys.path / import pattern mirrors the sibling ``test_rs_*_properties.py``
modules.
"""

import copy
import os
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from rs import (  # noqa: E402
    RSConfig,
    classify_relative_strength,
    resolve_rs_config,
)

# ─────────────────────────────────────────────────────────────────────────────
# Candle generation: arbitrary OHLCV records, including extreme / degenerate /
# corrupt values and overlapping / non-overlapping timestamps, so the purity
# guarantee is exercised across every code path (valid windows, flat bars,
# insufficient data, all-null measures, corrupt fields).
# ─────────────────────────────────────────────────────────────────────────────

_PRICE = st.one_of(
    st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1e-9, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([0.0, 1e-12, 1e12, 1.0, 100.0, 12345.6789]),
)

# Values that make an OHLCV field non-finite or non-numeric, so the carrying
# candle is excluded by ``time_align`` / the measure functions. Included so the
# purity property also covers the candle-exclusion path.
_BAD_VALUE = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), None, "x", "", True, False, [], {}]
)

# A small pool of timestamps so the symbol and benchmark sequences overlap on
# some timestamps (exercising the alignment path) and diverge on others.
_TIMESTAMP = st.integers(min_value=0, max_value=40)


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

    ts = draw(st.one_of(_TIMESTAMP, _BAD_VALUE)) if draw(
        st.integers(min_value=0, max_value=9)
    ) == 0 else draw(_TIMESTAMP)

    return {
        "timestamp_ms": ts,
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
    return {
        "timestamp_ms": draw(_TIMESTAMP),
        "open": p,
        "high": p,
        "low": p,
        "close": p,
        "volume": draw(_PRICE),
    }


# Sequences span from too-short (insufficient-data path) to long enough that
# every measure is computable.
_CANDLES = st.lists(
    st.one_of(_candle(), _flat_candle()),
    min_size=0,
    max_size=120,
)

# Proposed direction, including absent / non-directional values.
_DIRECTION = st.one_of(
    st.none(),
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", "", "weird"]),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 2: Calculator functions are pure (no input mutation)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 2: Calculator functions are pure (no input mutation)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.large_base_example],
)
@given(
    symbol_candles=_CANDLES,
    benchmark_candles=_CANDLES,
    proposed_direction=_DIRECTION,
)
def test_property_2_calculator_functions_are_pure(
    symbol_candles, benchmark_candles, proposed_direction
):
    """Validates: Requirements 1.1, 1.10

    ``classify_relative_strength`` leaves the provided symbol candle sequence,
    benchmark candle sequence, and configuration deep-equal to their pre-call
    snapshots — producing no observable change to any input. The candle
    sequences (and their candle dicts) are snapshotted with a deep copy before
    the call and asserted deep-equal afterward; the (frozen) ``RSConfig`` is
    compared by equality.
    """
    config = resolve_rs_config()
    assert isinstance(config, RSConfig)
    config_snapshot = config  # frozen dataclass -> compare by equality

    symbol_snapshot = copy.deepcopy(symbol_candles)
    benchmark_snapshot = copy.deepcopy(benchmark_candles)

    # Exercise the top-level entry point across both call shapes: with and
    # without symbol/benchmark/timeframe context, and with a proposed direction.
    classify_relative_strength(
        symbol_candles,
        benchmark_candles,
        config,
        proposed_direction=proposed_direction,
        symbol="RELIANCE",
        benchmark="NIFTY 50",
        timeframe="15m",
    )
    classify_relative_strength(symbol_candles, benchmark_candles, config)

    assert symbol_candles == symbol_snapshot, (
        "classify_relative_strength mutated its symbol candle input: "
        f"{symbol_candles!r} != {symbol_snapshot!r}"
    )
    assert benchmark_candles == benchmark_snapshot, (
        "classify_relative_strength mutated its benchmark candle input: "
        f"{benchmark_candles!r} != {benchmark_snapshot!r}"
    )
    assert config == config_snapshot, (
        "classify_relative_strength mutated its config input"
    )
