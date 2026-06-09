"""Candle-derived indicator helpers for the Evaluation_Harness.

Feature: deep-quant-analysis-hardening (task 17.1)

These are small, pure functions that derive the indicator inputs the
Signal_Engine conviction model consumes from a window of historical candles.
They mirror the spirit of the Rust ``IndicatorState`` computations closely
enough for a deterministic offline replay; every function returns ``None`` when
there is insufficient data so the conviction model can tolerate and report the
missing indicator (R8.4).

All functions are pure: identical inputs always yield identical outputs (no
clock, no RNG, no ambient state) — a prerequisite for the harness determinism
guarantee (R15.5).
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence


def _closes(candles: Sequence) -> List[float]:
    return [c.close for c in candles]


def ema(closes: Sequence[float], period: int) -> Optional[float]:
    """Exponential moving average seeded with the SMA of the first ``period``
    closes (mirrors ``IndicatorState::compute_ema``). Returns ``None`` when there
    are fewer than ``period`` closes.
    """
    closes = list(closes)
    if period <= 0 or len(closes) < period:
        return None
    multiplier = 2.0 / (period + 1.0)
    seed = sum(closes[:period]) / period
    value = seed
    for price in closes[period:]:
        value = (price - value) * multiplier + value
    return value


def sma(closes: Sequence[float], period: int) -> Optional[float]:
    """Simple moving average of the last ``period`` closes, or ``None`` when
    there are fewer than ``period`` closes.
    """
    closes = list(closes)
    if period <= 0 or len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """Wilder-style RSI over the last ``period`` deltas using simple averages of
    gains and losses (mirrors ``IndicatorState::compute_rsi``). Returns ``None``
    when there are fewer than ``period + 1`` closes.
    """
    closes = list(closes)
    if len(closes) < period + 1:
        return None
    window = closes[-(period + 1):]
    gains = 0.0
    losses = 0.0
    for prev, cur in zip(window, window[1:]):
        delta = cur - prev
        if delta >= 0.0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0.0:
        # No losses → maximally overbought (RSI 100) unless also no gains.
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(candles: Sequence, period: int = 14) -> Optional[float]:
    """Average True Range over ``period`` candles (SMA of the true range).
    Returns ``None`` when there are fewer than ``period + 1`` candles.
    """
    candles = list(candles)
    if len(candles) < period + 1:
        return None
    trs: List[float] = []
    for prev, cur in zip(candles, candles[1:]):
        tr = max(
            cur.high - cur.low,
            abs(cur.high - prev.close),
            abs(cur.low - prev.close),
        )
        trs.append(tr)
    window = trs[-period:]
    return sum(window) / len(window)


def bollinger(closes: Sequence[float], period: int = 20, num_std: float = 2.0):
    """Bollinger bands ``(upper, mid, lower)`` from the SMA and population
    standard deviation of the last ``period`` closes. Returns ``(None, None,
    None)`` when there are fewer than ``period`` closes.
    """
    closes = list(closes)
    if len(closes) < period:
        return (None, None, None)
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = math.sqrt(variance)
    return (mid + num_std * std, mid, mid - num_std * std)


def vwap(candles: Sequence) -> Optional[float]:
    """Volume-weighted average price across the supplied candles using the
    typical price ``(H + L + C) / 3``. Returns ``None`` when total volume is
    zero or no candles are supplied.
    """
    candles = list(candles)
    if not candles:
        return None
    num = 0.0
    den = 0.0
    for c in candles:
        typical = (c.high + c.low + c.close) / 3.0
        num += typical * c.volume
        den += c.volume
    if den == 0.0:
        return None
    return num / den


def obv_slope(candles: Sequence) -> Optional[float]:
    """On-balance-volume slope approximated as the OBV change over the last two
    bars (sign-bearing). Returns ``None`` when there are fewer than 2 candles.
    """
    candles = list(candles)
    if len(candles) < 2:
        return None
    obv = 0.0
    prev_obv = 0.0
    for prev, cur in zip(candles, candles[1:]):
        prev_obv = obv
        if cur.close > prev.close:
            obv += cur.volume
        elif cur.close < prev.close:
            obv -= cur.volume
    return obv - prev_obv
