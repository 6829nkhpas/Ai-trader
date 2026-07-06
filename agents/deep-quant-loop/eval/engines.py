"""Pure Python mirrors of the deterministic deep-quant engines.

Feature: deep-quant-analysis-hardening (task 17.1)

The Evaluation_Harness measures the *deterministic computational layer* of the
deep-quant core (design AD-4). The authoritative implementations are Rust:

  * SR_Engine          — ``frontend/src-tauri/src/quant/mod.rs`` (``compute_sr``)
  * Signal_Engine      — ``agents/technical/src/signal_engine.rs``
                         (``compute_conviction``)
  * Predictive_Engine  — ``agents/predictive/src/math.rs`` (``predict_next``)

These functions are mirrored here so the offline harness can replay them without
spawning the Rust services. Each mirror is a *pure* function: identical inputs
always yield identical outputs (no clock, RNG, or ambient state), which is what
lets the harness guarantee deterministic metrics (R15.5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Signal_Engine — weighted multi-indicator conviction model (R8.1–R8.5)
# Mirrors agents/technical/src/signal_engine.rs::compute_conviction.
# ─────────────────────────────────────────────────────────────────────────────

RSI_MIDPOINT: float = 50.0
NEUTRAL_EPSILON: float = 1e-9
AGREEMENT_FLOOR: float = 0.80


@dataclass(frozen=True)
class ConvictionInputs:
    """The optional indicator picture consumed by the conviction model. Every
    field is optional so the model can score from whatever subset is available
    (R8.4)."""

    rsi_14: Optional[float] = None
    macd_histogram: Optional[float] = None
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    sma_50: Optional[float] = None
    current_price: Optional[float] = None
    atr_14: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    obv_slope: Optional[float] = None
    cmf: Optional[float] = None
    vwap: Optional[float] = None


@dataclass(frozen=True)
class ConvictionResult:
    score: int
    missing_indicators: List[str] = field(default_factory=list)


def _round_half_away(x: float) -> int:
    """Round half away from zero, matching Rust's ``f64::round`` (Python's
    built-in ``round`` uses banker's rounding, which would diverge on .5)."""
    return int(math.floor(x + 0.5)) if x >= 0.0 else int(math.ceil(x - 0.5))


def _direction(value: float) -> float:
    if value > NEUTRAL_EPSILON:
        return 1.0
    if value < -NEUTRAL_EPSILON:
        return -1.0
    return 0.0


def _family_vote(sub_votes: Sequence[float]) -> Optional[float]:
    if not sub_votes:
        return None
    return sum(sub_votes) / len(sub_votes)


def _momentum_vote(inp: ConvictionInputs) -> Optional[float]:
    votes: List[float] = []
    if inp.rsi_14 is not None:
        votes.append(max(-1.0, min(1.0, (inp.rsi_14 - RSI_MIDPOINT) / RSI_MIDPOINT)))
    if inp.macd_histogram is not None:
        votes.append(_direction(inp.macd_histogram))
    return _family_vote(votes)


def _trend_vote(inp: ConvictionInputs) -> Optional[float]:
    votes: List[float] = []
    if inp.ema_9 is not None and inp.ema_21 is not None:
        votes.append(_direction(inp.ema_9 - inp.ema_21))
    if inp.current_price is not None and inp.sma_50 is not None:
        votes.append(_direction(inp.current_price - inp.sma_50))
    return _family_vote(votes)


def _volatility_vote(inp: ConvictionInputs) -> Optional[float]:
    if inp.current_price is not None and inp.bb_upper is not None and inp.bb_lower is not None:
        mid = (inp.bb_upper + inp.bb_lower) / 2.0
        half_width = (inp.bb_upper - inp.bb_lower) / 2.0
        if abs(half_width) <= NEUTRAL_EPSILON:
            return 0.0
        return max(-1.0, min(1.0, (inp.current_price - mid) / half_width))
    return None


def _volume_vote(inp: ConvictionInputs) -> Optional[float]:
    votes: List[float] = []
    if inp.obv_slope is not None:
        votes.append(_direction(inp.obv_slope))
    if inp.cmf is not None:
        votes.append(_direction(inp.cmf))
    if inp.current_price is not None and inp.vwap is not None:
        votes.append(_direction(inp.current_price - inp.vwap))
    return _family_vote(votes)


def _collect_missing(inp: ConvictionInputs) -> List[str]:
    checks = [
        ("rsi_14", inp.rsi_14),
        ("macd_histogram", inp.macd_histogram),
        ("ema_9", inp.ema_9),
        ("ema_21", inp.ema_21),
        ("sma_50", inp.sma_50),
        ("current_price", inp.current_price),
        ("atr_14", inp.atr_14),
        ("bb_upper", inp.bb_upper),
        ("bb_lower", inp.bb_lower),
        ("obv_slope", inp.obv_slope),
        ("cmf", inp.cmf),
        ("vwap", inp.vwap),
    ]
    return [name for name, value in checks if value is None]


def compute_conviction(inp: ConvictionInputs) -> ConvictionResult:
    """Weighted confluence conviction score in ``0..=100`` (R8.1–R8.5).

    Mirrors ``signal_engine.rs::compute_conviction``: four indicator families
    (momentum, trend, volatility, volume) each cast a signed vote in ``[-1, 1]``
    from their available inputs; present families share equal renormalized
    weight; an agreement amplification pushes a fully-aligned result past any
    conflicting mix; the aggregate is mapped onto ``[0, 100]``.
    """
    missing = _collect_missing(inp)

    present = [
        v
        for v in (
            _momentum_vote(inp),
            _trend_vote(inp),
            _volatility_vote(inp),
            _volume_vote(inp),
        )
        if v is not None
    ]

    if not present:
        return ConvictionResult(score=50, missing_indicators=missing)

    aggregate = sum(present) / len(present)

    first_sign = _direction(present[0])
    aligned = first_sign != 0.0 and all(_direction(v) == first_sign for v in present)

    if aligned:
        magnitude = max(math.sqrt(abs(aggregate)), AGREEMENT_FLOOR)
        amplified = first_sign * magnitude
    else:
        amplified = aggregate

    bounded = max(-1.0, min(1.0, amplified))
    score = _round_half_away(((bounded + 1.0) / 2.0) * 100.0)
    score = max(0, min(100, score))
    return ConvictionResult(score=score, missing_indicators=missing)


# ─────────────────────────────────────────────────────────────────────────────
# Predictive_Engine — OLS linear-regression forecast (R12.1, R12.2)
# Mirrors agents/predictive/src/math.rs::PredictionEngine::predict_next.
# ─────────────────────────────────────────────────────────────────────────────

PREDICTION_WINDOW: int = 14


def predict_next(closes: Sequence[float]) -> Optional[Tuple[float, float]]:
    """Predict the next close from the last :data:`PREDICTION_WINDOW` closes via
    ordinary least squares, returning ``(predicted_close, confidence)`` where
    confidence is R² mapped to ``[1, 100]``. Returns ``None`` when fewer than
    :data:`PREDICTION_WINDOW` closes are supplied.

    Mirrors the Rust ``PredictionEngine``: it regresses ``y`` (closes) on the
    integer time indices ``x = 0..13`` and projects ``x = 14``.
    """
    window = list(closes)[-PREDICTION_WINDOW:]
    if len(window) < PREDICTION_WINDOW:
        return None

    n = float(PREDICTION_WINDOW)
    sum_x = sum_y = sum_xy = sum_x2 = 0.0
    for i, y in enumerate(window):
        x = float(i)
        sum_x += x
        sum_y += y
        sum_xy += x * y
        sum_x2 += x * x

    denominator = n * sum_x2 - sum_x * sum_x
    if abs(denominator) < 1e-12:
        return None

    m = (n * sum_xy - sum_x * sum_y) / denominator
    b = (sum_y - m * sum_x) / n
    predicted_close = m * float(PREDICTION_WINDOW) + b

    y_mean = sum_y / n
    ss_res = 0.0
    ss_tot = 0.0
    for i, y in enumerate(window):
        y_hat = m * float(i) + b
        ss_res += (y - y_hat) ** 2
        ss_tot += (y - y_mean) ** 2

    r_squared = 1.0 if abs(ss_tot) < 1e-12 else 1.0 - (ss_res / ss_tot)
    confidence = max(1.0, min(100.0, r_squared * 100.0))
    return (predicted_close, confidence)


# ─────────────────────────────────────────────────────────────────────────────
# SR_Engine — classic floor-trader pivots (R9.1–R9.4)
# Mirrors frontend/src-tauri/src/quant/mod.rs::compute_sr.
# ─────────────────────────────────────────────────────────────────────────────

OPENING_RANGE_CANDLES: int = 15


@dataclass(frozen=True)
class SrLevels:
    pivot: float
    s1: float
    s2: float
    s3: float
    r1: float
    r2: float
    r3: float
    recent_high: float
    recent_low: float
    opening_range_high: Optional[float] = None
    opening_range_low: Optional[float] = None
    daily_pivot: Optional[float] = None
    ordering_exception: Optional[str] = None


def is_intraday(timeframe: str) -> bool:
    """True for any supported timeframe other than ``"1d"`` (case/space tolerant)."""
    return timeframe.strip().lower() != "1d"


def _detect_ordering_exception(levels: Sequence[float]) -> Optional[str]:
    if any(not math.isfinite(v) for v in levels):
        return "non-finite levels: candle data forced an undefined ordering"
    for a, b in zip(levels, levels[1:]):
        if a > b:
            return (
                "level ordering violation: s3 <= s2 <= s1 <= pivot <= r1 <= r2 <= r3 "
                "could not be satisfied"
            )
    return None


def compute_sr(candles: Sequence, timeframe: str) -> SrLevels:
    """Authoritative support/resistance levels from a candle window (R9).

    Mirrors ``mod.rs::compute_sr``: classic floor-trader pivots derived from the
    most recent completed period, aggregate window extremes, intraday opening
    range + daily macro pivot, and a flagged ``ordering_exception`` whenever the
    canonical ``s3 <= ... <= r3`` ordering cannot hold (R9.2). Pure: identical
    inputs always yield identical levels (R9.4).
    """
    candles = list(candles)
    intraday = is_intraday(timeframe)

    if not candles:
        return SrLevels(
            pivot=0.0, s1=0.0, s2=0.0, s3=0.0, r1=0.0, r2=0.0, r3=0.0,
            recent_high=0.0, recent_low=0.0,
            ordering_exception="insufficient candle data: no candles supplied",
        )

    recent_high = max(c.high for c in candles)
    recent_low = min(c.low for c in candles)

    last = candles[-1]
    ph, pl, pc = last.high, last.low, last.close
    pivot = (ph + pl + pc) / 3.0
    r1 = 2.0 * pivot - pl
    s1 = 2.0 * pivot - ph
    r2 = pivot + (ph - pl)
    s2 = pivot - (ph - pl)
    r3 = ph + 2.0 * (pivot - pl)
    s3 = pl - 2.0 * (ph - pivot)

    opening_range_high = opening_range_low = daily_pivot = None
    if intraday:
        n = min(OPENING_RANGE_CANDLES, len(candles))
        opening_range_high = max(c.high for c in candles[:n])
        opening_range_low = min(c.low for c in candles[:n])
        daily_pivot = (recent_high + recent_low + pc) / 3.0

    ordering_exception = _detect_ordering_exception([s3, s2, s1, pivot, r1, r2, r3])

    return SrLevels(
        pivot=pivot, s1=s1, s2=s2, s3=s3, r1=r1, r2=r2, r3=r3,
        recent_high=recent_high, recent_low=recent_low,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
        daily_pivot=daily_pivot,
        ordering_exception=ordering_exception,
    )
