"""Example-based smoke test for comparison-mode forecast backtest and forecast
calibration (backtest.py ``compare_forecast`` / ``calibrate_forecast``, task 16.4).

Feature: volatility-aware-forecaster

These are DETERMINISTIC, example-based smoke tests (NOT property tests) that
exercise ``backtest.compare_forecast()`` and ``backtest.calibrate_forecast()``
end-to-end, fully OFFLINE: a fixed, hand-pinned OHLCV candle fixture is passed
directly via ``candles=...`` so no Rust tool server / QuestDB is touched. To
keep the run network-free, ``backtest._resolve_benchmark_candles`` is
monkeypatched to return ``None`` — the relative-strength labelling then degrades
to an honest Unavailable_Marker for every signal (R13.4) and the run still
proceeds.

``test_compare_forecast_smoke`` asserts the documented comparison-mode contract
(R13.3):
  * the result carries both ``with_forecast`` and ``without_forecast`` summaries,
    each with ``signals_scored`` and the documented metric fields;
  * ``with_forecast.signals_scored <= without_forecast.signals_scored`` — the
    with-forecast seeded set is a SUBSET of the without-forecast set because the
    forecast filter only ever DROPS signals whose Forecast_Alignment is the
    available ``misaligned`` label for their direction (R13.2);
  * both runs report the SAME ``candles`` count (identical history);
  * each run's ``win_rate`` / ``expectancy`` are well-formed — a float (with
    win_rate in [0.0, 1.0]) or the string ``"n/a"`` when zero trades closed.

``test_calibrate_forecast_smoke`` asserts the documented reliability-report
contract (R12.2): a ``bins`` list whose length equals the configured bin count,
a finite-or-``"n/a"`` ``calibration_error``, a non-negative integer
``total_records``, and each bin entry carrying lower/upper/count/mean_predicted/
realized_up_fraction.

The sys.path / import pattern mirrors the sibling tests (the service directory
one level up is prepended to ``sys.path`` so ``backtest`` imports cleanly).
"""

import math
import os
import sys

# Make the service package importable (backtest.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import forecaster  # noqa: E402
from backtest import BacktestConfig  # noqa: E402

# Reduced-lookback config keeps the run fast while still satisfying the signal
# rules (need >= max(ema_slow=21, ols_window=20) + 1 == 22 closes per window) and
# the generate_and_score guard (n >= lookback + 2). Identical for both runs —
# compare_forecast() only flips the forecast-filter flag between them.
_CFG = BacktestConfig(lookback=30, cooldown_bars=2, profile_rows=12)

# Documented metric fields each run summary must carry.
_METRIC_FIELDS = ("closed_trades", "winning_closed_trades", "win_rate", "expectancy")

_BASE_TS = 1_700_000_000_000
_BAR_MS = 900_000  # 15m bars; strictly increasing, unique timestamps.


def _build_fixture():
    """Build a FIXED, fully deterministic OHLCV candle fixture (~150 bars).

    A closed-form price path — an upward LINEAR drift with a periodic SINE
    pullback — gives a trending series WITH regular pullbacks, so the
    deterministic rule set emits several signals (EMA crossings, value-area-edge
    pullbacks, occasional over-extensions). No randomness at all: the same bytes
    are produced on every run, so it is effectively a hand-pinned constant series
    expressed compactly. Each bar carries a UNIQUE, strictly increasing
    ``timestamp_ms``.
    """
    candles = []
    prev_close = 150.0
    for i in range(150):
        # Upward trend (0.6/bar) with a sine pullback that periodically dips the
        # path so both BUY and SELL setups arise.
        level = 150.0 + i * 0.6 + 8.0 * math.sin(i / 5.0)
        o = prev_close
        c = round(level, 4)
        hi = round(max(o, c) + 1.5 + 0.5 * math.cos(i / 3.0) + 0.5, 4)
        lo = round(max(0.5, min(o, c) - 1.5 - 0.5 * math.cos(i / 3.0) - 0.5), 4)
        candles.append({
            "timestamp_ms": _BASE_TS + i * _BAR_MS,
            "open": round(o, 4),
            "high": hi,
            "low": lo,
            "close": c,
            "volume": round(1.0e5 + (i % 7) * 1.0e4, 2),
        })
        prev_close = c
    return candles


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
        # Zero closed trades => both metrics not-applicable, never a division by
        # zero (R13.3).
        assert win_rate == "n/a", f"{label}: expected win_rate 'n/a', got {win_rate!r}"
        assert expectancy == "n/a", f"{label}: expected expectancy 'n/a', got {expectancy!r}"
    else:
        assert isinstance(win_rate, float), f"{label}: win_rate not a float: {win_rate!r}"
        assert 0.0 <= win_rate <= 1.0, f"{label}: win_rate {win_rate!r} outside [0,1]"
        assert isinstance(expectancy, float) and math.isfinite(expectancy), (
            f"{label}: expectancy not a finite float: {expectancy!r}"
        )


def test_compare_forecast_smoke(monkeypatch):
    """Validates: Requirements 13.3

    A comparison-mode forecast backtest over a fixed candle fixture produces
    with-forecast and without-forecast summaries — each with ``signals_scored``
    and the metric fields — with the expected subset relationship and well-formed
    metrics, fully offline.
    """
    candles = _build_fixture()
    expected_count = len(candles)

    # Avoid the network: with no benchmark candles, relative strength degrades to
    # an honest Unavailable_Marker for every signal (R13.4) and the run proceeds.
    monkeypatch.setattr(backtest, "_resolve_benchmark_candles", lambda *a, **k: None)

    result = backtest.compare_forecast("TEST", "15m", candles=candles, cfg=_CFG)

    # Both summaries are present.
    assert "with_forecast" in result and "without_forecast" in result, (
        f"result missing with_forecast/without_forecast: {sorted(result.keys())}"
    )
    with_forecast = result["with_forecast"]
    without_forecast = result["without_forecast"]

    # signals_scored + metric fields present and well-formed for each run.
    _assert_run_well_formed(with_forecast, "with_forecast")
    _assert_run_well_formed(without_forecast, "without_forecast")

    # The fixture is built to emit several signals, so the without-forecast run is
    # non-empty — otherwise the subset relationship would be vacuous.
    assert without_forecast["signals_scored"] >= 1, (
        "fixture produced no signals; cannot exercise the comparison"
    )

    # Subset relationship: the forecast filter only ever DROPS misaligned signals
    # (R13.2 / R13.3).
    assert with_forecast["signals_scored"] <= without_forecast["signals_scored"], (
        f"with_forecast signals {with_forecast['signals_scored']} exceeds "
        f"without_forecast {without_forecast['signals_scored']}"
    )

    # Both runs are computed over the identical history => identical candle count.
    assert result["candles"] == expected_count, (
        f"reported candles {result['candles']!r} != input {expected_count!r}"
    )


def test_calibrate_forecast_smoke():
    """Validates: Requirements 12.2

    A calibration run over the same fixed candle fixture produces a well-formed
    reliability report: a ``bins`` list of the configured length, a
    finite-or-``"n/a"`` ``calibration_error``, a non-negative integer
    ``total_records``, and each bin carrying the documented per-bin fields,
    fully offline (the forecaster never touches the network).
    """
    candles = _build_fixture()
    expected_count = len(candles)

    # The number of probability bins the report must partition [0, 1] into — the
    # SAME resolver the calibration uses for its default bin count (R12.2).
    expected_bins = forecaster.resolve_forecaster_config().prob_bins

    report = backtest.calibrate_forecast("TEST", "15m", candles=candles)

    # Context is echoed back.
    assert report["candles"] == expected_count, (
        f"reported candles {report['candles']!r} != input {expected_count!r}"
    )

    # bins is a list whose length equals the configured bin count (R12.2).
    bins = report["bins"]
    assert isinstance(bins, list), f"bins is not a list: {type(bins).__name__}"
    assert len(bins) == expected_bins, (
        f"bins length {len(bins)} != configured bin count {expected_bins}"
    )

    # calibration_error is 'n/a' or a finite float (R12.3).
    calibration_error = report["calibration_error"]
    assert calibration_error == "n/a" or (
        isinstance(calibration_error, float) and math.isfinite(calibration_error)
    ), f"calibration_error not 'n/a' or a finite float: {calibration_error!r}"

    # total_records is a non-negative integer.
    total_records = report["total_records"]
    assert isinstance(total_records, int) and total_records >= 0, (
        f"total_records not a non-negative int: {total_records!r}"
    )

    # Each bin entry carries the documented per-bin fields (R12.2).
    for k, b in enumerate(bins):
        for key in ("lower", "upper", "count", "mean_predicted", "realized_up_fraction"):
            assert key in b, f"bin {k} missing key {key!r}; got {sorted(b.keys())}"

        # Edges partition [0, 1] in increasing order.
        assert isinstance(b["lower"], (int, float)) and isinstance(b["upper"], (int, float)), (
            f"bin {k} edges not numeric: {b['lower']!r}/{b['upper']!r}"
        )
        assert b["lower"] < b["upper"], f"bin {k} edges not increasing: {b['lower']!r}/{b['upper']!r}"

        count = b["count"]
        assert isinstance(count, int) and count >= 0, f"bin {k} bad count: {count!r}"

        mean_predicted = b["mean_predicted"]
        realized_up_fraction = b["realized_up_fraction"]
        if count == 0:
            # Empty bin => both per-bin stats not-applicable, never divide by zero.
            assert mean_predicted == "n/a", f"bin {k} expected mean_predicted 'n/a', got {mean_predicted!r}"
            assert realized_up_fraction == "n/a", (
                f"bin {k} expected realized_up_fraction 'n/a', got {realized_up_fraction!r}"
            )
        else:
            assert isinstance(mean_predicted, float) and math.isfinite(mean_predicted), (
                f"bin {k} mean_predicted not a finite float: {mean_predicted!r}"
            )
            assert 0.0 <= mean_predicted <= 1.0, f"bin {k} mean_predicted {mean_predicted!r} outside [0,1]"
            assert isinstance(realized_up_fraction, float) and 0.0 <= realized_up_fraction <= 1.0, (
                f"bin {k} realized_up_fraction {realized_up_fraction!r} not a float in [0,1]"
            )
