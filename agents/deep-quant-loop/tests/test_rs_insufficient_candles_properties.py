"""Property-based test for insufficient-aligned-candle unavailability (rs.py, task 3.8).

Feature: relative-strength-context

This module implements design **Property 10: Insufficient aligned candles yield
an Unavailable_Marker with counts**:

    For any symbol candle sequence and benchmark candle sequence whose count of
    time-aligned valid candles is fewer than the configured minimum required for
    the longest lookback, ``classify_relative_strength`` returns an
    Unavailable_Marker whose reason identifies the insufficient-data condition
    and includes BOTH the count of aligned candles available and the configured
    minimum required, omits the Index_Direction / Relative_Strength_State /
    Alignment rather than fabricating them, leaves its inputs unmodified, and
    never raises.

Validates: Requirements 3.1, 5.2.

``classify_relative_strength`` gates on
``required = max(min_candles, largest_lookback)`` where the *aligned* candle
count is the number of timestamps common to BOTH the (cleaned) symbol and
benchmark sequences. The strategy below draws an arbitrary (internally
consistent) ``RSConfig``, computes that gate, then builds a symbol/benchmark
candle pair whose aligned valid-candle count is strictly below the gate by:

  * placing ``n_aligned`` clean candles at a shared pool of timestamps in BOTH
    sequences (these are the only candles that align), and
  * padding each sequence with clean candles at disjoint, non-overlapping
    timestamps and with dirty (non-finite / non-numeric) candles at a further
    disjoint pool — none of which add to the aligned count.

Disjoint integer timestamp pools guarantee the aligned count is exactly
``n_aligned`` regardless of the padding, covering both the "too few candles to
begin with" route and the "too few aligned after excluding non-overlapping /
non-finite candles" route.

The sys.path / import pattern mirrors the sibling ``test_rs_*_properties.py``
modules: the service directory (one level up) is prepended to ``sys.path`` so
``rs`` is importable when pytest runs from anywhere.
"""

import copy
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

# Finite price values in a sane, non-degenerate band so the shared candles are
# accepted by the parser (each field a finite number) and therefore count as
# valid, alignable candles.
_finite_price = st.floats(
    min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False
)

# Values that make a candle "dirty" — a non-finite or non-numeric OHLCV field
# that the parser rejects, so the carrying candle never aligns.
_bad_field = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), "x", None, "12.5", True, [], {}]
)

# Disjoint timestamp pools so padding can never accidentally align with the
# shared candles (which alone determine the aligned count).
_SHARED_TS_LO, _SHARED_TS_HI = 0, 999
_SYM_TS_LO, _SYM_TS_HI = 1_000, 1_999
_BENCH_TS_LO, _BENCH_TS_HI = 2_000, 2_999
_DIRTY_TS_LO, _DIRTY_TS_HI = 3_000, 3_999


@st.composite
def _clean_candle(draw, ts):
    """A well-formed OHLCV candle dict at timestamp ``ts`` with finite fields."""
    a = draw(_finite_price)
    b = draw(_finite_price)
    low = min(a, b)
    high = max(a, b)
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
    """A candle at ``ts`` carrying one non-finite/non-numeric required field so
    the parser excludes it (it never contributes to the aligned count)."""
    candle = draw(_clean_candle(ts))
    field = draw(st.sampled_from(["open", "high", "low", "close"]))
    candle[field] = draw(_bad_field)
    return candle


@st.composite
def _config(draw):
    """An arbitrary, internally consistent ``RSConfig`` with a modest gate.

    Lookbacks / windows / ``min_candles`` are bounded so the gate
    ``required = max(min_candles, largest_lookback)`` stays small, keeping the
    "fewer than required aligned candles" inputs cheap to generate while still
    exercising arbitrary parameter combinations. The strict ``laggard_cutoff <
    leader_cutoff`` ordering (which ``resolve_rs_config`` enforces) is preserved.
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


@st.composite
def _config_and_insufficient_aligned(draw):
    """Draw a config plus a symbol/benchmark pair with too few *aligned* candles.

    Returns ``(config, symbol_candles, benchmark_candles, n_aligned, required)``
    where:
      * ``required = max(min_candles, largest_lookback)`` is the gate,
      * ``n_aligned`` is drawn strictly below the gate (``0 <= n_aligned <
        required``),
      * ``symbol_candles`` / ``benchmark_candles`` share clean candles at exactly
        ``n_aligned`` common timestamps and are otherwise padded with clean
        candles at disjoint timestamps and dirty candles — so the time-aligned
        valid-candle count is exactly ``n_aligned``.
    """
    config = draw(_config())
    required = max(config.min_candles, config.largest_lookback)

    # Strictly fewer aligned candles than the gate (required >= 2).
    n_aligned = draw(st.integers(min_value=0, max_value=required - 1))

    # Shared clean candles at a common pool of timestamps: the ONLY candles that
    # align across both sequences.
    shared_ts = draw(
        st.lists(
            st.integers(min_value=_SHARED_TS_LO, max_value=_SHARED_TS_HI),
            min_size=n_aligned, max_size=n_aligned, unique=True,
        )
    )
    symbol_candles = [draw(_clean_candle(ts)) for ts in shared_ts]
    benchmark_candles = [draw(_clean_candle(ts)) for ts in shared_ts]

    # Symbol-only clean padding at a disjoint pool (never aligns with benchmark).
    sym_only_ts = draw(
        st.lists(st.integers(min_value=_SYM_TS_LO, max_value=_SYM_TS_HI),
                 max_size=15, unique=True)
    )
    symbol_candles += [draw(_clean_candle(ts)) for ts in sym_only_ts]

    # Benchmark-only clean padding at a disjoint pool (never aligns with symbol).
    bench_only_ts = draw(
        st.lists(st.integers(min_value=_BENCH_TS_LO, max_value=_BENCH_TS_HI),
                 max_size=15, unique=True)
    )
    benchmark_candles += [draw(_clean_candle(ts)) for ts in bench_only_ts]

    # Dirty padding at a disjoint pool, sprinkled into both sequences (excluded
    # before alignment, so they never add to the aligned count).
    dirty_ts = draw(
        st.lists(st.integers(min_value=_DIRTY_TS_LO, max_value=_DIRTY_TS_HI),
                 max_size=10, unique=True)
    )
    for ts in dirty_ts:
        if draw(st.booleans()):
            symbol_candles.append(draw(_dirty_candle(ts)))
        else:
            benchmark_candles.append(draw(_dirty_candle(ts)))

    # Shuffle so order carries no information (the calculator time-aligns).
    symbol_candles = draw(st.permutations(symbol_candles))
    benchmark_candles = draw(st.permutations(benchmark_candles))

    return config, list(symbol_candles), list(benchmark_candles), n_aligned, required


# A proposed trade direction (or its absence): the insufficient-data marker must
# arise regardless of the proposed direction.
_proposed_direction = st.sampled_from(["BUY", "SELL", "HOLD", "", None])


# ─────────────────────────────────────────────────────────────────────────────
# Property 10: Insufficient aligned candles yield an Unavailable_Marker with counts
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 10: Insufficient aligned candles yield an Unavailable_Marker with counts
@settings(max_examples=100, deadline=None)
@given(data=_config_and_insufficient_aligned(), proposed_direction=_proposed_direction)
def test_property_10_insufficient_aligned_candles_unavailable(data, proposed_direction):
    """Validates: Requirements 3.1, 5.2

    For any symbol/benchmark candle pair whose count of time-aligned valid
    candles is below the configured gate ``max(min_candles, largest_lookback)``,
    ``classify_relative_strength`` returns an Unavailable_Marker whose reason
    identifies the insufficient-data condition and includes BOTH the available
    aligned count and the required count, omits the Index_Direction /
    Relative_Strength_State / Alignment, leaves the inputs unmodified, and never
    raises.
    """
    config, symbol_candles, benchmark_candles, n_aligned, required = data

    # Snapshot the inputs to confirm purity (no mutation while classifying).
    symbol_snapshot = copy.deepcopy(symbol_candles)
    benchmark_snapshot = copy.deepcopy(benchmark_candles)

    # Must never raise (Requirements 3.1, 5.2): the call itself is the assertion.
    result = classify_relative_strength(
        symbol_candles, benchmark_candles, config,
        proposed_direction=proposed_direction,
        symbol="RELIANCE", benchmark="NIFTY 50", timeframe="15m",
    )

    # The result is an honest Unavailable_Marker.
    assert isinstance(result, dict)
    assert result.get("unavailable") is True, (
        f"expected an Unavailable_Marker for {n_aligned} aligned candles "
        f"(< required {required}); got {result!r}"
    )

    # The reason identifies the insufficient-data condition (Requirement 3.1) and
    # includes BOTH the count of aligned candles available and the configured
    # minimum required (Requirements 3.1, 5.2).
    reason = result.get("reason", "")
    assert isinstance(reason, str)
    assert "insufficient" in reason.lower(), (
        f"reason should identify the insufficient-data condition; got {reason!r}"
    )
    assert str(n_aligned) in reason, (
        f"reason should include the available aligned count {n_aligned}; got {reason!r}"
    )
    assert str(required) in reason, (
        f"reason should include the required count {required}; got {reason!r}"
    )

    # An Unavailable_Marker omits the classification fields rather than
    # fabricating them (Requirement 5.3 / design Unavailable_Marker schema).
    assert "index_direction" not in result
    assert "relative_strength_state" not in result
    assert "alignment" not in result

    # The inputs are left unmodified (purity — Requirements 1.1, 1.10).
    assert symbol_candles == symbol_snapshot, (
        "classify_relative_strength mutated its symbol candle input"
    )
    assert benchmark_candles == benchmark_snapshot, (
        "classify_relative_strength mutated its benchmark candle input"
    )
