// Feature: professional-charting-suite
//
// ChartTypeEngine — pure chart-type transforms and parameter validation.
//
// This module turns the canonical candle series into the renderable data
// series for any of the 11 supported chart types. It is deterministic and
// side-effect-free: it takes typed inputs (candles + parameters) and returns a
// typed `RenderableSeries`, never touching the DOM or `lightweight-charts`.
//
// Native series (candlestick, OHLC bar, line, area, baseline) are produced by a
// thin projection of the source candles. The "computed" types (Heikin Ashi,
// Renko, Kagi, Point & Figure, Line Break) are derived by transforms. The
// brick/column types (Renko, Kagi, P&F, Line Break) are price-driven rather
// than time-driven, so they emit an index-based series (`indexBased: true`)
// whose `time` field is a synthetic ordinal index 0,1,2,... — the renderer maps
// these onto an index-based time scale.
//
// Requirements covered by this task (2.1): 1.1 (all 11 types), 1.5/1.6
// (configurable parameters + validation), 1.7 (Heikin Ashi formula).

import type {
  ChartCandle,
  LinePoint,
  NumericRange,
  ValidationResult,
} from '../types';
import { validateParams } from './validation';

/** The 11 supported professional chart types (Requirement 1.1). */
export type ChartType =
  | 'candlestick'
  | 'hollow-candle'
  | 'ohlc-bar'
  | 'line'
  | 'area'
  | 'baseline'
  | 'heikin-ashi'
  | 'renko'
  | 'kagi'
  | 'point-figure'
  | 'line-break';

/**
 * Configuration parameters for the chart types that require them
 * (Requirement 1.5). All values are constrained to 1..999,999 by
 * {@link validateChartTypeParams}.
 */
export interface ChartTypeParams {
  /** Renko brick size in price units. */
  renkoBoxSize?: number;
  /** Point & Figure box size in price units. */
  pfBoxSize?: number;
  /** Point & Figure reversal, in number of boxes. */
  pfReversal?: number;
  /** Kagi reversal amount in price units. */
  kagiReversal?: number;
  /** Line Break count (number of prior lines a reversal must break). */
  lineBreakCount?: number;
}

/**
 * The data series the renderer consumes, tagged with the native
 * `lightweight-charts` series kind it should be drawn on and whether its
 * x-axis is a synthetic ordinal index rather than wall-clock time.
 */
export interface RenderableSeries {
  kind: 'candlestick' | 'bar' | 'line' | 'area' | 'baseline';
  points: ChartCandle[] | LinePoint[];
  /** true for the price-driven brick/column types (Renko/Kagi/P&F/LineBreak). */
  indexBased: boolean;
}

/** The canonical ordered list of all supported chart types (Requirement 1.1). */
export const CHART_TYPES: readonly ChartType[] = [
  'candlestick',
  'hollow-candle',
  'ohlc-bar',
  'line',
  'area',
  'baseline',
  'heikin-ashi',
  'renko',
  'kagi',
  'point-figure',
  'line-break',
];

/**
 * Per-chart-type parameter range specs. Types absent from a chart type's spec
 * are not configurable for it. All configurable values fall within 1..999,999
 * (Requirement 1.5/1.6); counts (P&F reversal, Line Break count) are integers,
 * while price-denominated box/reversal sizes may be fractional.
 */
export const CHART_TYPE_PARAM_SPEC: Record<ChartType, Record<string, NumericRange>> = {
  candlestick: {},
  'hollow-candle': {},
  'ohlc-bar': {},
  line: {},
  area: {},
  baseline: {},
  'heikin-ashi': {},
  renko: { renkoBoxSize: { min: 1, max: 999_999, integer: false } },
  kagi: { kagiReversal: { min: 1, max: 999_999, integer: false } },
  'point-figure': {
    pfBoxSize: { min: 1, max: 999_999, integer: false },
    pfReversal: { min: 1, max: 999_999, integer: true },
  },
  'line-break': { lineBreakCount: { min: 1, max: 999_999, integer: true } },
};

/**
 * Default parameter values used when a configurable type is rendered before the
 * trader has supplied explicit values. Every default is within the valid range.
 */
export const CHART_TYPE_PARAM_DEFAULTS: Required<ChartTypeParams> = {
  renkoBoxSize: 1,
  pfBoxSize: 1,
  pfReversal: 3,
  kagiReversal: 1,
  lineBreakCount: 3,
};

/**
 * Validate the configuration parameters for a chart type (Requirement 1.6).
 *
 * Only the parameters relevant to `type` are validated; types without
 * parameters always succeed with an empty parameter set. On the first invalid
 * value (non-numeric, wrong type, or out of the 1..999,999 range) validation
 * fails and identifies the offending parameter, so the caller can retain its
 * last valid parameters and rendered chart.
 */
export function validateChartTypeParams(
  type: ChartType,
  params: ChartTypeParams,
): ValidationResult<ChartTypeParams> {
  const spec = CHART_TYPE_PARAM_SPEC[type];
  const result = validateParams(params as Record<string, unknown>, spec);
  if (!result.ok) return result;
  return { ok: true, value: { ...result.value } as ChartTypeParams };
}

/**
 * Compute Heikin Ashi candles from a source candle series (Requirement 1.7).
 *
 * Recurrence:
 *   haClose = (open + high + low + close) / 4
 *   haOpen  = (prevHaOpen + prevHaClose) / 2, seeded from the first source
 *             candle as (open + close) / 2
 *   haHigh  = max(high, haOpen, haClose)
 *   haLow   = min(low, haOpen, haClose)
 *
 * Time is preserved from the source candle (Heikin Ashi is time-driven).
 */
export function computeHeikinAshi(candles: ChartCandle[]): ChartCandle[] {
  if (candles.length === 0) return [];

  const out: ChartCandle[] = [];
  let prevHaOpen = 0;
  let prevHaClose = 0;

  for (let i = 0; i < candles.length; i++) {
    const c = candles[i];
    const haClose = (c.open + c.high + c.low + c.close) / 4;
    const haOpen =
      i === 0 ? (c.open + c.close) / 2 : (prevHaOpen + prevHaClose) / 2;
    const haHigh = Math.max(c.high, haOpen, haClose);
    const haLow = Math.min(c.low, haOpen, haClose);

    out.push({ time: c.time, open: haOpen, high: haHigh, low: haLow, close: haClose });
    prevHaOpen = haOpen;
    prevHaClose = haClose;
  }

  return out;
}

/** Project source candles onto a close-price line series. */
function toCloseLine(candles: ChartCandle[]): LinePoint[] {
  return candles.map((c) => ({ time: c.time, value: c.close }));
}

/**
 * Build Renko bricks from close prices (index-based). A new brick is emitted
 * every time price moves a full `boxSize` away from the last brick's close,
 * producing as many bricks as the move spans.
 */
function buildRenko(candles: ChartCandle[], boxSize: number): ChartCandle[] {
  if (candles.length === 0) return [];

  const bricks: ChartCandle[] = [];
  let lastClose = candles[0].close;
  let index = 0;

  for (let i = 1; i < candles.length; i++) {
    const price = candles[i].close;

    while (price - lastClose >= boxSize) {
      const open = lastClose;
      const close = lastClose + boxSize;
      bricks.push({ time: index++, open, high: close, low: open, close });
      lastClose = close;
    }
    while (lastClose - price >= boxSize) {
      const open = lastClose;
      const close = lastClose - boxSize;
      bricks.push({ time: index++, open, high: open, low: close, close });
      lastClose = close;
    }
  }

  return bricks;
}

/**
 * Build a Kagi line from close prices (index-based). The line extends in its
 * current direction while price makes new extremes and reverses only when price
 * retraces by at least `reversal`; each reversal adds a new ordinal point.
 */
function buildKagi(candles: ChartCandle[], reversal: number): LinePoint[] {
  if (candles.length === 0) return [];

  const points: LinePoint[] = [{ time: 0, value: candles[0].close }];
  let value = candles[0].close;
  let trend: 0 | 1 | -1 = 0;
  let index = 0;

  for (let i = 1; i < candles.length; i++) {
    const price = candles[i].close;

    if (trend === 0) {
      if (price - value >= reversal) {
        trend = 1;
        value = price;
        points.push({ time: ++index, value });
      } else if (value - price >= reversal) {
        trend = -1;
        value = price;
        points.push({ time: ++index, value });
      }
      continue;
    }

    if (trend === 1) {
      if (price > value) {
        value = price;
        points[points.length - 1] = { time: index, value };
      } else if (value - price >= reversal) {
        trend = -1;
        value = price;
        points.push({ time: ++index, value });
      }
    } else {
      if (price < value) {
        value = price;
        points[points.length - 1] = { time: index, value };
      } else if (price - value >= reversal) {
        trend = 1;
        value = price;
        points.push({ time: ++index, value });
      }
    }
  }

  return points;
}

/**
 * Build Point & Figure columns from close prices (index-based). Each column is
 * emitted as a brick spanning its low..high; up (X) columns are bullish
 * (close at high), down (O) columns bearish (close at low). A reversal requires
 * price to move `reversal` boxes against the current column.
 */
function buildPointFigure(
  candles: ChartCandle[],
  boxSize: number,
  reversal: number,
): ChartCandle[] {
  if (candles.length === 0) return [];

  const reversalAmount = reversal * boxSize;
  const columns: { dir: 1 | -1; low: number; high: number }[] = [];
  let dir: 0 | 1 | -1 = 0;
  let colHigh = Math.round(candles[0].close / boxSize) * boxSize;
  let colLow = colHigh;

  for (let i = 1; i < candles.length; i++) {
    const p = candles[i].close;

    if (dir === 0) {
      if (p - colHigh >= boxSize) {
        dir = 1;
        colHigh = Math.floor(p / boxSize) * boxSize;
      } else if (colLow - p >= boxSize) {
        dir = -1;
        colLow = Math.ceil(p / boxSize) * boxSize;
      }
      continue;
    }

    if (dir === 1) {
      if (p - colHigh >= boxSize) {
        colHigh = Math.floor(p / boxSize) * boxSize;
      } else if (colHigh - p >= reversalAmount) {
        columns.push({ dir: 1, low: colLow, high: colHigh });
        dir = -1;
        colHigh = colHigh - boxSize;
        colLow = Math.ceil(p / boxSize) * boxSize;
      }
    } else {
      if (colLow - p >= boxSize) {
        colLow = Math.ceil(p / boxSize) * boxSize;
      } else if (p - colLow >= reversalAmount) {
        columns.push({ dir: -1, low: colLow, high: colHigh });
        dir = 1;
        colLow = colLow + boxSize;
        colHigh = Math.floor(p / boxSize) * boxSize;
      }
    }
  }

  if (dir !== 0) columns.push({ dir, low: colLow, high: colHigh });

  return columns.map((col, idx) => {
    const high = Math.max(col.low, col.high);
    const low = Math.min(col.low, col.high);
    return {
      time: idx,
      open: col.dir === 1 ? low : high,
      high,
      low,
      close: col.dir === 1 ? high : low,
    };
  });
}

/**
 * Build a Line Break series from close prices (index-based). A new line is
 * drawn only when the close breaks the extreme of the last `count` lines, in
 * either direction; otherwise no line is added.
 */
function buildLineBreak(candles: ChartCandle[], count: number): ChartCandle[] {
  if (candles.length === 0) return [];

  const lines: { open: number; close: number }[] = [
    { open: candles[0].open, close: candles[0].close },
  ];

  for (let i = 1; i < candles.length; i++) {
    const price = candles[i].close;
    const recent = lines.slice(-count);
    let highMax = -Infinity;
    let lowMin = Infinity;
    for (const ln of recent) {
      highMax = Math.max(highMax, ln.open, ln.close);
      lowMin = Math.min(lowMin, ln.open, ln.close);
    }

    const last = lines[lines.length - 1];
    if (price > highMax) {
      lines.push({ open: last.close, close: price });
    } else if (price < lowMin) {
      lines.push({ open: last.close, close: price });
    }
  }

  return lines.map((ln, idx) => ({
    time: idx,
    open: ln.open,
    high: Math.max(ln.open, ln.close),
    low: Math.min(ln.open, ln.close),
    close: ln.close,
  }));
}

/**
 * Transform a canonical candle series into the renderable series for a chart
 * type (Requirements 1.1, 1.7). Configurable types fall back to
 * {@link CHART_TYPE_PARAM_DEFAULTS} for any parameter not supplied; callers
 * should validate parameters with {@link validateChartTypeParams} first. An
 * empty input yields an empty series of the appropriate kind so the renderer
 * can present an empty state (Requirement 1.8).
 */
export function buildSeries(
  candles: ChartCandle[],
  type: ChartType,
  params: ChartTypeParams,
): RenderableSeries {
  const p = { ...CHART_TYPE_PARAM_DEFAULTS, ...params };

  switch (type) {
    case 'candlestick':
    case 'hollow-candle':
      return { kind: 'candlestick', points: candles, indexBased: false };

    case 'ohlc-bar':
      return { kind: 'bar', points: candles, indexBased: false };

    case 'line':
      return { kind: 'line', points: toCloseLine(candles), indexBased: false };

    case 'area':
      return { kind: 'area', points: toCloseLine(candles), indexBased: false };

    case 'baseline':
      return { kind: 'baseline', points: toCloseLine(candles), indexBased: false };

    case 'heikin-ashi':
      return { kind: 'candlestick', points: computeHeikinAshi(candles), indexBased: false };

    case 'renko':
      return { kind: 'candlestick', points: buildRenko(candles, p.renkoBoxSize), indexBased: true };

    case 'kagi':
      return { kind: 'line', points: buildKagi(candles, p.kagiReversal), indexBased: true };

    case 'point-figure':
      return {
        kind: 'candlestick',
        points: buildPointFigure(candles, p.pfBoxSize, p.pfReversal),
        indexBased: true,
      };

    case 'line-break':
      return {
        kind: 'candlestick',
        points: buildLineBreak(candles, p.lineBreakCount),
        indexBased: true,
      };

    default: {
      // Exhaustiveness guard: a new ChartType must be handled above.
      const _exhaustive: never = type;
      return { kind: 'candlestick', points: candles, indexBased: false };
    }
  }
}
