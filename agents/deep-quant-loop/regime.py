"""Regime_Classifier — pure-math market-regime detection for the Deep Quant agent.

The motivating evidence is a multi-symbol/multi-timeframe backtest in which the
existing rule set carries genuine edge on the daily timeframe but decays toward
break-even on fast intraday timeframes. The hypothesis: the losses concentrate
in choppy, rangebound, or abnormally low/high-volatility "regimes" where trend
and momentum setups fail. A veteran trader's core skill is knowing when *not* to
trade. This module implements that skill as a cheap, deterministic classifier.

Scope discipline (Requirement 12): everything here is a *filter / calibration
aid*, never a trade generator. The classifier maps a candle sequence plus a
resolved configuration to a structured Regime_Label (or an honest
Unavailable_Marker); it never emits BUY/SELL/HOLD, never blocks a trade, and
never fabricates data.

Purity (Requirement 1): this module is pure Python. It performs zero network
calls, reads zero data sources other than its two provided inputs (candles +
config), and touches no file/clock. Threshold *resolution* (``resolve_regime_
config``) is the only place the process environment is read, and it does so once
up front, deterministically, with documented defaults.

This file (task 1.1) provides the threshold-resolution foundation: the
documented default constants, the frozen ``RegimeConfig`` dataclass, and
``resolve_regime_config()``. The measure and classification functions are added
in subsequent tasks.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Optional, Sequence

# ── Documented default thresholds ─────────────────────────────────────────────
# Applied whenever a threshold env var is unset / empty / unparseable / out of
# range (Requirement 11.2-11.4). These are the single source of truth for the
# defaults on BOTH the live tool path and the backtest path (Requirement 11.6).

DEFAULT_ADX_TREND_CUTOFF = 25.0          # ADX >= this => directional strength present
DEFAULT_CHOP_RANGING_CUTOFF = 61.8       # choppiness index >= this => ranging (chop)
DEFAULT_VOL_LOW_PCTL = 25.0              # ATR-percentile < this => low volatility
DEFAULT_VOL_HIGH_PCTL = 75.0            # ATR-percentile > this => high volatility
DEFAULT_MIN_CANDLES = 50                 # minimum candles to classify

# Lookback periods (also configurable; drive the "largest lookback" gate).
DEFAULT_ADX_PERIOD = 14
DEFAULT_CHOP_PERIOD = 14
DEFAULT_VOL_PERIOD = 14
DEFAULT_VOL_PCTL_WINDOW = 100            # window over which ATR percentile is ranked
DEFAULT_BB_PERIOD = 20

# ── Environment variable names ────────────────────────────────────────────────
ENV_ADX_TREND_CUTOFF = "REGIME_ADX_TREND_CUTOFF"
ENV_CHOP_RANGING_CUTOFF = "REGIME_CHOP_RANGING_CUTOFF"
ENV_VOL_LOW_PCTL = "REGIME_VOL_LOW_PCTL"
ENV_VOL_HIGH_PCTL = "REGIME_VOL_HIGH_PCTL"
ENV_MIN_CANDLES = "REGIME_MIN_CANDLES"
ENV_ADX_PERIOD = "REGIME_ADX_PERIOD"
ENV_CHOP_PERIOD = "REGIME_CHOP_PERIOD"
ENV_VOL_PERIOD = "REGIME_VOL_PERIOD"
ENV_VOL_PCTL_WINDOW = "REGIME_VOL_PCTL_WINDOW"
ENV_BB_PERIOD = "REGIME_BB_PERIOD"

# ── Valid ranges (inclusive) ──────────────────────────────────────────────────
# Percentages / cutoffs are decimals in [0.0, 100.0]; periods/counts are integers
# >= 1 with no upper bound (Requirement 11.1).
_PCT_MIN = 0.0
_PCT_MAX = 100.0
_PERIOD_MIN = 1


@dataclass(frozen=True)
class RegimeConfig:
    """The resolved, validated threshold set used to classify a regime.

    Frozen so a resolved configuration cannot be mutated by any downstream
    consumer (supports the classifier's purity guarantee). For identical
    environment-variable values the resolved configuration is identical on both
    the tool path and the backtest path (Requirement 11.6).
    """

    adx_period: int
    chop_period: int
    vol_period: int
    vol_pctl_window: int
    bb_period: int
    adx_trend_cutoff: float
    chop_ranging_cutoff: float
    vol_low_pctl: float
    vol_high_pctl: float
    min_candles: int

    @property
    def largest_lookback(self) -> int:
        """Max candles any single Regime_Measure requires (drives the gate).

        The ATR-percentile measure is the most demanding: it ranks the latest
        ATR (itself computed over ``vol_period`` candles) within a trailing
        window of ``vol_pctl_window`` ATR samples, so it needs roughly
        ``vol_period + vol_pctl_window`` candles. The remaining measures each
        need their own single lookback. The classifier additionally requires at
        least ``min_candles``; ``classify_regime`` gates on the max of the two.
        """
        return max(
            self.adx_period,
            self.chop_period,
            self.vol_period,
            self.bb_period,
            self.vol_period + self.vol_pctl_window,
        )


def _resolve_float(env_name: str, default: float, low: float, high: float) -> float:
    """Resolve one float threshold from its own env var (Requirement 11.1-11.4).

    Falls back to ``default`` when the var is unset/empty, cannot be parsed as a
    float, is non-finite (NaN/inf), or parses but falls outside ``[low, high]``.
    Never raises.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except (ValueError, TypeError):
        return default
    if not math.isfinite(value):
        return default
    if value < low or value > high:
        return default
    return value


def _resolve_int(env_name: str, default: int, low: int) -> int:
    """Resolve one integer threshold from its own env var (Requirement 11.1-11.4).

    Falls back to ``default`` when the var is unset/empty, cannot be parsed as an
    int, or parses but is below ``low`` (the minimum valid value). Never raises.
    """
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except (ValueError, TypeError):
        return default
    if value < low:
        return default
    return value


def resolve_regime_config() -> RegimeConfig:
    """Resolve every threshold from its own env var with documented defaults.

    Per-threshold rules (Requirement 11):
      * unset / empty            -> documented default
      * unparseable as its type  -> documented default (never raises)
      * parses but out of range  -> documented default (never raises)
      * vol_low_pctl >= vol_high_pctl -> BOTH revert to their defaults

    The same function is called on the tool path and the backtest path so the
    resolved values are identical for identical environment (Requirement 11.6).
    This function NEVER raises.
    """
    adx_period = _resolve_int(ENV_ADX_PERIOD, DEFAULT_ADX_PERIOD, _PERIOD_MIN)
    chop_period = _resolve_int(ENV_CHOP_PERIOD, DEFAULT_CHOP_PERIOD, _PERIOD_MIN)
    vol_period = _resolve_int(ENV_VOL_PERIOD, DEFAULT_VOL_PERIOD, _PERIOD_MIN)
    vol_pctl_window = _resolve_int(
        ENV_VOL_PCTL_WINDOW, DEFAULT_VOL_PCTL_WINDOW, _PERIOD_MIN
    )
    bb_period = _resolve_int(ENV_BB_PERIOD, DEFAULT_BB_PERIOD, _PERIOD_MIN)
    min_candles = _resolve_int(ENV_MIN_CANDLES, DEFAULT_MIN_CANDLES, _PERIOD_MIN)

    adx_trend_cutoff = _resolve_float(
        ENV_ADX_TREND_CUTOFF, DEFAULT_ADX_TREND_CUTOFF, _PCT_MIN, _PCT_MAX
    )
    chop_ranging_cutoff = _resolve_float(
        ENV_CHOP_RANGING_CUTOFF, DEFAULT_CHOP_RANGING_CUTOFF, _PCT_MIN, _PCT_MAX
    )
    vol_low_pctl = _resolve_float(
        ENV_VOL_LOW_PCTL, DEFAULT_VOL_LOW_PCTL, _PCT_MIN, _PCT_MAX
    )
    vol_high_pctl = _resolve_float(
        ENV_VOL_HIGH_PCTL, DEFAULT_VOL_HIGH_PCTL, _PCT_MIN, _PCT_MAX
    )

    # Enforce the strict low < high ordering. If it does not hold (after the
    # per-threshold resolution above), BOTH volatility-percentile cutoffs revert
    # to their documented defaults together (Requirement 11.5).
    if vol_low_pctl >= vol_high_pctl:
        vol_low_pctl = DEFAULT_VOL_LOW_PCTL
        vol_high_pctl = DEFAULT_VOL_HIGH_PCTL

    return RegimeConfig(
        adx_period=adx_period,
        chop_period=chop_period,
        vol_period=vol_period,
        vol_pctl_window=vol_pctl_window,
        bb_period=bb_period,
        adx_trend_cutoff=adx_trend_cutoff,
        chop_ranging_cutoff=chop_ranging_cutoff,
        vol_low_pctl=vol_low_pctl,
        vol_high_pctl=vol_high_pctl,
        min_candles=min_candles,
    )


# ── Candle validation helpers (Requirement 2.2) ──────────────────────────────
# A candle is "dict-like" OHLCV record from the Rust Tool_Server with keys
# ``open`` / ``high`` / ``low`` / ``close`` / ``volume`` (mirroring how
# ``journal.py`` and ``backtest.py`` read candles via ``c.get(...)``). A candle
# is excluded from EVERY Regime_Measure computation when any OHLCV field it
# carries is non-numeric or non-finite (NaN / +/-inf), so the measures operate
# only on clean data. None of these helpers mutate their inputs.


def _is_finite_number(v: Any) -> bool:
    """True for a finite real number; ``bool`` is excluded (matches the repo's
    ``_is_num`` convention in ``journal.py`` / ``backtest.py``)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _parse_ohlc(candle: Any) -> Optional[tuple]:
    """Read ``(open, high, low, close)`` from one dict-like candle.

    Returns the four values as floats when ``open``/``high``/``low``/``close``
    are each finite numbers (and any present ``volume`` is also a finite
    number); returns ``None`` for a candle carrying a non-finite/non-numeric
    OHLCV field, an absent required field, or a non-mapping value
    (Requirement 2.2). Reads the candle without modifying it.
    """
    get = getattr(candle, "get", None)
    if not callable(get):
        return None
    o = get("open")
    h = get("high")
    low = get("low")
    c = get("close")
    if not (
        _is_finite_number(o)
        and _is_finite_number(h)
        and _is_finite_number(low)
        and _is_finite_number(c)
    ):
        return None
    # ``volume`` is part of the OHLCV record: if it is present but non-finite /
    # non-numeric, the candle is excluded too (Requirement 2.2). An absent
    # volume does not, by itself, invalidate an otherwise-clean OHLC candle.
    v = get("volume")
    if v is not None and not _is_finite_number(v):
        return None
    return (float(o), float(h), float(low), float(c))


def _valid_ohlc_rows(candles: Any) -> list:
    """Project a candle sequence to a list of valid ``(o, h, l, c)`` rows.

    Candles with non-finite/non-numeric OHLCV fields are dropped
    (Requirement 2.2). The original sequence and its candle objects are left
    unmodified (the classifier's purity guarantee). Returns an empty list for a
    ``None`` or non-iterable input rather than raising.
    """
    rows: list = []
    if candles is None:
        return rows
    try:
        iterator = iter(candles)
    except TypeError:
        return rows
    for candle in iterator:
        parsed = _parse_ohlc(candle)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _valid_period(period: Any) -> bool:
    """True when ``period`` is a usable positive integer lookback."""
    return isinstance(period, int) and not isinstance(period, bool) and period >= 1


def _clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` to ``[low, high]`` (Requirement 2.5)."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def _true_ranges(rows: Sequence[tuple]) -> list:
    """True-range series for consecutive valid rows.

    ``TR_i = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)``
    for ``i >= 1``. Returns ``len(rows) - 1`` values (empty when fewer than two
    rows). Pure: reads ``rows`` without modifying it.
    """
    trs: list = []
    for i in range(1, len(rows)):
        _, high, low, close = rows[i]
        prev_close = rows[i - 1][3]
        trs.append(
            max(high - low, abs(high - prev_close), abs(low - prev_close))
        )
    return trs


# ── Regime_Measure functions (pure) ──────────────────────────────────────────
# Each function:
#   * operates only on candles whose OHLCV fields are finite numbers (R2.2),
#   * returns a finite ``float`` when the measure is computable,
#   * returns ``None`` when its denominator is zero (e.g. a zero-range / flat
#     window) or when there are too few valid candles to compute it (R2.6),
#   * clamps bounded measures into their defined range (R2.5),
#   * never mutates its inputs and never raises.


def compute_directional_strength(candles: Any, period: Any) -> Optional[float]:
    """ADX-style directional strength over ``period`` (Requirement 1.4).

    Builds Wilder-smoothed +DM / -DM / TR series, derives the directional index
    DX = 100 * |+DI - -DI| / (+DI + -DI) at each smoothed point, and returns the
    mean of the most recent ``period`` DX values (an ADX). The result is bounded
    to ``[0, 100]``. Returns ``None`` when there are too few valid candles or
    when every smoothing point has a zero denominator (e.g. a flat, zero
    true-range window — Requirement 2.6). Never raises.
    """
    if not _valid_period(period):
        return None
    rows = _valid_ohlc_rows(candles)
    if len(rows) < period + 1:
        return None

    trs = _true_ranges(rows)
    plus_dms: list = []
    minus_dms: list = []
    for i in range(1, len(rows)):
        up_move = rows[i][1] - rows[i - 1][1]      # high_i - high_{i-1}
        down_move = rows[i - 1][2] - rows[i][2]    # low_{i-1} - low_i
        plus_dms.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dms.append(down_move if (down_move > up_move and down_move > 0) else 0.0)

    if len(trs) < period:
        return None

    def _wilder(values: Sequence[float], p: int) -> list:
        # Wilder's running smoothing: seed with the sum of the first ``p``
        # values, then ``s = s - s/p + value`` for each subsequent value.
        smoothed: list = []
        running = sum(values[:p])
        smoothed.append(running)
        for v in values[p:]:
            running = running - (running / p) + v
            smoothed.append(running)
        return smoothed

    sm_tr = _wilder(trs, period)
    sm_plus = _wilder(plus_dms, period)
    sm_minus = _wilder(minus_dms, period)

    dxs: list = []
    for tr_s, plus_s, minus_s in zip(sm_tr, sm_plus, sm_minus):
        if tr_s == 0:
            continue  # zero true-range over the window -> DI undefined here
        plus_di = 100.0 * plus_s / tr_s
        minus_di = 100.0 * minus_s / tr_s
        di_sum = plus_di + minus_di
        if di_sum == 0:
            continue
        dxs.append(100.0 * abs(plus_di - minus_di) / di_sum)

    if not dxs:
        return None  # denominator was zero everywhere (Requirement 2.6)

    window = dxs[-period:]
    adx = sum(window) / len(window)
    return _clamp(adx, 0.0, 100.0)


def compute_choppiness(candles: Any, period: Any) -> Optional[float]:
    """Choppiness index over ``period``, clamped to ``[0, 100]`` (R1.5, R2.5).

    ``CI = 100 * log10(sum(TR, period) / (maxHigh - minLow)) / log10(period)``.
    Returns ``None`` when the window's high-low range is zero (a flat window —
    Requirement 2.6), when ``period < 2`` (``log10(period)`` would be zero), or
    when there are too few valid candles. Never raises.
    """
    if not _valid_period(period) or period < 2:
        return None
    rows = _valid_ohlc_rows(candles)
    if len(rows) < period + 1:
        return None

    sub = rows[-(period + 1):]
    trs = _true_ranges(sub)
    highs = [r[1] for r in sub[1:]]
    lows = [r[2] for r in sub[1:]]
    sum_tr = sum(trs)
    price_range = max(highs) - min(lows)
    if price_range <= 0 or sum_tr <= 0:
        return None  # zero range over the window (Requirement 2.6)

    ci = 100.0 * math.log10(sum_tr / price_range) / math.log10(period)
    return _clamp(ci, 0.0, 100.0)


def compute_efficiency_ratio(candles: Any, period: Any) -> Optional[float]:
    """Kaufman efficiency ratio over ``period``, clamped to ``[0, 1]`` (R1.5, R2.5).

    ``ER = |close_last - close_{last-period}| / sum(|close_i - close_{i-1}|)``
    over the most recent ``period`` closes. Returns ``None`` when the total
    traversed path is zero (no movement — Requirement 2.6) or when there are too
    few valid candles. Never raises.
    """
    if not _valid_period(period):
        return None
    rows = _valid_ohlc_rows(candles)
    if len(rows) < period + 1:
        return None

    closes = [r[3] for r in rows[-(period + 1):]]
    net_move = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path == 0:
        return None  # zero total move -> denominator is zero (Requirement 2.6)

    return _clamp(net_move / path, 0.0, 1.0)


def compute_atr_percentile(
    candles: Any, atr_period: Any, window: Any
) -> Optional[float]:
    """Percentile rank (0-100) of the latest ATR within a trailing ``window``.

    Computes a rolling ATR (mean true-range over ``atr_period``) series, then
    ranks the most recent ATR among the last ``window`` ATR values as
    ``100 * count(atr <= latest) / sample_size``. Clamped to ``[0, 100]``
    (Requirement 2.5). Returns ``None`` when there are insufficient ATR samples
    (Requirement 1.6 / 2.6), or when the ranked ATR sample is degenerate because
    every ATR value in it is zero — a zero true-range / zero price range over the
    window means the dispersion (denominator) is zero, so the measure is null per
    Requirement 2.6. A non-flat window with varied ATRs still yields a valid
    percentile. Never raises.
    """
    if not _valid_period(atr_period) or not _valid_period(window):
        return None
    rows = _valid_ohlc_rows(candles)
    trs = _true_ranges(rows)
    if len(trs) < atr_period:
        return None

    atrs: list = [
        sum(trs[i - atr_period:i]) / atr_period
        for i in range(atr_period, len(trs) + 1)
    ]
    if not atrs:
        return None

    sample = atrs[-window:]
    # Degenerate / zero-denominator case: a flat, zero-range window produces an
    # all-zero ATR series, so there is no dispersion to rank against. Per R2.6
    # this measure is null rather than a spurious 100th percentile.
    if all(v == 0 for v in sample):
        return None

    latest = sample[-1]
    count_le = sum(1 for v in sample if v <= latest)
    pctl = 100.0 * count_le / len(sample)
    return _clamp(pctl, 0.0, 100.0)


def compute_bb_width(candles: Any, period: Any) -> Optional[float]:
    """Bollinger-band width over ``period`` (Requirement 1.7).

    ``width = (upper - lower) / mid`` where ``mid = SMA(close, period)`` and the
    bands are ``mid +/- 2 * stddev(close, period)``, i.e. ``width = 4*stddev/mid``.
    Returns ``None`` when ``mid == 0`` (denominator is zero — Requirement 2.6),
    when the closes have zero dispersion (``stddev == 0``, e.g. a flat, zero-range
    window — the relative band width is degenerate/undefined, so the measure is
    null per Requirement 2.6), or when there are too few valid candles. A non-flat
    window with non-zero stddev returns the finite width as before. Not a bounded
    measure, so it is not clamped; the returned value is finite when not ``None``.
    Never raises.
    """
    if not _valid_period(period):
        return None
    rows = _valid_ohlc_rows(candles)
    if len(rows) < period:
        return None

    closes = [r[3] for r in rows[-period:]]
    # Zero-dispersion / zero-range case: when every close in the window is the
    # same value the closes have no spread, so the relative band width is
    # degenerate/undefined. Detect this exactly via ``max == min`` (robust to the
    # tiny floating-point residual that ``sqrt(variance)`` would otherwise leave),
    # and report the measure as null per Requirement 2.6.
    if max(closes) == min(closes):
        return None

    mid = sum(closes) / len(closes)
    if mid == 0:
        return None  # mid == 0 -> denominator is zero (Requirement 2.6)

    variance = sum((x - mid) ** 2 for x in closes) / len(closes)
    std = math.sqrt(variance)
    upper = mid + 2.0 * std
    lower = mid - 2.0 * std
    return (upper - lower) / mid


# ── Classification functions (pure, total) ───────────────────────────────────
# These map the numeric Regime_Measures to the categorical regime states using
# the configured thresholds. Each is a *total* function: it returns exactly one
# value of its enumeration for every possible input, including ``None`` measures
# (which arise from zero-denominator windows). None of them read the environment,
# touch a clock, or mutate their inputs, and none of them raise.

# Trend_State / Volatility_State / Favorability enumerations (single source of
# truth for the values this module emits).
TREND_STATES = ("trending", "ranging", "transitional")
VOLATILITY_STATES = ("low", "normal", "high")
FAVORABILITY_VALUES = ("favorable", "unfavorable", "neutral")

# Named Regime_Measure fields carried in a Regime_Label (matches the contract in
# ``tools.py`` and the Data Models section of the design).
REGIME_MEASURE_FIELDS = (
    "directional_strength",
    "choppiness",
    "efficiency_ratio",
    "atr_percentile",
    "bb_width",
)

# Favorability mapping over (Trend_State x Volatility_State). Every one of the
# nine combinations maps to exactly one Favorability value, so
# ``derive_favorability`` is total (Requirement 1.10). See the design's
# "Favorability derivation" table:
#
#   Trend_State \ Vol_State |  low      | normal      | high
#   trending                |  neutral  | favorable   | unfavorable
#   ranging                 |  unfav.   | unfavorable | unfavorable
#   transitional            |  neutral  | neutral     | unfavorable
_FAVORABILITY_TABLE = {
    ("trending", "low"): "neutral",
    ("trending", "normal"): "favorable",
    ("trending", "high"): "unfavorable",
    ("ranging", "low"): "unfavorable",
    ("ranging", "normal"): "unfavorable",
    ("ranging", "high"): "unfavorable",
    ("transitional", "low"): "neutral",
    ("transitional", "normal"): "neutral",
    ("transitional", "high"): "unfavorable",
}


def classify_trend_state(
    adx: Optional[float],
    chop_or_efficiency: Optional[float],
    config: RegimeConfig,
) -> str:
    """Classify the Trend_State (Requirement 1.8).

    Returns exactly one of ``'trending'`` / ``'ranging'`` / ``'transitional'``
    by comparing the directional-strength measure ``adx`` and the choppiness
    measure ``chop_or_efficiency`` against the configured cutoffs, per the
    design's Trend_State mapping table:

      * ``adx >= adx_trend_cutoff`` AND ``chop < chop_ranging_cutoff`` -> trending
      * ``adx <  adx_trend_cutoff`` AND ``chop >= chop_ranging_cutoff`` -> ranging
      * otherwise (mixed signals, or a contributing measure is ``None``)
        -> transitional

    Total: every input (including ``None`` for either measure) maps to exactly
    one Trend_State. Pure; never raises.
    """
    if adx is None or chop_or_efficiency is None:
        return "transitional"
    strong_direction = adx >= config.adx_trend_cutoff
    choppy = chop_or_efficiency >= config.chop_ranging_cutoff
    if strong_direction and not choppy:
        return "trending"
    if not strong_direction and choppy:
        return "ranging"
    return "transitional"


def classify_volatility_state(
    atr_pctl: Optional[float],
    bb_width: Optional[float],
    config: RegimeConfig,
) -> str:
    """Classify the Volatility_State (Requirement 1.9).

    Returns exactly one of ``'low'`` / ``'normal'`` / ``'high'``. The primary
    signal is the ATR-percentile ``atr_pctl`` compared against the configured
    low/high percentile cutoffs (``bb_width`` corroborates but does not, on its
    own, override the percentile classification), per the design's
    Volatility_State mapping table:

      * ``atr_pctl < vol_low_pctl``  -> low
      * ``atr_pctl > vol_high_pctl`` -> high
      * otherwise (between cutoffs, or ``atr_pctl`` is ``None``) -> normal

    Total: every input (including ``None``) maps to exactly one Volatility_State.
    Pure; never raises.
    """
    if atr_pctl is None:
        return "normal"
    if atr_pctl < config.vol_low_pctl:
        return "low"
    if atr_pctl > config.vol_high_pctl:
        return "high"
    return "normal"


def derive_favorability(
    trend_state: str,
    volatility_state: str,
    config: RegimeConfig,
) -> str:
    """Derive the Favorability from Trend_State and Volatility_State (R1.10).

    Returns exactly one of ``'favorable'`` / ``'unfavorable'`` / ``'neutral'``.
    Total function over the nine (Trend_State x Volatility_State) combinations
    via ``_FAVORABILITY_TABLE``; any unrecognized pair falls back to
    ``'neutral'`` so the function is total for *all* inputs. Pure; never raises.
    """
    return _FAVORABILITY_TABLE.get((trend_state, volatility_state), "neutral")


# ── Unavailable_Marker / Regime_Label helpers ────────────────────────────────


def _unavailable(
    reason: str,
    symbol: Optional[str],
    timeframe: Optional[str],
) -> dict:
    """Build an honest Unavailable_Marker (Requirements 2.1, 2.3, 2.7, 4.3).

    Trend_State / Volatility_State / Favorability are *omitted* (never defaulted
    or fabricated). ``symbol`` / ``timeframe`` are included only when provided by
    the caller (the classifier itself has no knowledge of them).
    """
    marker: dict = {}
    if symbol is not None:
        marker["symbol"] = symbol
    if timeframe is not None:
        marker["timeframe"] = timeframe
    marker["unavailable"] = True
    marker["reason"] = reason
    return marker


def classify_regime(
    candles: Any,
    config: RegimeConfig,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
) -> dict:
    """Top-level entry point: map candles + config to a Regime_Label or marker.

    Returns either a Regime_Label dict (``trend_state`` / ``volatility_state`` /
    ``favorability`` / ``measures`` / ``candles_used`` — plus ``symbol`` /
    ``timeframe`` when the caller supplies them) or an Unavailable_Marker dict.

    Behaviour (Requirements 1, 2, 12):
      * Computes every Regime_Measure from the *valid* candles only — candles
        carrying non-finite/non-numeric OHLCV fields are excluded (R2.2).
      * Returns an Unavailable_Marker, citing the count of valid candles received
        and the count required, when the valid-candle count is below the gate
        ``max(min_candles, largest_lookback)`` (R1.3, R2.1, R2.3).
      * Returns an Unavailable_Marker citing "no regime measure could be
        computed" when every named measure is ``None`` (R2.7).
      * Each reported measure is a finite number or ``null`` (R2.4, R2.6);
        bounded measures are clamped by their measure function (R2.5).
      * Pure (no input mutation — R1.11, R12.2/12.4), deterministic (R1.2, R2.8),
        and non-raising (R2). Emits ONLY a label or marker — never a BUY/SELL/
        HOLD action, conviction, or decision field (R12.1, R12.3).
    """
    try:
        valid_rows = _valid_ohlc_rows(candles)
        received = len(valid_rows)

        # Sufficiency gate: the classifier requires at least ``min_candles`` and
        # at least the largest single-measure lookback (R1.3, R2.1, R2.3).
        required = max(config.min_candles, config.largest_lookback)
        if received < required:
            return _unavailable(
                f"insufficient data: {received} valid candles received, "
                f"{required} required",
                symbol,
                timeframe,
            )

        # Compute each named Regime_Measure from the *valid* candles. The measure
        # functions exclude non-finite candles themselves and return ``None`` on
        # a zero denominator, so passing the original ``candles`` is equivalent
        # to passing only the valid rows.
        directional_strength = compute_directional_strength(candles, config.adx_period)
        choppiness = compute_choppiness(candles, config.chop_period)
        efficiency_ratio = compute_efficiency_ratio(candles, config.chop_period)
        atr_percentile = compute_atr_percentile(
            candles, config.vol_period, config.vol_pctl_window
        )
        bb_width = compute_bb_width(candles, config.bb_period)

        measures = {
            "directional_strength": directional_strength,
            "choppiness": choppiness,
            "efficiency_ratio": efficiency_ratio,
            "atr_percentile": atr_percentile,
            "bb_width": bb_width,
        }

        # If no measure could be computed at all (e.g. a perfectly flat window),
        # the regime is genuinely unavailable rather than a default label (R2.7).
        if all(value is None for value in measures.values()):
            return _unavailable(
                "no regime measure could be computed",
                symbol,
                timeframe,
            )

        # Trend uses directional-strength + choppiness; volatility uses the
        # ATR-percentile (corroborated by BB-width). Favorability is the total
        # derivation over the two states.
        trend_state = classify_trend_state(directional_strength, choppiness, config)
        volatility_state = classify_volatility_state(atr_percentile, bb_width, config)
        favorability = derive_favorability(trend_state, volatility_state, config)

        label: dict = {
            "trend_state": trend_state,
            "volatility_state": volatility_state,
            "favorability": favorability,
            "measures": measures,
        }
        if symbol is not None:
            label["symbol"] = symbol
        if timeframe is not None:
            label["timeframe"] = timeframe
        label["candles_used"] = received
        return label
    except Exception as exc:  # pragma: no cover - defensive; classifier is pure
        # The classifier must never raise into its callers (R2). Any unexpected
        # failure degrades to an honest Unavailable_Marker rather than an
        # exception or a fabricated label.
        return _unavailable(
            f"regime classification error: {exc.__class__.__name__}",
            symbol,
            timeframe,
        )
