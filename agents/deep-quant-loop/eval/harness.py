"""Evaluation_Harness — EvalReport replay over the deterministic layer.

Feature: deep-quant-analysis-hardening (task 17.1)

Feeds a historical candle series through the deterministic analysis layer —
SR_Engine, Signal_Engine (conviction), Predictive_Engine (OLS), and the
Trade_Validator — with **no live LLM** (design Component 13, AD-4) and produces
an :class:`EvalReport` summarizing prediction accuracy and trade quality:

  * ``directional_accuracy``      — predicted vs realized next-bar direction (R15.1)
  * ``rr_met_proportion``         — share of generated trades with RR >= 1:2 (R15.2)
  * ``validator_pass_proportion`` — share passing all Trade_Validator checks (R15.3)
  * ``sample_count``              — number of evaluated windows

The report producer :func:`produce_eval_report` is a **pure** function of the
dataset and configuration — no clock, RNG, or ambient state — so identical input
always yields identical metrics. The determinism double-run guard that exploits
this purity is task 17.2 (kept separate).

Replay model (one sample per evaluable bar ``i``):

  1. **Predictive_Engine** regresses the trailing ``PREDICTION_WINDOW`` closes to
     project the next close; its sign vs the current close is the *predicted
     direction*. The *realized direction* is the sign of ``close[i+1] -
     close[i]``. A match contributes to ``directional_accuracy``.
  2. **Signal_Engine** scores conviction from candle-derived indicators over the
     trailing window; the score chooses a trade side (BUY when >= 50, else SELL).
  3. **SR_Engine** computes pivots over the trailing window; a candidate trade is
     placed at the pivot with SR-derived stop/target
     (BUY: entry=pivot, SL=s1, TP=r2; SELL: entry=pivot, SL=r1, TP=s2).
  4. **Trade_Validator** validates the candidate (using ATR for the stop check);
     its pass/fail and the candidate's RR feed the trade-quality proportions.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence

from . import indicators as ind
from .engines import (
    PREDICTION_WINDOW,
    ConvictionInputs,
    SrLevels,
    compute_conviction,
    compute_sr,
    predict_next,
)

# Import the Trade_Validator mirror that lives one level up (validator.py). It is
# reused verbatim so the validator pass-rate is computed by the exact production
# rules (R6.1–R6.5 / task 5.2).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from validator import (  # noqa: E402
    Action,
    ExecutionLevels,
    validate_trade,
)


# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Candle:
    """A single OHLCV candle, the unit of historical input the harness replays."""

    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp_ms: int = 0


@dataclass(frozen=True)
class EvalReport:
    """The summary report emitted by a completed evaluation run (R15.4).

    ``directional_accuracy``, ``rr_met_proportion`` and
    ``validator_pass_proportion`` are proportions in ``[0.0, 1.0]``;
    ``sample_count`` is the number of evaluated windows.
    """

    directional_accuracy: float
    rr_met_proportion: float
    validator_pass_proportion: float
    sample_count: int

    def to_dict(self) -> dict:
        return {
            "directional_accuracy": self.directional_accuracy,
            "rr_met_proportion": self.rr_met_proportion,
            "validator_pass_proportion": self.validator_pass_proportion,
            "sample_count": self.sample_count,
        }


# Minimum trailing candles required to evaluate a sample. The Predictive_Engine
# needs a full regression window; one extra leading bar lets the indicators see
# at least two points. The realized-direction comparison needs one bar ahead.
MIN_WARMUP: int = PREDICTION_WINDOW

# Risk_Reward_Ratio threshold a generated trade must meet or exceed (R15.2).
RR_TARGET: float = 2.0


# ─────────────────────────────────────────────────────────────────────────────
# Per-sample helpers (pure)
# ─────────────────────────────────────────────────────────────────────────────


def _direction_sign(delta: float, eps: float = 1e-9) -> int:
    """Sign of a price delta with a flat band: +1 up, -1 down, 0 flat."""
    if delta > eps:
        return 1
    if delta < -eps:
        return -1
    return 0


def _build_inputs(window: Sequence[Candle]) -> ConvictionInputs:
    """Derive the conviction model's indicator inputs from a candle window.
    Indicators that cannot be computed from the available data are left as
    ``None`` so the model reports them as missing and renormalizes (R8.4)."""
    closes = [c.close for c in window]
    bb_upper, _bb_mid, bb_lower = ind.bollinger(closes, period=20, num_std=2.0)
    return ConvictionInputs(
        rsi_14=ind.rsi(closes, 14),
        ema_9=ind.ema(closes, 9),
        ema_21=ind.ema(closes, 21),
        sma_50=ind.sma(closes, 50),
        current_price=closes[-1] if closes else None,
        atr_14=ind.atr(window, 14),
        bb_upper=bb_upper,
        bb_lower=bb_lower,
        obv_slope=ind.obv_slope(window),
        vwap=ind.vwap(window),
    )


def _candidate_levels(action: Action, sr: SrLevels) -> ExecutionLevels:
    """Place a candidate trade at the pivot with SR-derived stop/target.

    BUY:  entry=pivot, stop=s1 (support below), target=r2 (resistance above).
    SELL: entry=pivot, stop=r1 (resistance above), target=s2 (support below).
    """
    if action == Action.BUY:
        return ExecutionLevels(entry=sr.pivot, stop_loss=sr.s1, take_profit=sr.r2)
    return ExecutionLevels(entry=sr.pivot, stop_loss=sr.r1, take_profit=sr.s2)


def _risk_reward(levels: ExecutionLevels) -> Optional[float]:
    """Reward/risk for a candidate, or ``None`` when risk is zero/non-finite."""
    risk = abs(levels.entry - levels.stop_loss)
    reward = abs(levels.take_profit - levels.entry)
    if not math.isfinite(risk) or not math.isfinite(reward) or risk <= 0.0:
        return None
    return reward / risk


# ─────────────────────────────────────────────────────────────────────────────
# The report producer (pure)
# ─────────────────────────────────────────────────────────────────────────────


def produce_eval_report(
    candles: Sequence[Candle],
    timeframe: str = "1d",
    warmup: int = MIN_WARMUP,
) -> EvalReport:
    """Replay a historical candle series through the deterministic layer and
    return the summary :class:`EvalReport` (R15.1–R15.4).

    Pure function: identical ``candles``, ``timeframe`` and ``warmup`` always
    produce an identical report. With too few candles to form even one sample,
    a well-formed zeroed report (``sample_count == 0``) is returned.

    Args:
        candles:   Historical OHLCV candles in ascending chronological order.
        timeframe: The timeframe label passed to the SR_Engine (affects intraday
                   extras only; pivots are timeframe-independent).
        warmup:    Trailing candles required before the first evaluable sample.
    """
    candles = list(candles)
    effective_warmup = max(warmup, PREDICTION_WINDOW)

    matches = 0
    rr_met = 0
    validator_passes = 0
    sample_count = 0

    # Evaluate every bar i that has a full warmup window behind it and a realized
    # next bar (i + 1) ahead of it.
    for i in range(effective_warmup - 1, len(candles) - 1):
        window = candles[: i + 1]
        current_close = window[-1].close
        next_close = candles[i + 1].close

        prediction = predict_next([c.close for c in window])
        if prediction is None:
            # Not enough data for a directional prediction — skip this bar.
            continue
        predicted_close, _confidence = prediction

        sample_count += 1

        # 1) Directional accuracy: predicted vs realized next-bar direction.
        predicted_dir = _direction_sign(predicted_close - current_close)
        realized_dir = _direction_sign(next_close - current_close)
        if predicted_dir == realized_dir:
            matches += 1

        # 2) Conviction chooses the trade side.
        conviction = compute_conviction(_build_inputs(window))
        action = Action.BUY if conviction.score >= 50 else Action.SELL

        # 3) SR-derived candidate trade levels.
        sr = compute_sr(window, timeframe)
        levels = _candidate_levels(action, sr)

        # 4) Trade quality: RR met and Trade_Validator outcome.
        rr = _risk_reward(levels)
        if rr is not None and rr >= RR_TARGET:
            rr_met += 1

        outcome = validate_trade(action, levels, _build_inputs(window).atr_14)
        if outcome.is_pass():
            validator_passes += 1

    if sample_count == 0:
        return EvalReport(
            directional_accuracy=0.0,
            rr_met_proportion=0.0,
            validator_pass_proportion=0.0,
            sample_count=0,
        )

    return EvalReport(
        directional_accuracy=matches / sample_count,
        rr_met_proportion=rr_met / sample_count,
        validator_pass_proportion=validator_passes / sample_count,
        sample_count=sample_count,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Determinism double-run guard (R15.5)
# ─────────────────────────────────────────────────────────────────────────────


class NonDeterminismError(RuntimeError):
    """Raised when an evaluation dataset yields differing metrics across two
    identical replays (R15.5).

    ``produce_eval_report`` is a pure function of its inputs, so two runs over
    the same dataset and configuration must produce byte-identical reports. If
    they do not, some non-deterministic dependency (clock, RNG, ambient state,
    iteration order) has crept into the deterministic analysis layer, and the
    measured metrics can no longer be trusted. The guard aborts the run rather
    than report numbers that cannot be reproduced.

    The two conflicting reports are attached so the failure can be diagnosed.
    """

    def __init__(self, first: EvalReport, second: EvalReport) -> None:
        self.first = first
        self.second = second
        diffs = [
            f"{field}: {getattr(first, field)!r} != {getattr(second, field)!r}"
            for field in (
                "directional_accuracy",
                "rr_met_proportion",
                "validator_pass_proportion",
                "sample_count",
            )
            if getattr(first, field) != getattr(second, field)
        ]
        super().__init__(
            "Evaluation metrics were non-deterministic across two identical "
            "replays of the same dataset and configuration: " + "; ".join(diffs)
        )


def produce_eval_report_checked(
    candles: Sequence[Candle],
    timeframe: str = "1d",
    warmup: int = MIN_WARMUP,
) -> EvalReport:
    """Produce an :class:`EvalReport` with a determinism double-run guard (R15.5).

    Replays the dataset through :func:`produce_eval_report` **twice** with the
    same inputs and compares the two reports. If they match, the report is
    returned. If they differ, a :class:`NonDeterminismError` is raised to abort
    the evaluation run rather than report unreproducible metrics.

    This is the entry point evaluation runs should use; the bare
    :func:`produce_eval_report` is the underlying pure producer.

    Args:
        candles:   Historical OHLCV candles in ascending chronological order.
        timeframe: The timeframe label passed to the SR_Engine.
        warmup:    Trailing candles required before the first evaluable sample.

    Raises:
        NonDeterminismError: if the two replays produce differing metrics.
    """
    first = produce_eval_report(candles, timeframe=timeframe, warmup=warmup)
    second = produce_eval_report(candles, timeframe=timeframe, warmup=warmup)
    if first != second:
        raise NonDeterminismError(first, second)
    return first


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point — replay a JSON candle dataset and print the report
# ─────────────────────────────────────────────────────────────────────────────


def _load_candles_from_json(path: str) -> List[Candle]:
    import json

    with open(path, "r", encoding="utf-8-sig") as fh:
        raw = json.load(fh)
    rows = raw["candles"] if isinstance(raw, dict) else raw
    return [
        Candle(
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            volume=float(r.get("volume", 0.0)),
            timestamp_ms=int(r.get("timestamp_ms", 0)),
        )
        for r in rows
    ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Offline EvalReport replay over the deterministic deep-quant layer."
    )
    parser.add_argument("dataset", help="Path to a JSON file of historical candles.")
    parser.add_argument("--timeframe", default="1d", help="Timeframe label (default: 1d).")
    parser.add_argument(
        "--no-determinism-check",
        action="store_true",
        help="Skip the determinism double-run guard (run the dataset only once).",
    )
    args = parser.parse_args(argv)

    candles = _load_candles_from_json(args.dataset)
    if args.no_determinism_check:
        report = produce_eval_report(candles, timeframe=args.timeframe)
    else:
        try:
            report = produce_eval_report_checked(candles, timeframe=args.timeframe)
        except NonDeterminismError as exc:
            print(f"NON-DETERMINISM FAILURE: {exc}", file=sys.stderr)
            return 1
    print(json.dumps(report.to_dict(), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
