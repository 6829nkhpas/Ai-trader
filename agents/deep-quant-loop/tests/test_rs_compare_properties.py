"""Property-based test for comparison-mode consistency and metrics
(backtest.py ``compare_relative_strength``, task 13.6).

Feature: relative-strength-context

This module implements design **Property 29: Comparison-mode runs are consistent
and metrics are well-defined**:

    ``compare_relative_strength()`` runs the backtest WITH and WITHOUT the
    relative-strength filter over the IDENTICAL candle history, the IDENTICAL
    Benchmark_Index series, and IDENTICAL setup rules (only ``rs_filter_enabled``
    differs). Because the filter only ever DROPS signals (those whose
    relative-strength Alignment is the available ``misaligned`` label for their
    direction) and RETAINS Unavailable_Marker signals, the with-filter seeded set
    is a SUBSET of the without-filter set, hence
    ``with_filter.signals_scored <= without_filter.signals_scored``. Each run's
    win-rate is ``winning_closed_trades / closed_trades`` — a float in [0.0, 1.0]
    — and expectancy is the mean realized R-multiple over closed trades, both
    reported as ``"n/a"`` EXACTLY when a run produced zero closed trades.

Validates: Requirements 11.3, 11.4, 11.7.

The test stays fully OFFLINE: synthetic candle series for both the symbol and the
benchmark are passed directly via ``compare_relative_strength(..., candles=...,
benchmark_candles=...)`` so no Rust tool server / QuestDB is touched. The two
series share timestamps so the real ``rs.classify_relative_strength`` time-aligns
them and produces genuine relative-strength labels (some aligned/neutral, some
misaligned), exercising the filter's subset relationship with the real calculator.

The sys.path / import pattern mirrors ``tests/test_backtest_compare_properties.py``:
the service directory (one level up) is prepended to ``sys.path`` so ``backtest``
is importable when pytest is run from anywhere.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / rs.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
from backtest import BacktestConfig  # noqa: E402

# A reduced-lookback config keeps each comparison run fast while still satisfying
# the signal rules (which need >= max(ema_slow, ols_window) + 1 == 22 closes) and
# the generate_and_score guard (n >= lookback + 2). Identical for both runs — the
# only difference compare_relative_strength introduces is the rs_filter flag.
_CFG = BacktestConfig(lookback=30, cooldown_bars=2, profile_rows=12)


@st.composite
def _symbol_and_benchmark(draw):
    """A synthetic symbol + benchmark OHLCV pair sharing identical timestamps.

    Both series follow independent bounded random walks long enough to emit
    signals (so the rule set produces a realistic mix of wins/losses) and share a
    common, strictly-monotonic ``timestamp_ms`` axis so the real
    ``rs.classify_relative_strength`` time-aligns them and yields genuine
    relative-strength labels (some misaligned, exercising the filter).
    """
    n = draw(st.integers(min_value=45, max_value=140))
    base_ts = 1_700_000_000_000

    def _walk(start_price):
        price = start_price
        rows = []
        for i in range(n):
            step = draw(st.floats(min_value=-4.0, max_value=4.0,
                                  allow_nan=False, allow_infinity=False))
            new_price = max(1.0, price + step)
            o, c = price, new_price
            up = draw(st.floats(min_value=0.0, max_value=2.5,
                                allow_nan=False, allow_infinity=False))
            dn = draw(st.floats(min_value=0.0, max_value=2.5,
                                allow_nan=False, allow_infinity=False))
            hi = max(o, c) + up
            lo = max(0.5, min(o, c) - dn)
            rows.append({
                "open": o, "high": hi, "low": lo, "close": c,
                "volume": draw(st.floats(min_value=0.0, max_value=1e6,
                                         allow_nan=False, allow_infinity=False)),
                "timestamp_ms": base_ts + i * 60_000,
            })
            price = new_price
        return rows

    sym_start = draw(st.floats(min_value=50.0, max_value=500.0,
                               allow_nan=False, allow_infinity=False))
    bench_start = draw(st.floats(min_value=50.0, max_value=500.0,
                                 allow_nan=False, allow_infinity=False))
    return _walk(sym_start), _walk(bench_start)


def _assert_run_metrics_well_defined(run, label):
    """Assert one run's metrics obey Requirements 11.4 / 11.7."""
    closed = run["closed_trades"]
    winning = run["winning_closed_trades"]
    win_rate = run["win_rate"]
    expectancy = run["expectancy"]

    assert isinstance(closed, int) and closed >= 0, f"{label}: bad closed_trades {closed!r}"
    assert isinstance(winning, int) and 0 <= winning <= closed, (
        f"{label}: winning_closed_trades {winning!r} out of range for closed {closed!r}"
    )

    if closed == 0:
        # Zero closed trades => BOTH metrics are not-applicable (R11.7), never a
        # division by zero.
        assert win_rate == "n/a", f"{label}: expected win_rate 'n/a' for 0 closed, got {win_rate!r}"
        assert expectancy == "n/a", f"{label}: expected expectancy 'n/a' for 0 closed, got {expectancy!r}"
    else:
        # win_rate is a float in [0,1] equal to winning/closed (R11.4); 'n/a' is
        # reserved EXCLUSIVELY for the zero-closed case.
        assert win_rate != "n/a", f"{label}: win_rate 'n/a' with {closed} closed trades"
        assert expectancy != "n/a", f"{label}: expectancy 'n/a' with {closed} closed trades"
        assert isinstance(win_rate, float), f"{label}: win_rate not a float: {win_rate!r}"
        assert 0.0 <= win_rate <= 1.0, f"{label}: win_rate {win_rate!r} outside [0,1]"
        assert abs(win_rate - winning / closed) <= 1e-4, (
            f"{label}: win_rate {win_rate!r} != winning/closed {winning / closed!r}"
        )
        # expectancy is a finite float (mean realized R).
        assert isinstance(expectancy, float) and math.isfinite(expectancy), (
            f"{label}: expectancy not a finite float: {expectancy!r}"
        )


# Feature: relative-strength-context, Property 29: Comparison-mode runs are consistent and metrics are well-defined
@settings(max_examples=100, deadline=None)
@given(series=_symbol_and_benchmark())
def test_property_29_comparison_consistency_and_metrics(series):
    """Validates: Requirements 11.3, 11.4, 11.7

    Over identical history/rules/benchmark, both runs are reported, the
    with-filter seeded set is a subset of the without-filter set (signals_scored
    ordering), and each run's win-rate / expectancy are well-defined — a float in
    [0,1] equal to winning/closed and a finite float — or both ``"n/a"`` EXACTLY
    when zero trades closed.
    """
    candles, benchmark_candles = series

    result = backtest.compare_relative_strength(
        "TESTSYM", "1d",
        candles=candles, benchmark_candles=benchmark_candles,
        cfg=_CFG, benchmark="TESTBENCH",
    )

    # Both runs are reported (R11.3 / R11.4).
    assert "with_filter" in result, "comparison result missing the with_filter run"
    assert "without_filter" in result, "comparison result missing the without_filter run"

    with_filter = result["with_filter"]
    without_filter = result["without_filter"]

    # Subset relationship: the filter only removes (never adds) signals (R11.4).
    assert with_filter["signals_scored"] <= without_filter["signals_scored"], (
        f"with_filter signals {with_filter['signals_scored']} exceeds "
        f"without_filter {without_filter['signals_scored']}"
    )

    # Per-run metrics are well-defined (R11.4) with the zero-closed-trades case
    # reported as 'n/a' exactly (R11.7).
    _assert_run_metrics_well_defined(with_filter, "with_filter")
    _assert_run_metrics_well_defined(without_filter, "without_filter")
