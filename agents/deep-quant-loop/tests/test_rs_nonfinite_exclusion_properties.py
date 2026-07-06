"""Property-based test for non-finite candle exclusion (rs.py, task 2.3).

Feature: relative-strength-context

This Hypothesis property exercises the candle-validation behaviour shared by
``time_align`` and every Relative_Strength_Measure function in ``rs.py``: a
candle carrying a non-finite or non-numeric OHLCV field (NaN / +/-inf / None /
non-numeric values such as strings, bools, or containers — including a corrupt
timestamp) is excluded from EVERY computation. So interleaving such corrupt
candles anywhere in an otherwise-valid pair of symbol/benchmark candle
sequences does not change any measure result, and never raises.

  * Property 8 (task 2.3) — Non-finite candles are excluded without affecting
                            the result: for a valid pair of time-aligned candle
                            sequences and any interleaving of candles carrying
                            non-finite / non-numeric OHLCV fields, ``time_align``
                            and each measure function return a result equal to
                            the result of computing on only the valid candles,
                            and never raise. (``classify_relative_strength`` is
                            asserted the same way when it is available.)

Validates: Requirements 3.2

The sys.path bootstrap and generator structure mirror
``tests/test_regime_nonfinite_properties.py``.
"""

import os
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import rs  # noqa: E402
from rs import (  # noqa: E402
    compute_beta,
    compute_correlation,
    compute_index_return,
    compute_relative_return,
    compute_rs_ratio_slope,
    time_align,
)

# Resolve the documented-default configuration once; its lookback (20) and
# correlation/beta window (30) drive the measure calls. Enough aligned candles
# make every measure computable while keeping input generation cheap.
_CONFIG = rs.resolve_rs_config()
_LOOKBACK = _CONFIG.lookback
_CORR_WINDOW = _CONFIG.corr_window

# Enough valid, fully-aligned candles to make every measure computable (the most
# demanding measure needs ``corr_window + 1`` = 31 aligned rows).
_MIN_VALID = 40
_MAX_VALID = 80

# Spacing between generated candle timestamps (ms). One trading-minute bars.
_STEP_MS = 60_000
_BASE_TS = 1_700_000_000_000


# ── Generators ────────────────────────────────────────────────────────────────

# Finite, positive, bounded close prices. Bounded so variance/covariance
# arithmetic stays well away from overflow while spanning a realistic range.
_price = st.floats(
    min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _valid_candle(draw, ts):
    """A dict OHLCV candle at timestamp ``ts`` whose every field is finite.

    ``rs._parse_row`` accepts this candle (every measure includes it). High/low
    are derived from open/close with a tiny fixed margin so the record is
    plausible; the measures only consume the close, so generation stays cheap.
    """
    open_ = draw(_price)
    close = draw(_price)
    high = max(open_, close) + 1.0
    low = max(min(open_, close) - 1.0, 0.5)
    return {
        "timestamp_ms": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
    }


# Values that make an OHLCV field non-finite or non-numeric. Each guarantees the
# carrying candle is excluded by ``rs._parse_row`` (NaN/inf fail ``isfinite``;
# None/str/bool/containers are non-numeric — note ``bool`` is excluded by the
# repo's ``_is_finite_number`` convention).
_bad_value = st.sampled_from(
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        None,
        "not-a-number",
        "",
        True,
        False,
        [],
        {},
    ]
)


@st.composite
def _bad_candle(draw):
    """A candle guaranteed to be excluded: at least one OHLCV field (or the
    timestamp) carries a non-finite / non-numeric value.

    Its timestamp is drawn from a range that overlaps the valid candles' span so
    the timestamp-collision path (a corrupt candle sharing a valid candle's
    timestamp) is exercised too — it must still be dropped, never overriding the
    valid row.
    """
    ts = draw(st.integers(min_value=_BASE_TS, max_value=_BASE_TS + _MAX_VALID * _STEP_MS))
    candle = dict(draw(_valid_candle(ts=ts)))
    # Corrupt at least one OHLCV field (the primary focus of Requirement 3.2).
    field = draw(st.sampled_from(["open", "high", "low", "close"]))
    candle[field] = draw(_bad_value)
    # Optionally also corrupt the volume and/or the timestamp.
    if draw(st.booleans()):
        candle["volume"] = draw(_bad_value)
    if draw(st.booleans()):
        candle["timestamp_ms"] = draw(_bad_value)
    return candle


@st.composite
def _clean_and_corrupted(draw):
    """Produce ``(clean_symbol, clean_benchmark, corr_symbol, corr_benchmark)``.

    The two clean sequences share an identical, strictly-ascending set of unique
    timestamps so they are fully time-aligned and every measure is computable.
    Each corrupted sequence is its clean counterpart with zero or more
    guaranteed-invalid candles inserted at arbitrary positions, so the valid
    candles retain their original relative order.
    """
    n = draw(st.integers(min_value=_MIN_VALID, max_value=_MAX_VALID))
    timestamps = [_BASE_TS + i * _STEP_MS for i in range(n)]

    clean_symbol = [draw(_valid_candle(ts=ts)) for ts in timestamps]
    clean_benchmark = [draw(_valid_candle(ts=ts)) for ts in timestamps]

    def _inject(clean):
        corrupted = list(clean)
        bad_candles = draw(st.lists(_bad_candle(), max_size=15))
        for bad in bad_candles:
            idx = draw(st.integers(min_value=0, max_value=len(corrupted)))
            corrupted.insert(idx, bad)
        return corrupted

    return clean_symbol, clean_benchmark, _inject(clean_symbol), _inject(clean_benchmark)


# ─────────────────────────────────────────────────────────────────────────────
# Property 8 (task 2.3): Non-finite candles are excluded without affecting result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 8: Non-finite candles are excluded without affecting the result
@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.large_base_example])
@given(data=_clean_and_corrupted())
def test_property_8_non_finite_candles_excluded(data):
    """Feature: relative-strength-context, Property 8: Non-finite candles are
    excluded without affecting the result — for a valid pair of candle sequences
    and any interleaving of candles carrying non-finite / non-numeric OHLCV
    fields, ``time_align`` and every measure function return a result equal to
    the result of computing on only the valid candles, and never raise.

    Validates: Requirements 3.2
    """
    clean_sym, clean_bench, corr_sym, corr_bench = data

    # ``time_align`` itself must drop the corrupt candles before intersection, so
    # the aligned rows are identical whether or not the corrupt candles were
    # interleaved (and even when a corrupt candle collided with a valid
    # timestamp, the valid row is never overridden).
    clean_rows = time_align(clean_sym, clean_bench)
    corr_rows = time_align(corr_sym, corr_bench)
    assert corr_rows == clean_rows

    clean_sym_rows, clean_bench_rows = clean_rows
    corr_sym_rows, corr_bench_rows = corr_rows

    # Each measure must yield an identical result on the corrupted sequences as
    # on the clean sequences, and neither call may raise.
    assert compute_rs_ratio_slope(corr_sym_rows, corr_bench_rows, _LOOKBACK) == \
        compute_rs_ratio_slope(clean_sym_rows, clean_bench_rows, _LOOKBACK)
    assert compute_relative_return(corr_sym_rows, corr_bench_rows, _LOOKBACK) == \
        compute_relative_return(clean_sym_rows, clean_bench_rows, _LOOKBACK)
    assert compute_correlation(corr_sym_rows, corr_bench_rows, _CORR_WINDOW) == \
        compute_correlation(clean_sym_rows, clean_bench_rows, _CORR_WINDOW)
    assert compute_beta(corr_sym_rows, corr_bench_rows, _CORR_WINDOW) == \
        compute_beta(clean_sym_rows, clean_bench_rows, _CORR_WINDOW)
    assert compute_index_return(corr_bench_rows, _LOOKBACK) == \
        compute_index_return(clean_bench_rows, _LOOKBACK)

    # classify_relative_strength is the top-level entry point (added in a later
    # task). When present, it must obey the same exclusion invariant: classifying
    # the corrupted sequences equals classifying only the valid candles, and it
    # never raises.
    classify = getattr(rs, "classify_relative_strength", None)
    if callable(classify):
        assert classify(corr_sym, corr_bench, _CONFIG) == \
            classify(clean_sym, clean_bench, _CONFIG)
