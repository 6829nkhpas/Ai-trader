// Feature: professional-charting-suite
//
// IndicatorEngine — pure technical-indicator math for the charting suite.
//
// This module is a deterministic, side-effect-free computation layer. Each
// indicator is described by an `IndicatorDef` that carries its valid parameter
// ranges (`paramSpec`), its required candle count (`minLookback`), and a pure
// `compute(candles, params)` function that returns an `IndicatorPlot` (line
// sets, optional filled bands, optional reference levels, and a `warmupBars`
// count of leading bars with no defined value).
//
// Task 3.1 implements the OVERLAY indicators drawn on the price scale and the
// shared `INDICATOR_REGISTRY`. Task 3.2 extends the same registry with the
// oscillator indicators via `registerIndicator` and adds `listIndicators` /
// `searchIndicators`. Keeping the registry an open object lets later tasks add
// definitions without restructuring this module.
//
// Relevant requirements:
//  - 2.1 provide the overlay indicators (SMA, EMA, WMA, Bollinger, VWAP,
//        Ichimoku, SuperTrend, Parabolic SAR, Donchian, Keltner)
//  - 2.2 draw aligned to the price-series time axis
//  - 2.8 render every constituent line and fill of multi-output indicators
//  - 2.9 EMA uses the standard smoothing factor 2 / (period + 1)
//  - 2.6 insufficient data omits computation (signalled via `insufficientData`)

import type { ChartCandle, LinePoint, LineStyleSpec, NumericRange } from '../types';

// ---------------------------------------------------------------------------
// Identifiers
// ---------------------------------------------------------------------------

/** Price-overlay indicators drawn on the price scale (Requirement 2.1). */
export type OverlayId =
  | 'sma'
  | 'ema'
  | 'wma'
  | 'bollinger'
  | 'vwap'
  | 'ichimoku'
  | 'supertrend'
  | 'psar'
  | 'donchian'
  | 'keltner';

/** Separate-pane oscillator indicators (registered by task 3.2). */
export type OscillatorId =
  | 'rsi'
  | 'macd'
  | 'stochastic'
  | 'adx'
  | 'atr'
  | 'obv'
  | 'cci'
  | 'mfi'
  | 'williams-r';

export type IndicatorId = OverlayId | OscillatorId;

// ---------------------------------------------------------------------------
// Plot / definition shapes
// ---------------------------------------------------------------------------

/** A bag of numeric parameters, e.g. `{ period: 14, stdDev: 2 }`. */
export interface IndicatorParams {
  [key: string]: number;
}

/** A single plotted line within an indicator's output. */
export interface IndicatorLine {
  /** Stable id unique within the indicator (e.g. `'middle'`, `'tenkan'`). */
  id: string;
  /** The plotted points, anchored to candle timestamps and sorted ascending. */
  points: LinePoint[];
  /** Default visual style; the IndicatorManager may override per instance. */
  style: LineStyleSpec;
}

/** A filled band between an upper and a lower line (e.g. Bollinger, cloud). */
export interface IndicatorBand {
  upper: LinePoint[];
  lower: LinePoint[];
  /** CSS color (typically translucent) used to fill between the two edges. */
  fill: string;
}

/**
 * The full computed output of an indicator.
 *
 * `warmupBars` is the count of leading candles that have no defined value
 * (so the CrosshairController can show a no-value placeholder before warm-up
 * completes — Requirement 10.3). When the active series has fewer candles than
 * `minLookback`, `compute` returns no plotted output and sets
 * `insufficientData: true` (Requirements 2.6, 3.8).
 */
export interface IndicatorPlot {
  lines: IndicatorLine[];
  bands?: IndicatorBand[];
  referenceLevels?: number[];
  warmupBars: number;
  insufficientData?: boolean;
}

/** A registered indicator: metadata + validation spec + pure computation. */
export interface IndicatorDef {
  id: IndicatorId;
  name: string;
  kind: 'overlay' | 'oscillator';
  /** Default parameter values applied when a caller omits a parameter. */
  defaults: IndicatorParams;
  /** Valid range per configurable parameter (used by `validateParams`). */
  paramSpec: Record<string, NumericRange>;
  /** Minimum number of candles required to produce any output. */
  minLookback(params: IndicatorParams): number;
  /** Pure transform from candles + params to a renderable plot. */
  compute(candles: ChartCandle[], params: IndicatorParams): IndicatorPlot;
}

// ---------------------------------------------------------------------------
// Shared numeric ranges
// ---------------------------------------------------------------------------

/** Period range shared by every period-based indicator (Requirement 2.3). */
const PERIOD: NumericRange = { min: 1, max: 5_000, integer: true };
/** Bollinger standard-deviation multiplier range (Requirement 2.3). */
const STDDEV: NumericRange = { min: 0.1, max: 10.0, integer: false };
/** SuperTrend / Keltner ATR multiplier range. */
const MULTIPLIER: NumericRange = { min: 0.1, max: 10.0, integer: false };
/** Parabolic SAR acceleration-factor range. */
const AF: NumericRange = { min: 0.001, max: 1.0, integer: false };

/** Default style palette so overlays are visually distinct out of the box. */
function line(id: string, points: LinePoint[], color: string, lineWidth = 1): IndicatorLine {
  return { id, points, style: { color, lineWidth, lineStyle: 'solid' } };
}

// ---------------------------------------------------------------------------
// Pure math helpers
// ---------------------------------------------------------------------------
//
// Each helper returns an array aligned to the candle index, using `null` for
// bars that have no defined value yet (the warm-up region). `toPoints` then
// drops the nulls and anchors each value to its candle timestamp.

type Series = (number | null)[];

function closes(candles: ChartCandle[]): number[] {
  return candles.map((c) => c.close);
}

/** Index of the first non-null entry, or the array length if all null. */
function firstDefined(series: Series): number {
  for (let i = 0; i < series.length; i++) {
    if (series[i] !== null) return i;
  }
  return series.length;
}

/** Convert an index-aligned series into time-anchored line points. */
function toPoints(candles: ChartCandle[], series: Series): LinePoint[] {
  const pts: LinePoint[] = [];
  for (let i = 0; i < candles.length; i++) {
    const v = series[i];
    if (v !== null) pts.push({ time: candles[i].time, value: v });
  }
  return pts;
}

/** Simple moving average over a rolling window of `period` values. */
function smaSeries(values: number[], period: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (period <= 0) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

/**
 * Exponential moving average using the standard smoothing factor
 * `alpha = 2 / (period + 1)` (Requirement 2.9). Seeded with the simple moving
 * average of the first `period` values at index `period - 1`, after which the
 * recurrence `ema[i] = price[i] * alpha + ema[i-1] * (1 - alpha)` holds.
 */
function emaSeries(values: number[], period: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (period <= 0 || values.length < period) return out;
  const alpha = 2 / (period + 1);
  let seed = 0;
  for (let i = 0; i < period; i++) seed += values[i];
  seed /= period;
  out[period - 1] = seed;
  let prev = seed;
  for (let i = period; i < values.length; i++) {
    const v = values[i] * alpha + prev * (1 - alpha);
    out[i] = v;
    prev = v;
  }
  return out;
}

/** Linearly-weighted moving average (most recent value weighted highest). */
function wmaSeries(values: number[], period: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (period <= 0) return out;
  const denom = (period * (period + 1)) / 2;
  for (let i = period - 1; i < values.length; i++) {
    let acc = 0;
    for (let k = 0; k < period; k++) {
      acc += values[i - period + 1 + k] * (k + 1);
    }
    out[i] = acc / denom;
  }
  return out;
}

/** Population standard deviation over a rolling window of `period` values. */
function stdevSeries(values: number[], period: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (period <= 0) return out;
  for (let i = period - 1; i < values.length; i++) {
    let mean = 0;
    for (let k = 0; k < period; k++) mean += values[i - period + 1 + k];
    mean /= period;
    let varSum = 0;
    for (let k = 0; k < period; k++) {
      const d = values[i - period + 1 + k] - mean;
      varSum += d * d;
    }
    out[i] = Math.sqrt(varSum / period);
  }
  return out;
}

/** Highest high over a rolling window of `period` candles. */
function highestHigh(candles: ChartCandle[], period: number): Series {
  const out: Series = new Array(candles.length).fill(null);
  for (let i = period - 1; i < candles.length; i++) {
    let hh = -Infinity;
    for (let k = 0; k < period; k++) hh = Math.max(hh, candles[i - k].high);
    out[i] = hh;
  }
  return out;
}

/** Lowest low over a rolling window of `period` candles. */
function lowestLow(candles: ChartCandle[], period: number): Series {
  const out: Series = new Array(candles.length).fill(null);
  for (let i = period - 1; i < candles.length; i++) {
    let ll = Infinity;
    for (let k = 0; k < period; k++) ll = Math.min(ll, candles[i - k].low);
    out[i] = ll;
  }
  return out;
}

/** True range per candle: max(h-l, |h-prevClose|, |l-prevClose|). */
function trueRange(candles: ChartCandle[]): number[] {
  const tr = new Array(candles.length).fill(0);
  for (let i = 0; i < candles.length; i++) {
    const c = candles[i];
    if (i === 0) {
      tr[i] = c.high - c.low;
    } else {
      const pc = candles[i - 1].close;
      tr[i] = Math.max(c.high - c.low, Math.abs(c.high - pc), Math.abs(c.low - pc));
    }
  }
  return tr;
}

/**
 * Wilder's Average True Range. The first ATR is the simple average of the
 * true ranges over indices `1..period` (index `period`), after which Wilder's
 * smoothing `atr[i] = (atr[i-1]*(period-1) + tr[i]) / period` applies.
 */
function atrSeries(candles: ChartCandle[], period: number): Series {
  const out: Series = new Array(candles.length).fill(null);
  if (period <= 0 || candles.length <= period) return out;
  const tr = trueRange(candles);
  let seed = 0;
  for (let i = 1; i <= period; i++) seed += tr[i];
  seed /= period;
  out[period] = seed;
  let prev = seed;
  for (let i = period + 1; i < candles.length; i++) {
    const v = (prev * (period - 1) + tr[i]) / period;
    out[i] = v;
    prev = v;
  }
  return out;
}

/** The inferred bar interval (median time delta), used to project the cloud. */
function inferStep(candles: ChartCandle[]): number {
  if (candles.length < 2) return 1;
  const diffs: number[] = [];
  for (let i = 1; i < candles.length; i++) diffs.push(candles[i].time - candles[i - 1].time);
  diffs.sort((a, b) => a - b);
  const mid = diffs[Math.floor(diffs.length / 2)];
  return mid > 0 ? mid : 1;
}

/** Time for a (possibly future) candle index, projecting past the last bar. */
function timeAt(candles: ChartCandle[], idx: number, step: number): number {
  if (idx < candles.length) return candles[idx].time;
  const last = candles.length - 1;
  return candles[last].time + (idx - last) * step;
}

// ---------------------------------------------------------------------------
// Plot construction helpers
// ---------------------------------------------------------------------------

/** Merge caller params over an indicator's defaults. */
function withDefaults(defaults: IndicatorParams, params: IndicatorParams): IndicatorParams {
  return { ...defaults, ...params };
}

/** The standard empty plot returned when the series is too short. */
function insufficient(candleCount: number): IndicatorPlot {
  return { lines: [], warmupBars: candleCount, insufficientData: true };
}

/**
 * Read per-candle volume from optionally-enriched candles. `ChartCandle`
 * carries no volume field, so order-flow-aware callers supply candles with a
 * numeric `volume`. When volume is absent or non-positive everywhere, the
 * helper falls back to unit weights so volume-based oscillators (OBV, MFI)
 * still produce a meaningful directional series.
 */
function volumes(candles: ChartCandle[]): number[] {
  const vols = candles.map((c) => {
    const v = (c as unknown as { volume?: number }).volume;
    return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0;
  });
  const total = vols.reduce((a, b) => a + b, 0);
  return total > 0 ? vols : candles.map(() => 1);
}

/** Per-candle typical price (high + low + close) / 3. */
function typicalPrices(candles: ChartCandle[]): number[] {
  return candles.map((c) => (c.high + c.low + c.close) / 3);
}

/**
 * Rolling simple moving average over a nullable series. Emits a value only
 * once `period` consecutive defined inputs are available, so it composes with
 * other index-aligned series (used to smooth Stochastic %K into %D).
 */
function smoothSeries(series: Series, period: number): Series {
  const out: Series = new Array(series.length).fill(null);
  if (period <= 0) return out;
  for (let i = period - 1; i < series.length; i++) {
    let sum = 0;
    let ok = true;
    for (let k = 0; k < period; k++) {
      const v = series[i - k];
      if (v === null) {
        ok = false;
        break;
      }
      sum += v;
    }
    if (ok) out[i] = sum / period;
  }
  return out;
}

/**
 * Wilder's relative strength index over a value series. Seeds the average gain
 * and loss with the simple average of the first `period` deltas at index
 * `period`, after which Wilder's smoothing applies. A zero average loss yields
 * 100 (maximum strength).
 */
function rsiSeries(values: number[], period: number): Series {
  const out: Series = new Array(values.length).fill(null);
  if (period <= 0 || values.length <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const d = values[i] - values[i - 1];
    if (d >= 0) gain += d;
    else loss -= d;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  const rsiOf = (g: number, l: number): number => (l === 0 ? 100 : 100 - 100 / (1 + g / l));
  out[period] = rsiOf(avgGain, avgLoss);
  for (let i = period + 1; i < values.length; i++) {
    const d = values[i] - values[i - 1];
    const g = d > 0 ? d : 0;
    const l = d < 0 ? -d : 0;
    avgGain = (avgGain * (period - 1) + g) / period;
    avgLoss = (avgLoss * (period - 1) + l) / period;
    out[i] = rsiOf(avgGain, avgLoss);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Overlay indicator definitions
// ---------------------------------------------------------------------------

const SMA_DEF: IndicatorDef = {
  id: 'sma',
  name: 'Simple Moving Average',
  kind: 'overlay',
  defaults: { period: 20 },
  paramSpec: { period: PERIOD },
  minLookback: (p) => p.period,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const series = smaSeries(closes(candles), p.period);
    return {
      lines: [line('sma', toPoints(candles, series), '#3b82f6', 2)],
      warmupBars: firstDefined(series),
    };
  },
};

const EMA_DEF: IndicatorDef = {
  id: 'ema',
  name: 'Exponential Moving Average',
  kind: 'overlay',
  defaults: { period: 20 },
  paramSpec: { period: PERIOD },
  minLookback: (p) => p.period,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const series = emaSeries(closes(candles), p.period);
    return {
      lines: [line('ema', toPoints(candles, series), '#f59e0b', 2)],
      warmupBars: firstDefined(series),
    };
  },
};

const WMA_DEF: IndicatorDef = {
  id: 'wma',
  name: 'Weighted Moving Average',
  kind: 'overlay',
  defaults: { period: 20 },
  paramSpec: { period: PERIOD },
  minLookback: (p) => p.period,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const series = wmaSeries(closes(candles), p.period);
    return {
      lines: [line('wma', toPoints(candles, series), '#8b5cf6', 2)],
      warmupBars: firstDefined(series),
    };
  },
};

const BOLLINGER_DEF: IndicatorDef = {
  id: 'bollinger',
  name: 'Bollinger Bands',
  kind: 'overlay',
  defaults: { period: 20, stdDev: 2 },
  paramSpec: { period: PERIOD, stdDev: STDDEV },
  minLookback: (p) => p.period,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const src = closes(candles);
    const mid = smaSeries(src, p.period);
    const sd = stdevSeries(src, p.period);
    const upper: Series = mid.map((m, i) => (m === null || sd[i] === null ? null : m + p.stdDev * (sd[i] as number)));
    const lower: Series = mid.map((m, i) => (m === null || sd[i] === null ? null : m - p.stdDev * (sd[i] as number)));
    const upperPts = toPoints(candles, upper);
    const lowerPts = toPoints(candles, lower);
    return {
      lines: [
        line('middle', toPoints(candles, mid), '#3b82f6', 1),
        line('upper', upperPts, '#94a3b8', 1),
        line('lower', lowerPts, '#94a3b8', 1),
      ],
      bands: [{ upper: upperPts, lower: lowerPts, fill: 'rgba(59,130,246,0.08)' }],
      warmupBars: firstDefined(mid),
    };
  },
};

const VWAP_DEF: IndicatorDef = {
  id: 'vwap',
  name: 'VWAP',
  kind: 'overlay',
  defaults: {},
  paramSpec: {},
  minLookback: () => 1,
  compute(candles) {
    if (candles.length < 1) return insufficient(candles.length);
    // ChartCandle carries no volume field; read an optional `volume` when the
    // caller supplies enriched candles, otherwise fall back to equal weights
    // so VWAP degrades to a cumulative typical-price average.
    const vols = candles.map((c) => {
      const v = (c as unknown as { volume?: number }).volume;
      return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0;
    });
    const totalVol = vols.reduce((a, b) => a + b, 0);
    const useUnit = totalVol <= 0;
    let cumPV = 0;
    let cumV = 0;
    const series: Series = candles.map((c, i) => {
      const typical = (c.high + c.low + c.close) / 3;
      const w = useUnit ? 1 : vols[i];
      cumPV += typical * w;
      cumV += w;
      return cumV > 0 ? cumPV / cumV : null;
    });
    return {
      lines: [line('vwap', toPoints(candles, series), '#ec4899', 2)],
      warmupBars: firstDefined(series),
    };
  },
};

const ICHIMOKU_DEF: IndicatorDef = {
  id: 'ichimoku',
  name: 'Ichimoku Cloud',
  kind: 'overlay',
  defaults: { conversion: 9, base: 26, spanB: 52, displacement: 26 },
  paramSpec: { conversion: PERIOD, base: PERIOD, spanB: PERIOD, displacement: PERIOD },
  minLookback: (p) => Math.max(p.conversion, p.base, p.spanB),
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const step = inferStep(candles);

    const convHH = highestHigh(candles, p.conversion);
    const convLL = lowestLow(candles, p.conversion);
    const baseHH = highestHigh(candles, p.base);
    const baseLL = lowestLow(candles, p.base);
    const spanBHH = highestHigh(candles, p.spanB);
    const spanBLL = lowestLow(candles, p.spanB);

    const midOf = (hh: Series, ll: Series): Series =>
      hh.map((h, i) => (h === null || ll[i] === null ? null : (h + (ll[i] as number)) / 2));

    const tenkan = midOf(convHH, convLL);
    const kijun = midOf(baseHH, baseLL);
    const spanARaw = tenkan.map((t, i) => (t === null || kijun[i] === null ? null : (t + (kijun[i] as number)) / 2));
    const spanBRaw = midOf(spanBHH, spanBLL);

    // Senkou spans are projected forward by `displacement` bars.
    const spanAPts: LinePoint[] = [];
    const spanBPts: LinePoint[] = [];
    for (let i = 0; i < candles.length; i++) {
      const t = timeAt(candles, i + p.displacement, step);
      if (spanARaw[i] !== null) spanAPts.push({ time: t, value: spanARaw[i] as number });
      if (spanBRaw[i] !== null) spanBPts.push({ time: t, value: spanBRaw[i] as number });
    }

    // Chikou span is the close shifted backward by `displacement` bars.
    const chikouPts: LinePoint[] = [];
    for (let i = p.displacement; i < candles.length; i++) {
      chikouPts.push({ time: candles[i - p.displacement].time, value: candles[i].close });
    }

    return {
      lines: [
        line('tenkan', toPoints(candles, tenkan), '#2563eb', 1),
        line('kijun', toPoints(candles, kijun), '#dc2626', 1),
        line('spanA', spanAPts, '#16a34a', 1),
        line('spanB', spanBPts, '#b91c1c', 1),
        line('chikou', chikouPts, '#9333ea', 1),
      ],
      bands: [{ upper: spanAPts, lower: spanBPts, fill: 'rgba(22,163,74,0.12)' }],
      warmupBars: this.minLookback(p) - 1,
    };
  },
};

const SUPERTREND_DEF: IndicatorDef = {
  id: 'supertrend',
  name: 'SuperTrend',
  kind: 'overlay',
  defaults: { period: 10, multiplier: 3 },
  paramSpec: { period: PERIOD, multiplier: MULTIPLIER },
  minLookback: (p) => p.period + 1,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const atr = atrSeries(candles, p.period);
    const series: Series = new Array(candles.length).fill(null);

    let finalUpper = 0;
    let finalLower = 0;
    let prevST = 0;
    let started = false;
    let trendUp = true;

    for (let i = 0; i < candles.length; i++) {
      if (atr[i] === null) continue;
      const c = candles[i];
      const hl2 = (c.high + c.low) / 2;
      const a = atr[i] as number;
      const basicUpper = hl2 + p.multiplier * a;
      const basicLower = hl2 - p.multiplier * a;

      if (!started) {
        finalUpper = basicUpper;
        finalLower = basicLower;
        trendUp = c.close >= hl2;
        prevST = trendUp ? finalLower : finalUpper;
        series[i] = prevST;
        started = true;
        continue;
      }

      const prevClose = candles[i - 1].close;
      finalUpper =
        basicUpper < finalUpper || prevClose > finalUpper ? basicUpper : finalUpper;
      finalLower =
        basicLower > finalLower || prevClose < finalLower ? basicLower : finalLower;

      if (trendUp) {
        if (c.close < finalLower) {
          trendUp = false;
          prevST = finalUpper;
        } else {
          prevST = finalLower;
        }
      } else {
        if (c.close > finalUpper) {
          trendUp = true;
          prevST = finalLower;
        } else {
          prevST = finalUpper;
        }
      }
      series[i] = prevST;
    }

    return {
      lines: [line('supertrend', toPoints(candles, series), '#10b981', 2)],
      warmupBars: firstDefined(series),
    };
  },
};

const PSAR_DEF: IndicatorDef = {
  id: 'psar',
  name: 'Parabolic SAR',
  kind: 'overlay',
  defaults: { step: 0.02, max: 0.2 },
  paramSpec: { step: AF, max: AF },
  minLookback: () => 2,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const series: Series = new Array(candles.length).fill(null);

    let bull = candles[1].close >= candles[0].close;
    let af = p.step;
    let ep = bull ? candles[0].high : candles[0].low;
    let sar = bull ? candles[0].low : candles[0].high;

    for (let i = 1; i < candles.length; i++) {
      sar = sar + af * (ep - sar);
      const c = candles[i];
      const prevLow = candles[i - 1].low;
      const prevHigh = candles[i - 1].high;
      const prev2Low = i >= 2 ? candles[i - 2].low : prevLow;
      const prev2High = i >= 2 ? candles[i - 2].high : prevHigh;

      if (bull) {
        sar = Math.min(sar, prevLow, prev2Low);
        if (c.low < sar) {
          bull = false;
          sar = ep;
          ep = c.low;
          af = p.step;
        } else if (c.high > ep) {
          ep = c.high;
          af = Math.min(af + p.step, p.max);
        }
      } else {
        sar = Math.max(sar, prevHigh, prev2High);
        if (c.high > sar) {
          bull = true;
          sar = ep;
          ep = c.high;
          af = p.step;
        } else if (c.low < ep) {
          ep = c.low;
          af = Math.min(af + p.step, p.max);
        }
      }
      series[i] = sar;
    }

    return {
      lines: [line('psar', toPoints(candles, series), '#06b6d4', 1)],
      warmupBars: firstDefined(series),
    };
  },
};

const DONCHIAN_DEF: IndicatorDef = {
  id: 'donchian',
  name: 'Donchian Channel',
  kind: 'overlay',
  defaults: { period: 20 },
  paramSpec: { period: PERIOD },
  minLookback: (p) => p.period,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const upper = highestHigh(candles, p.period);
    const lower = lowestLow(candles, p.period);
    const mid: Series = upper.map((u, i) => (u === null || lower[i] === null ? null : (u + (lower[i] as number)) / 2));
    const upperPts = toPoints(candles, upper);
    const lowerPts = toPoints(candles, lower);
    return {
      lines: [
        line('middle', toPoints(candles, mid), '#3b82f6', 1),
        line('upper', upperPts, '#94a3b8', 1),
        line('lower', lowerPts, '#94a3b8', 1),
      ],
      bands: [{ upper: upperPts, lower: lowerPts, fill: 'rgba(148,163,184,0.10)' }],
      warmupBars: firstDefined(upper),
    };
  },
};

const KELTNER_DEF: IndicatorDef = {
  id: 'keltner',
  name: 'Keltner Channel',
  kind: 'overlay',
  defaults: { period: 20, multiplier: 2, atrPeriod: 10 },
  paramSpec: { period: PERIOD, multiplier: MULTIPLIER, atrPeriod: PERIOD },
  minLookback: (p) => Math.max(p.period, p.atrPeriod + 1),
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const mid = emaSeries(closes(candles), p.period);
    const atr = atrSeries(candles, p.atrPeriod);
    const upper: Series = mid.map((m, i) => (m === null || atr[i] === null ? null : m + p.multiplier * (atr[i] as number)));
    const lower: Series = mid.map((m, i) => (m === null || atr[i] === null ? null : m - p.multiplier * (atr[i] as number)));
    const upperPts = toPoints(candles, upper);
    const lowerPts = toPoints(candles, lower);
    return {
      lines: [
        line('middle', toPoints(candles, mid), '#f59e0b', 1),
        line('upper', upperPts, '#94a3b8', 1),
        line('lower', lowerPts, '#94a3b8', 1),
      ],
      bands: [{ upper: upperPts, lower: lowerPts, fill: 'rgba(245,158,11,0.08)' }],
      warmupBars: Math.max(firstDefined(upper), firstDefined(lower)),
    };
  },
};

// ---------------------------------------------------------------------------
// Oscillator indicator definitions (Requirement 3.1)
// ---------------------------------------------------------------------------
//
// Oscillators are drawn in a separate sub-pane on their own scale. Where an
// oscillator has canonical reference levels (RSI 30/70, Stochastic 20/80,
// Williams %R -20/-80, CCI ±100, ADX 25, MACD/OBV zero line), the plot carries
// them in `referenceLevels` so the Chart_Renderer can draw them (Requirement
// 3.5).

const MACD_FAST: NumericRange = { min: 1, max: 5_000, integer: true };

const RSI_DEF: IndicatorDef = {
  id: 'rsi',
  name: 'Relative Strength Index',
  kind: 'oscillator',
  defaults: { period: 14 },
  paramSpec: { period: PERIOD },
  minLookback: (p) => p.period + 1,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const series = rsiSeries(closes(candles), p.period);
    return {
      lines: [line('rsi', toPoints(candles, series), '#8b5cf6', 2)],
      referenceLevels: [30, 70],
      warmupBars: firstDefined(series),
    };
  },
};

const MACD_DEF: IndicatorDef = {
  id: 'macd',
  name: 'MACD',
  kind: 'oscillator',
  defaults: { fast: 12, slow: 26, signal: 9 },
  paramSpec: { fast: MACD_FAST, slow: PERIOD, signal: PERIOD },
  minLookback: (p) => p.slow + p.signal,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const src = closes(candles);
    const fast = emaSeries(src, p.fast);
    const slow = emaSeries(src, p.slow);
    // MACD line is defined wherever both EMAs are defined (i.e. from the slow
    // EMA's first value onward, since slow > fast for sane parameters).
    const macd: Series = fast.map((f, i) => (f === null || slow[i] === null ? null : f - (slow[i] as number)));
    // The signal line is an EMA of the MACD line over its defined region.
    const start = firstDefined(macd);
    const macdDefined = macd.slice(start).map((v) => v as number);
    const signalCompact = emaSeries(macdDefined, p.signal);
    const signal: Series = new Array(candles.length).fill(null);
    for (let i = 0; i < signalCompact.length; i++) signal[start + i] = signalCompact[i];
    const histogram: Series = macd.map((m, i) => (m === null || signal[i] === null ? null : m - (signal[i] as number)));
    return {
      lines: [
        line('macd', toPoints(candles, macd), '#2563eb', 2),
        line('signal', toPoints(candles, signal), '#f59e0b', 1),
        line('histogram', toPoints(candles, histogram), '#94a3b8', 1),
      ],
      referenceLevels: [0],
      warmupBars: firstDefined(signal),
    };
  },
};

const STOCHASTIC_DEF: IndicatorDef = {
  id: 'stochastic',
  name: 'Stochastic Oscillator',
  kind: 'oscillator',
  defaults: { kPeriod: 14, dPeriod: 3, smooth: 3 },
  paramSpec: { kPeriod: PERIOD, dPeriod: PERIOD, smooth: PERIOD },
  minLookback: (p) => p.kPeriod + p.smooth + p.dPeriod,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const hh = highestHigh(candles, p.kPeriod);
    const ll = lowestLow(candles, p.kPeriod);
    const rawK: Series = candles.map((c, i) => {
      if (hh[i] === null || ll[i] === null) return null;
      const denom = (hh[i] as number) - (ll[i] as number);
      return denom === 0 ? 50 : (100 * (c.close - (ll[i] as number))) / denom;
    });
    const k = smoothSeries(rawK, p.smooth);
    const d = smoothSeries(k, p.dPeriod);
    return {
      lines: [
        line('k', toPoints(candles, k), '#2563eb', 2),
        line('d', toPoints(candles, d), '#f59e0b', 1),
      ],
      referenceLevels: [20, 80],
      warmupBars: firstDefined(d),
    };
  },
};

const ADX_DEF: IndicatorDef = {
  id: 'adx',
  name: 'ADX / DMI',
  kind: 'oscillator',
  defaults: { period: 14 },
  paramSpec: { period: PERIOD },
  minLookback: (p) => 2 * p.period,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    const period = p.period;
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const tr = trueRange(candles);
    const plusDM = new Array(candles.length).fill(0);
    const minusDM = new Array(candles.length).fill(0);
    for (let i = 1; i < candles.length; i++) {
      const up = candles[i].high - candles[i - 1].high;
      const down = candles[i - 1].low - candles[i].low;
      plusDM[i] = up > down && up > 0 ? up : 0;
      minusDM[i] = down > up && down > 0 ? down : 0;
    }
    // Wilder-smoothed sums seeded at index `period` with the sum of 1..period.
    const wilder = (vals: number[]): Series => {
      const out: Series = new Array(vals.length).fill(null);
      if (vals.length <= period) return out;
      let sum = 0;
      for (let i = 1; i <= period; i++) sum += vals[i];
      out[period] = sum;
      let prev = sum;
      for (let i = period + 1; i < vals.length; i++) {
        prev = prev - prev / period + vals[i];
        out[i] = prev;
      }
      return out;
    };
    const smTR = wilder(tr);
    const smPlus = wilder(plusDM);
    const smMinus = wilder(minusDM);
    const plusDI: Series = new Array(candles.length).fill(null);
    const minusDI: Series = new Array(candles.length).fill(null);
    const dx: Series = new Array(candles.length).fill(null);
    for (let i = 0; i < candles.length; i++) {
      const t = smTR[i];
      if (t === null || t === 0 || smPlus[i] === null || smMinus[i] === null) continue;
      const pdi = (100 * (smPlus[i] as number)) / t;
      const mdi = (100 * (smMinus[i] as number)) / t;
      plusDI[i] = pdi;
      minusDI[i] = mdi;
      const sum = pdi + mdi;
      dx[i] = sum === 0 ? 0 : (100 * Math.abs(pdi - mdi)) / sum;
    }
    // ADX seeds at index 2*period-1 with the average of the first `period` DX
    // values, then applies Wilder smoothing.
    const adx: Series = new Array(candles.length).fill(null);
    const firstDx = firstDefined(dx);
    if (firstDx + period - 1 < candles.length) {
      let seed = 0;
      for (let i = firstDx; i < firstDx + period; i++) seed += dx[i] as number;
      seed /= period;
      const seedIdx = firstDx + period - 1;
      adx[seedIdx] = seed;
      let prev = seed;
      for (let i = seedIdx + 1; i < candles.length; i++) {
        if (dx[i] === null) continue;
        prev = (prev * (period - 1) + (dx[i] as number)) / period;
        adx[i] = prev;
      }
    }
    return {
      lines: [
        line('plusDI', toPoints(candles, plusDI), '#16a34a', 1),
        line('minusDI', toPoints(candles, minusDI), '#dc2626', 1),
        line('adx', toPoints(candles, adx), '#2563eb', 2),
      ],
      referenceLevels: [25],
      warmupBars: firstDefined(adx),
    };
  },
};

const ATR_DEF: IndicatorDef = {
  id: 'atr',
  name: 'Average True Range',
  kind: 'oscillator',
  defaults: { period: 14 },
  paramSpec: { period: PERIOD },
  minLookback: (p) => p.period + 1,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const series = atrSeries(candles, p.period);
    return {
      lines: [line('atr', toPoints(candles, series), '#f59e0b', 2)],
      warmupBars: firstDefined(series),
    };
  },
};

const OBV_DEF: IndicatorDef = {
  id: 'obv',
  name: 'On-Balance Volume',
  kind: 'oscillator',
  defaults: {},
  paramSpec: {},
  minLookback: () => 1,
  compute(candles) {
    if (candles.length < 1) return insufficient(candles.length);
    const vol = volumes(candles);
    const series: Series = new Array(candles.length).fill(null);
    let obv = 0;
    series[0] = 0;
    for (let i = 1; i < candles.length; i++) {
      const diff = candles[i].close - candles[i - 1].close;
      if (diff > 0) obv += vol[i];
      else if (diff < 0) obv -= vol[i];
      series[i] = obv;
    }
    return {
      lines: [line('obv', toPoints(candles, series), '#0ea5e9', 2)],
      referenceLevels: [0],
      warmupBars: firstDefined(series),
    };
  },
};

const CCI_DEF: IndicatorDef = {
  id: 'cci',
  name: 'Commodity Channel Index',
  kind: 'oscillator',
  defaults: { period: 20 },
  paramSpec: { period: PERIOD },
  minLookback: (p) => p.period,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const tp = typicalPrices(candles);
    const smaTp = smaSeries(tp, p.period);
    const series: Series = new Array(candles.length).fill(null);
    for (let i = p.period - 1; i < candles.length; i++) {
      const mean = smaTp[i] as number;
      let meanDev = 0;
      for (let k = 0; k < p.period; k++) meanDev += Math.abs(tp[i - k] - mean);
      meanDev /= p.period;
      series[i] = meanDev === 0 ? 0 : (tp[i] - mean) / (0.015 * meanDev);
    }
    return {
      lines: [line('cci', toPoints(candles, series), '#8b5cf6', 2)],
      referenceLevels: [-100, 100],
      warmupBars: firstDefined(series),
    };
  },
};

const MFI_DEF: IndicatorDef = {
  id: 'mfi',
  name: 'Money Flow Index',
  kind: 'oscillator',
  defaults: { period: 14 },
  paramSpec: { period: PERIOD },
  minLookback: (p) => p.period + 1,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const tp = typicalPrices(candles);
    const vol = volumes(candles);
    const posFlow = new Array(candles.length).fill(0);
    const negFlow = new Array(candles.length).fill(0);
    for (let i = 1; i < candles.length; i++) {
      const raw = tp[i] * vol[i];
      if (tp[i] > tp[i - 1]) posFlow[i] = raw;
      else if (tp[i] < tp[i - 1]) negFlow[i] = raw;
    }
    const series: Series = new Array(candles.length).fill(null);
    for (let i = p.period; i < candles.length; i++) {
      let pos = 0;
      let neg = 0;
      for (let k = 0; k < p.period; k++) {
        pos += posFlow[i - k];
        neg += negFlow[i - k];
      }
      series[i] = neg === 0 ? 100 : 100 - 100 / (1 + pos / neg);
    }
    return {
      lines: [line('mfi', toPoints(candles, series), '#0ea5e9', 2)],
      referenceLevels: [20, 80],
      warmupBars: firstDefined(series),
    };
  },
};

const WILLIAMS_R_DEF: IndicatorDef = {
  id: 'williams-r',
  name: 'Williams %R',
  kind: 'oscillator',
  defaults: { period: 14 },
  paramSpec: { period: PERIOD },
  minLookback: (p) => p.period,
  compute(candles, params) {
    const p = withDefaults(this.defaults, params);
    if (candles.length < this.minLookback(p)) return insufficient(candles.length);
    const hh = highestHigh(candles, p.period);
    const ll = lowestLow(candles, p.period);
    const series: Series = candles.map((c, i) => {
      if (hh[i] === null || ll[i] === null) return null;
      const denom = (hh[i] as number) - (ll[i] as number);
      return denom === 0 ? -50 : (-100 * ((hh[i] as number) - c.close)) / denom;
    });
    return {
      lines: [line('williams-r', toPoints(candles, series), '#ec4899', 2)],
      referenceLevels: [-80, -20],
      warmupBars: firstDefined(series),
    };
  },
};

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

/**
 * The shared indicator registry, keyed by indicator id. Task 3.1 populates the
 * overlay indicators; task 3.2 adds the oscillators via `registerIndicator`.
 * Typed as `Record<string, IndicatorDef>` so it can be populated incrementally
 * across tasks while still being looked up by `IndicatorId` (a string subtype).
 */
export const INDICATOR_REGISTRY: Record<string, IndicatorDef> = {};

/**
 * Register (or replace) an indicator definition in the shared registry. This is
 * the extension point task 3.2 uses to add oscillator indicators without
 * editing this module.
 */
export function registerIndicator(def: IndicatorDef): void {
  INDICATOR_REGISTRY[def.id] = def;
}

/** Look up a registered indicator definition by id, if present. */
export function getIndicator(id: IndicatorId): IndicatorDef | undefined {
  return INDICATOR_REGISTRY[id];
}

/** Every registered indicator definition (overlays + oscillators). */
export function listIndicators(): IndicatorDef[] {
  return Object.values(INDICATOR_REGISTRY);
}

/**
 * Indicators whose display name contains `query` (case-insensitive). An empty
 * or whitespace-only query returns the full list, matching the Indicator
 * Manager's "no filter" behavior (Requirement 4.2).
 */
export function searchIndicators(query: string): IndicatorDef[] {
  const q = query.trim().toLowerCase();
  if (q === '') return listIndicators();
  return listIndicators().filter((def) => def.name.toLowerCase().includes(q));
}

const OVERLAY_DEFS: IndicatorDef[] = [
  SMA_DEF,
  EMA_DEF,
  WMA_DEF,
  BOLLINGER_DEF,
  VWAP_DEF,
  ICHIMOKU_DEF,
  SUPERTREND_DEF,
  PSAR_DEF,
  DONCHIAN_DEF,
  KELTNER_DEF,
];

for (const def of OVERLAY_DEFS) registerIndicator(def);

const OSCILLATOR_DEFS: IndicatorDef[] = [
  RSI_DEF,
  MACD_DEF,
  STOCHASTIC_DEF,
  ADX_DEF,
  ATR_DEF,
  OBV_DEF,
  CCI_DEF,
  MFI_DEF,
  WILLIAMS_R_DEF,
];

for (const def of OSCILLATOR_DEFS) registerIndicator(def);
