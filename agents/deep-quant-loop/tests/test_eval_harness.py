"""Example-based unit tests for the Evaluation_Harness (eval/, task 17.1).

Feature: deep-quant-analysis-hardening

These tests cover the deterministic replay producer and its engine mirrors with
concrete, hand-checkable examples (R15.1–R15.4). Property-based coverage of the
metrics (Properties 49–52) is tasks 17.3–17.6 and is intentionally not included
here.
"""

import os
import sys

# Make the service package importable (eval/ and validator.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from eval.engines import (  # noqa: E402
    ConvictionInputs,
    compute_conviction,
    compute_sr,
    predict_next,
)
from eval.harness import (  # noqa: E402
    Candle,
    EvalReport,
    NonDeterminismError,
    produce_eval_report,
    produce_eval_report_checked,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _candle(close, *, high=None, low=None, open_=None, volume=1000.0, ts=0):
    """Build a candle; H/L/O default to a tight band around the close."""
    high = close + 1.0 if high is None else high
    low = close - 1.0 if low is None else low
    open_ = close if open_ is None else open_
    return Candle(open=open_, high=high, low=low, close=close, volume=volume, timestamp_ms=ts)


def _series(closes):
    return [_candle(c, ts=i) for i, c in enumerate(closes)]


# ── Engine mirror sanity (predictive / conviction / SR) ──────────────────────

def test_predict_next_perfect_uptrend():
    # 14 points on a perfect line y = 100 + i → next (x=14) is 114, R² = 100.
    closes = [100.0 + i for i in range(14)]
    predicted, confidence = predict_next(closes)
    assert abs(predicted - 114.0) < 1e-9
    assert abs(confidence - 100.0) < 1e-9


def test_predict_next_requires_full_window():
    assert predict_next([100.0 + i for i in range(13)]) is None


def test_conviction_within_bounds_and_neutral_default():
    # No inputs → neutral 50 with everything reported missing.
    result = compute_conviction(ConvictionInputs())
    assert result.score == 50
    assert len(result.missing_indicators) == 12


def test_conviction_all_bullish_is_extreme_high():
    inp = ConvictionInputs(
        rsi_14=65.0, macd_histogram=1.2,
        ema_9=105.0, ema_21=100.0, sma_50=95.0, current_price=110.0,
        bb_upper=112.0, bb_lower=90.0,
        obv_slope=500.0, cmf=0.3, vwap=102.0,
    )
    assert compute_conviction(inp).score >= 90


def test_compute_sr_classic_pivot_formula():
    # Single prior period H=110, L=90, C=105 → pivot = 101.6667.
    sr = compute_sr([_candle(105.0, high=110.0, low=90.0)], "1d")
    assert abs(sr.pivot - (110.0 + 90.0 + 105.0) / 3.0) < 1e-9
    # Daily timeframe omits intraday extras.
    assert sr.opening_range_high is None
    assert sr.daily_pivot is None


def test_compute_sr_intraday_adds_extras():
    sr = compute_sr([_candle(105.0, high=110.0, low=90.0)], "15m")
    assert sr.opening_range_high is not None
    assert sr.opening_range_low is not None
    assert sr.daily_pivot is not None


# ── EvalReport replay (R15.1–R15.4) ──────────────────────────────────────────

def test_report_is_well_formed_for_trending_series():
    # A steadily rising series: every realized next-bar move is up, and the OLS
    # projection is up too → perfect directional accuracy.
    report = produce_eval_report(_series([100.0 + i for i in range(40)]), timeframe="1d")
    assert isinstance(report, EvalReport)
    assert report.sample_count > 0
    assert abs(report.directional_accuracy - 1.0) < 1e-9
    for proportion in (
        report.directional_accuracy,
        report.rr_met_proportion,
        report.validator_pass_proportion,
    ):
        assert 0.0 <= proportion <= 1.0


def test_report_empty_when_insufficient_data():
    # Fewer candles than the prediction window → no samples, zeroed report.
    report = produce_eval_report(_series([100.0, 101.0, 102.0]), timeframe="1d")
    assert report.sample_count == 0
    assert report.directional_accuracy == 0.0
    assert report.rr_met_proportion == 0.0
    assert report.validator_pass_proportion == 0.0


def test_report_sample_count_matches_evaluable_bars():
    # With N candles and a full window W, evaluable bars run from index W-1 to
    # N-2 inclusive → N - W samples.
    closes = [100.0 + (i % 5) for i in range(30)]
    report = produce_eval_report(_series(closes), timeframe="1d")
    assert report.sample_count == 30 - 14  # PREDICTION_WINDOW = 14


def test_report_is_deterministic():
    # The producer is pure: identical input → identical report (basis for R15.5).
    closes = [100.0 + ((i * 7) % 11) - 5 for i in range(50)]
    a = produce_eval_report(_series(closes), timeframe="1h")
    b = produce_eval_report(_series(closes), timeframe="1h")
    assert a == b


def test_report_to_dict_round_trips_fields():
    report = produce_eval_report(_series([100.0 + i for i in range(20)]))
    d = report.to_dict()
    assert set(d) == {
        "directional_accuracy",
        "rr_met_proportion",
        "validator_pass_proportion",
        "sample_count",
    }
    assert d["sample_count"] == report.sample_count


# ── Determinism double-run guard (R15.5, task 17.2) ──────────────────────────

def test_checked_report_matches_single_run_for_deterministic_layer():
    # The deterministic layer is pure, so the guarded producer returns a report
    # equal to a single bare run over the same dataset.
    closes = [100.0 + ((i * 7) % 11) - 5 for i in range(50)]
    guarded = produce_eval_report_checked(_series(closes), timeframe="1h")
    single = produce_eval_report(_series(closes), timeframe="1h")
    assert guarded == single


def test_checked_report_is_well_formed_for_trending_series():
    report = produce_eval_report_checked(_series([100.0 + i for i in range(40)]), timeframe="1d")
    assert isinstance(report, EvalReport)
    assert report.sample_count > 0
    assert abs(report.directional_accuracy - 1.0) < 1e-9


def test_checked_report_handles_empty_dataset():
    # Too few candles → a zeroed report, and the two replays still agree.
    report = produce_eval_report_checked(_series([100.0, 101.0]), timeframe="1d")
    assert report.sample_count == 0


def test_non_determinism_is_detected_when_runs_differ(monkeypatch):
    # Force the producer to yield two different reports on consecutive calls to
    # simulate a non-deterministic dependency leaking into the analysis layer.
    import eval.harness as harness

    reports = iter([
        EvalReport(0.6, 0.5, 0.4, 10),
        EvalReport(0.7, 0.5, 0.4, 10),  # directional_accuracy differs
    ])
    monkeypatch.setattr(
        harness, "produce_eval_report", lambda *a, **k: next(reports)
    )

    try:
        harness.produce_eval_report_checked(_series([100.0 + i for i in range(20)]))
        assert False, "expected NonDeterminismError"
    except NonDeterminismError as exc:
        # The conflicting reports are attached for diagnosis, and the message
        # names the differing field.
        assert exc.first.directional_accuracy == 0.6
        assert exc.second.directional_accuracy == 0.7
        assert "directional_accuracy" in str(exc)


def test_consistent_runs_do_not_raise(monkeypatch):
    # Two identical reports → no error, the report is returned unchanged.
    import eval.harness as harness

    fixed = EvalReport(0.55, 0.5, 0.45, 12)
    monkeypatch.setattr(harness, "produce_eval_report", lambda *a, **k: fixed)

    result = harness.produce_eval_report_checked(_series([100.0 + i for i in range(20)]))
    assert result == fixed
