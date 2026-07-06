"""Example-based smoke test for comparison-mode relative-strength backtest
(backtest.py ``compare_relative_strength``, task 14.3).

Feature: relative-strength-context

This is a DETERMINISTIC, example-based smoke test (NOT a property test) that
exercises ``backtest.compare_relative_strength()`` end-to-end, fully OFFLINE: a
fixed, hand-pinned OHLCV candle fixture for the symbol and a matching benchmark
series sharing the SAME timestamps are passed directly via
``compare_relative_strength(..., candles=..., benchmark_candles=...)`` so no Rust
tool server / QuestDB is touched and the REAL ``rs.classify_relative_strength``
time-aligns the two series and produces genuine relative-strength labels.

It asserts the documented comparison-mode contract (R11.2, R11.4):
  * the result carries both ``with_filter`` and ``without_filter`` summaries,
    each with ``signals_scored`` and the documented metric fields;
  * ``with_filter.signals_scored <= without_filter.signals_scored`` — the
    with-filter seeded set is a SUBSET of the without-filter set because the
    filter only ever DROPS signals whose relative-strength Alignment is the
    available ``misaligned`` label for their direction (R11.2);
  * both runs report the SAME ``candles`` count (identical history);
  * each run's ``win_rate`` / ``expectancy`` are well-formed — a float (with
    win_rate in [0.0, 1.0]) or the string ``"n/a"`` when zero trades closed
    (R11.4 / R11.7).

The fixtures are built so the symbol RISES while the benchmark FALLS over the
lookback windows: the symbol is then a leader and any SELL-side signals it
generates are ``misaligned`` against the (down) index, so the with-filter run
genuinely drops at least one signal and the subset relationship is non-trivial.

The sys.path / import pattern mirrors the sibling tests (the service directory
one level up is prepended to ``sys.path`` so ``backtest`` imports cleanly).
"""

import math
import os
import random
import sys

# Make the service package importable (backtest.py / rs.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
from backtest import BacktestConfig  # noqa: E402

# Reduced-lookback config keeps the run fast while still satisfying the signal
# rules (need >= max(ema_slow=21, ols_window=20) + 1 == 22 closes per window) and
# the generate_and_score guard (n >= lookback + 2). Identical for both runs —
# compare_relative_strength() only flips the rs_filter flag between them.
_CFG = BacktestConfig(lookback=30, cooldown_bars=2, profile_rows=12)

# Documented metric fields each run summary must carry.
_METRIC_FIELDS = ("closed_trades", "winning_closed_trades", "win_rate", "expectancy")

_BASE_TS = 1_700_000_000_000
_BAR_MS = 60_000  # 1m bars; both series share this axis so they time-align.


def _build_fixtures():
    """Build fixed, deterministic symbol + benchmark OHLCV series sharing timestamps.

    Two SEEDED random walks (fixed seeds -> identical bytes on every run) give
    price paths with enough dispersion for the deterministic rule set to emit
    several signals. The symbol drifts UP while the benchmark drifts DOWN so the
    real relative-strength calculator labels the symbol a leader against a falling
    index — making SELL-side signals ``misaligned`` so the with-filter run drops
    at least one. Both series share the same strictly-monotonic ``timestamp_ms``
    axis so ``rs.classify_relative_strength`` time-aligns them cleanly.
    """

    def _walk(seed, start_price, drift):
        rng = random.Random(seed)
        price = start_price
        rows = []
        for i in range(140):
            step = rng.uniform(-4.0, 4.0) + drift
            new_price = max(1.0, price + step)
            o, c = price, new_price
            hi = max(o, c) + rng.uniform(0.0, 2.5)
            lo = max(0.5, min(o, c) - rng.uniform(0.0, 2.5))
            rows.append({
                "timestamp_ms": _BASE_TS + i * _BAR_MS,
                "open": round(o, 4),
                "high": round(hi, 4),
                "low": round(lo, 4),
                "close": round(c, 4),
                "volume": round(rng.uniform(1e3, 1e6), 2),
            })
            price = new_price
        return rows

    symbol_candles = _walk(seed=20240517, start_price=150.0, drift=0.35)
    benchmark_candles = _walk(seed=99887766, start_price=400.0, drift=-0.35)
    return symbol_candles, benchmark_candles


def _assert_run_well_formed(run, label):
    """Assert one run carries ``signals_scored`` + the metric fields, well-formed."""
    assert "signals_scored" in run, f"{label}: missing signals_scored; got {sorted(run.keys())}"
    for field in _METRIC_FIELDS:
        assert field in run, f"{label}: missing metric field {field!r}; got {sorted(run.keys())}"

    scored = run["signals_scored"]
    closed = run["closed_trades"]
    winning = run["winning_closed_trades"]
    win_rate = run["win_rate"]
    expectancy = run["expectancy"]

    assert isinstance(scored, int) and scored >= 0, f"{label}: bad signals_scored {scored!r}"
    assert isinstance(closed, int) and closed >= 0, f"{label}: bad closed_trades {closed!r}"
    assert isinstance(winning, int) and 0 <= winning <= closed, (
        f"{label}: winning_closed_trades {winning!r} out of range for closed {closed!r}"
    )

    if closed == 0:
        # Zero closed trades => both metrics not-applicable (R11.7), never a
        # division by zero.
        assert win_rate == "n/a", f"{label}: expected win_rate 'n/a', got {win_rate!r}"
        assert expectancy == "n/a", f"{label}: expected expectancy 'n/a', got {expectancy!r}"
    else:
        assert isinstance(win_rate, float), f"{label}: win_rate not a float: {win_rate!r}"
        assert 0.0 <= win_rate <= 1.0, f"{label}: win_rate {win_rate!r} outside [0,1]"
        assert isinstance(expectancy, float) and math.isfinite(expectancy), (
            f"{label}: expectancy not a finite float: {expectancy!r}"
        )


def test_comparison_mode_relative_strength_smoke():
    """Validates: Requirements 11.2, 11.4

    A comparison-mode relative-strength backtest over a fixed candle fixture
    produces with-filter and without-filter summaries — each with
    ``signals_scored`` and the metric fields — with the expected subset
    relationship and well-formed metrics, fully offline.
    """
    symbol_candles, benchmark_candles = _build_fixtures()
    expected_count = len(symbol_candles)

    result = backtest.compare_relative_strength(
        "TESTSYM", "1m",
        candles=symbol_candles, benchmark_candles=benchmark_candles,
        cfg=_CFG, benchmark="TESTBENCH",
    )

    # Both summaries are present.
    assert "with_filter" in result and "without_filter" in result, (
        f"result missing with_filter/without_filter: {sorted(result.keys())}"
    )
    with_filter = result["with_filter"]
    without_filter = result["without_filter"]

    # signals_scored + metric fields present and well-formed for each run.
    _assert_run_well_formed(with_filter, "with_filter")
    _assert_run_well_formed(without_filter, "without_filter")

    # The fixture is built to emit several signals, so the without-filter run is
    # non-empty — otherwise the subset relationship would be vacuous.
    assert without_filter["signals_scored"] >= 1, (
        "fixture produced no signals; cannot exercise the comparison"
    )

    # Subset relationship: the filter only ever DROPS misaligned signals (R11.2).
    assert with_filter["signals_scored"] <= without_filter["signals_scored"], (
        f"with_filter signals {with_filter['signals_scored']} exceeds "
        f"without_filter {without_filter['signals_scored']}"
    )

    # Both runs are computed over the identical history => identical candle count.
    assert result["candles"] == expected_count, (
        f"reported candles {result['candles']!r} != input {expected_count!r}"
    )
