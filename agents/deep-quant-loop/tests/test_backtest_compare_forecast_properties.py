"""Property-based test for comparison-mode consistency and metrics
(backtest.py ``compare_forecast``, task 14.6).

Feature: volatility-aware-forecaster

This module implements design **Property 35: Comparison-mode runs are consistent
and metrics are well-defined**:

    ``compare_forecast()`` runs the backtest WITH and WITHOUT the forecast filter
    over the IDENTICAL candle history and IDENTICAL setup rules (only
    ``forecast_filter_enabled`` differs). Because the filter only ever DROPS
    signals (those whose Forecast_Alignment is the available ``misaligned`` label
    for their direction) and RETAINS Unavailable_Marker signals, the with-forecast
    seeded set is a SUBSET of the without-forecast set, hence
    ``with_forecast.signals_scored <= without_forecast.signals_scored``. Each run's
    win-rate is ``winning_closed_trades / closed_trades`` — a float in [0.0, 1.0] —
    and expectancy is the mean realized R-multiple over closed trades, both
    reported as ``"n/a"`` EXACTLY when a run produced zero closed trades.

Validates: Requirements 13.3.

The test stays fully OFFLINE: a synthetic OHLCV candle series is passed directly
via ``compare_forecast(..., candles=...)`` so ``compare_forecast`` never fetches
the symbol candles. ``compare_forecast`` also resolves the Benchmark_Index candle
series internally via ``backtest._resolve_benchmark_candles`` (which may touch the
network / Rust tool server), so that resolver is monkeypatched to return ``None``
— relative strength then degrades to an honest Unavailable_Marker for every
signal, leaving the forecast filter as the only thing that differs between the two
runs. The REAL ``forecaster.forecast`` runs point-in-time over each window and
produces genuine forecast labels (some aligned/neutral, some misaligned),
exercising the filter's subset relationship with the real forecaster.

The sys.path / import pattern mirrors ``tests/test_rs_compare_properties.py``: the
service directory (one level up) is prepended to ``sys.path`` so ``backtest`` is
importable when pytest is run from anywhere.
"""

import math
import os
import sys
from unittest import mock

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / forecaster.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
from backtest import BacktestConfig  # noqa: E402

# A reduced-lookback config keeps each comparison run fast while still satisfying
# the signal rules (which need >= max(ema_slow, ols_window) + 1 == 22 closes) and
# the generate_and_score guard (n >= lookback + 2). Identical for both runs — the
# only difference compare_forecast introduces is the forecast_filter flag.
_CFG = BacktestConfig(lookback=30, cooldown_bars=2, profile_rows=12)


@st.composite
def _symbol_candles(draw):
    """A synthetic symbol OHLCV series long enough to emit signals.

    The series follows a bounded random walk on a strictly-monotonic
    ``timestamp_ms`` axis so the rule set produces a realistic mix of wins/losses
    and the real ``forecaster.forecast`` yields genuine forecast labels (some
    misaligned, exercising the filter).
    """
    n = draw(st.integers(min_value=45, max_value=140))
    base_ts = 1_700_000_000_000
    price = draw(st.floats(min_value=50.0, max_value=500.0,
                           allow_nan=False, allow_infinity=False))
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


def _assert_run_metrics_well_defined(run, label):
    """Assert one run's metrics obey Requirement 13.3."""
    closed = run["closed_trades"]
    winning = run["winning_closed_trades"]
    win_rate = run["win_rate"]
    expectancy = run["expectancy"]

    assert isinstance(closed, int) and closed >= 0, f"{label}: bad closed_trades {closed!r}"
    assert isinstance(winning, int) and 0 <= winning <= closed, (
        f"{label}: winning_closed_trades {winning!r} out of range for closed {closed!r}"
    )

    if closed == 0:
        # Zero closed trades => BOTH metrics are not-applicable (R13.3), never a
        # division by zero.
        assert win_rate == "n/a", f"{label}: expected win_rate 'n/a' for 0 closed, got {win_rate!r}"
        assert expectancy == "n/a", f"{label}: expected expectancy 'n/a' for 0 closed, got {expectancy!r}"
    else:
        # win_rate is a float in [0,1] equal to winning/closed (R13.3); 'n/a' is
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


# Feature: volatility-aware-forecaster, Property 35: Comparison-mode runs are consistent and metrics are well-defined
@settings(max_examples=100, deadline=None)
@given(candles=_symbol_candles())
def test_property_35_comparison_consistency_and_metrics(candles):
    """Validates: Requirements 13.3

    Over identical history/rules, both runs are reported, the with-forecast seeded
    set is a subset of the without-forecast set (signals_scored ordering), and each
    run's win-rate / expectancy are well-defined — a float in [0,1] equal to
    winning/closed and a finite float — or both ``"n/a"`` EXACTLY when zero trades
    closed.
    """
    # Avoid ANY network: compare_forecast resolves the Benchmark_Index candle
    # series internally via _resolve_benchmark_candles (which may hit the Rust tool
    # server). Force it to None so relative strength degrades to an honest
    # Unavailable_Marker and the forecast filter is the only differing factor. The
    # symbol candles are passed directly so the symbol fetch is never invoked.
    with mock.patch.object(backtest, "_resolve_benchmark_candles", return_value=None):
        result = backtest.compare_forecast(
            "TESTSYM", "1d",
            candles=candles, cfg=_CFG,
        )

    # Both runs are reported (R13.3).
    assert "with_forecast" in result, "comparison result missing the with_forecast run"
    assert "without_forecast" in result, "comparison result missing the without_forecast run"

    with_forecast = result["with_forecast"]
    without_forecast = result["without_forecast"]

    # Each run carries its scored-signal count and its metrics.
    assert "signals_scored" in with_forecast, "with_forecast missing signals_scored"
    assert "signals_scored" in without_forecast, "without_forecast missing signals_scored"

    # Subset relationship: the forecast filter only removes (never adds) signals,
    # and retains Unavailable_Marker signals (R13.3).
    assert with_forecast["signals_scored"] <= without_forecast["signals_scored"], (
        f"with_forecast signals {with_forecast['signals_scored']} exceeds "
        f"without_forecast {without_forecast['signals_scored']}"
    )

    # Per-run metrics are well-defined with the zero-closed-trades case reported as
    # 'n/a' exactly (R13.3).
    _assert_run_metrics_well_defined(with_forecast, "with_forecast")
    _assert_run_metrics_well_defined(without_forecast, "without_forecast")
