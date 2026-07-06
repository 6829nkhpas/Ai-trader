"""Property-based tests for the Evaluation_Harness metrics (eval/, tasks 17.3–17.6).

Feature: deep-quant-analysis-hardening

These Hypothesis properties exercise the deterministic EvalReport producer
(:func:`eval.harness.produce_eval_report` / :func:`produce_eval_report_checked`)
over arbitrary historical candle datasets. They complement the example-based
tests in ``test_eval_harness.py`` by asserting universal invariants across the
whole input space:

  * Property 49 (17.3) — the directional-accuracy metric is always well-formed.
  * Property 50 (17.4) — the trade-quality proportions equal the true
                         count/total recomputed independently over the replay.
  * Property 51 (17.5) — a completed evaluation emits a full summary report.
  * Property 52 (17.6) — evaluation metrics are deterministic across identical
                         runs (and the checked producer never raises).
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (eval/ and validator.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from eval.engines import (  # noqa: E402
    PREDICTION_WINDOW,
    compute_conviction,
    compute_sr,
    predict_next,
)
from eval.harness import (  # noqa: E402
    Candle,
    EvalReport,
    RR_TARGET,
    _build_inputs,
    _candidate_levels,
    _direction_sign,
    _risk_reward,
    produce_eval_report,
    produce_eval_report_checked,
)
from validator import Action, validate_trade  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Strategies: arbitrary but well-formed OHLC candle datasets
# ─────────────────────────────────────────────────────────────────────────────

# Supported timeframes the SR_Engine accepts ("1d" = daily, others = intraday).
TIMEFRAMES = st.sampled_from(["1d", "1h", "4h", "15m", "5m"])


@st.composite
def ohlc_dicts(draw):
    """Generate a single valid OHLC(V) dict with ``low <= open/close <= high``.

    Built low-first with a non-negative span so the candle invariant
    ``high >= low`` (and high/low bracket open/close) holds by construction.
    """
    low = draw(
        st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False)
    )
    span = draw(
        st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False)
    )
    high = low + span
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    volume = draw(
        st.floats(min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
    )
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def _to_candles(rows):
    """Convert generated OHLC dicts into the harness ``Candle`` input."""
    return [
        Candle(
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
            timestamp_ms=i,
        )
        for i, r in enumerate(rows)
    ]


# Datasets sized to straddle the warmup boundary: many will produce zero samples
# (exercising the sentinel branch) and many will produce several samples.
candle_datasets = st.lists(ohlc_dicts(), min_size=0, max_size=40)


def _independent_proportions(candles, timeframe):
    """Recompute (rr_met, validator_passes, sample_count) over the replay using
    the same engine layer, but counted independently of the producer's tallies.

    Mirrors the evaluable-bar loop in ``produce_eval_report`` so the property can
    compare the report's proportions against a freshly recomputed count/total.
    """
    effective_warmup = max(PREDICTION_WINDOW, PREDICTION_WINDOW)
    rr_met = 0
    validator_passes = 0
    sample_count = 0
    for i in range(effective_warmup - 1, len(candles) - 1):
        window = candles[: i + 1]
        if predict_next([c.close for c in window]) is None:
            continue
        sample_count += 1
        conviction = compute_conviction(_build_inputs(window))
        action = Action.BUY if conviction.score >= 50 else Action.SELL
        sr = compute_sr(window, timeframe)
        levels = _candidate_levels(action, sr)
        rr = _risk_reward(levels)
        if rr is not None and rr >= RR_TARGET:
            rr_met += 1
        outcome = validate_trade(action, levels, _build_inputs(window).atr_14)
        if outcome.is_pass():
            validator_passes += 1
    return rr_met, validator_passes, sample_count


# ─────────────────────────────────────────────────────────────────────────────
# Property 49 (17.3): Directional-accuracy metric is well-formed
# ─────────────────────────────────────────────────────────────────────────────

@settings(max_examples=100)
@given(rows=candle_datasets, timeframe=TIMEFRAMES)
def test_property_49_directional_accuracy_well_formed(rows, timeframe):
    """Feature: deep-quant-analysis-hardening, Property 49: Directional-accuracy
    metric is well-formed — ``directional_accuracy`` is a float in ``[0, 1]``
    (or the defined sentinel ``0.0`` when ``sample_count == 0``).

    Validates: Requirements 15.1
    """
    candles = _to_candles(rows)
    report = produce_eval_report(candles, timeframe=timeframe)

    da = report.directional_accuracy
    assert isinstance(da, float)
    assert math.isfinite(da)

    if report.sample_count == 0:
        # Defined sentinel for an evaluation that produced no samples.
        assert da == 0.0
    else:
        assert 0.0 <= da <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Property 50 (17.4): Trade-quality proportions equal the true proportions
# ─────────────────────────────────────────────────────────────────────────────

@settings(max_examples=100)
@given(rows=candle_datasets, timeframe=TIMEFRAMES)
def test_property_50_trade_quality_proportions_equal_true_proportions(rows, timeframe):
    """Feature: deep-quant-analysis-hardening, Property 50: Trade-quality
    proportions equal the true proportions — ``rr_met_proportion`` and
    ``validator_pass_proportion`` equal the independently recomputed
    count/total over the replay, and each lies in ``[0, 1]``.

    Validates: Requirements 15.2, 15.3
    """
    candles = _to_candles(rows)
    report = produce_eval_report(candles, timeframe=timeframe)

    rr_met, validator_passes, sample_count = _independent_proportions(candles, timeframe)

    # The independently counted sample total must match the report's.
    assert sample_count == report.sample_count

    if sample_count == 0:
        assert report.rr_met_proportion == 0.0
        assert report.validator_pass_proportion == 0.0
    else:
        expected_rr = rr_met / sample_count
        expected_validator = validator_passes / sample_count
        assert report.rr_met_proportion == expected_rr
        assert report.validator_pass_proportion == expected_validator

    # Both proportions are well-formed shares.
    for proportion in (report.rr_met_proportion, report.validator_pass_proportion):
        assert isinstance(proportion, float)
        assert 0.0 <= proportion <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Property 51 (17.5): A completed evaluation emits a full summary report
# ─────────────────────────────────────────────────────────────────────────────

@settings(max_examples=100)
@given(rows=candle_datasets, timeframe=TIMEFRAMES)
def test_property_51_completed_evaluation_emits_full_summary(rows, timeframe):
    """Feature: deep-quant-analysis-hardening, Property 51: A completed
    evaluation emits a full summary report — the EvalReport carries
    ``directional_accuracy``, ``rr_met_proportion``, ``validator_pass_proportion``
    and ``sample_count``, all present and of the correct types.

    Validates: Requirements 15.4
    """
    candles = _to_candles(rows)
    report = produce_eval_report(candles, timeframe=timeframe)

    assert isinstance(report, EvalReport)

    # All four summary fields are present with the correct types.
    assert isinstance(report.directional_accuracy, float)
    assert isinstance(report.rr_met_proportion, float)
    assert isinstance(report.validator_pass_proportion, float)
    assert isinstance(report.sample_count, int)
    # bool is an int subclass — guard against a sample_count that is actually a bool.
    assert not isinstance(report.sample_count, bool)

    assert report.sample_count >= 0
    for proportion in (
        report.directional_accuracy,
        report.rr_met_proportion,
        report.validator_pass_proportion,
    ):
        assert math.isfinite(proportion)

    # The serialized summary exposes exactly the four documented fields.
    summary = report.to_dict()
    assert set(summary) == {
        "directional_accuracy",
        "rr_met_proportion",
        "validator_pass_proportion",
        "sample_count",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 52 (17.6): Evaluation metrics are deterministic across identical runs
# ─────────────────────────────────────────────────────────────────────────────

@settings(max_examples=100)
@given(rows=candle_datasets, timeframe=TIMEFRAMES)
def test_property_52_metrics_are_deterministic(rows, timeframe):
    """Feature: deep-quant-analysis-hardening, Property 52: Evaluation metrics
    are deterministic across identical runs — ``produce_eval_report`` twice on
    the same dataset yields identical metrics, and ``produce_eval_report_checked``
    does not raise.

    Validates: Requirements 15.5
    """
    candles = _to_candles(rows)

    first = produce_eval_report(candles, timeframe=timeframe)
    second = produce_eval_report(candles, timeframe=timeframe)

    # Identical inputs → byte-identical reports (every metric matches).
    assert first == second
    assert first.directional_accuracy == second.directional_accuracy
    assert first.rr_met_proportion == second.rr_met_proportion
    assert first.validator_pass_proportion == second.validator_pass_proportion
    assert first.sample_count == second.sample_count

    # The determinism double-run guard must not raise on this pure layer, and it
    # must agree with a single bare run.
    checked = produce_eval_report_checked(candles, timeframe=timeframe)
    assert checked == first
