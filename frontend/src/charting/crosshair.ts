// Feature: professional-charting-suite
//
// crosshair — PURE, side-effect-free helpers for the Crosshair_Controller.
//
// These functions own the two pieces of crosshair behavior that must be
// mathematically exact and are therefore the target of property-based tests
// (Properties 31 and 32):
//
//   - numeric formatting to the instrument's configured decimal precision
//     (Requirements 10.1, 10.2); and
//   - the no-value placeholder produced for warm-up bars and out-of-range
//     crosshair positions (Requirements 10.3, 10.8).
//
// The rendering adapter (`useCrosshairController`) subscribes to the chart's
// crosshair-move events and delegates every value decision to the pure
// functions here. The vertical-crosshair synchronization across panes
// (Requirement 10.4) is provided intrinsically by the shared time scale of
// lightweight-charts v5 panes, so it is not modeled here.

import type { ChartCandle, LinePoint } from './types';
import type { IndicatorPlot } from './engines';

/** The placeholder shown when a readout has no defined value. */
export const NO_VALUE = '—';

/** Default decimal precision when an instrument declares none (₹0.01 tick). */
export const DEFAULT_PRICE_PRECISION = 2;

/** Clamp an arbitrary precision request to the range `Number.toFixed` accepts. */
function clampPrecision(precision: number): number {
  if (!Number.isFinite(precision)) return DEFAULT_PRICE_PRECISION;
  const p = Math.trunc(precision);
  if (p < 0) return 0;
  if (p > 100) return 100;
  return p;
}

/**
 * Format a single numeric value to the instrument's configured decimal
 * precision (Requirements 10.1, 10.2).
 *
 * Returns the {@link NO_VALUE} placeholder for any value that is not a finite
 * number (`null`, `undefined`, `NaN`, `±Infinity`) — the building block for the
 * warm-up / out-of-range placeholder behavior (Requirements 10.3, 10.8).
 */
export function formatValue(
  value: number | null | undefined,
  precision: number,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return NO_VALUE;
  }
  return value.toFixed(clampPrecision(precision));
}

/** A formatted OHLC readout; every field is either a number string or NO_VALUE. */
export interface OhlcReadout {
  open: string;
  high: string;
  low: string;
  close: string;
}

/** The placeholder OHLC readout (all four fields show NO_VALUE). */
export const EMPTY_OHLC_READOUT: OhlcReadout = {
  open: NO_VALUE,
  high: NO_VALUE,
  low: NO_VALUE,
  close: NO_VALUE,
};

/**
 * Find the candle anchored at exactly `time`, or `undefined` when the crosshair
 * is at a time with no loaded candle (i.e. outside the loaded data range).
 *
 * Candle series are short enough per visible frame that a linear scan is fine;
 * keeping this pure avoids coupling to any index structure.
 */
export function findCandleAt(
  candles: ChartCandle[],
  time: number | null | undefined,
): ChartCandle | undefined {
  if (time === null || time === undefined || !Number.isFinite(time)) {
    return undefined;
  }
  for (let i = 0; i < candles.length; i++) {
    if (candles[i].time === time) return candles[i];
  }
  return undefined;
}

/**
 * Format the OHLC readout for the candle at `time`.
 *
 * When no candle exists at `time` (out-of-range crosshair position), every
 * field is the {@link NO_VALUE} placeholder rather than values borrowed from an
 * adjacent candle (Requirement 10.8).
 */
export function formatOhlcAt(
  candles: ChartCandle[],
  time: number | null | undefined,
  precision: number,
): OhlcReadout {
  const candle = findCandleAt(candles, time);
  if (!candle) return { ...EMPTY_OHLC_READOUT };
  return {
    open: formatValue(candle.open, precision),
    high: formatValue(candle.high, precision),
    low: formatValue(candle.low, precision),
    close: formatValue(candle.close, precision),
  };
}

/**
 * Resolve the value of a plotted indicator line at exactly `time`.
 *
 * Indicator engines omit points for bars that fall in the indicator's warm-up
 * region (no point is emitted until the value is defined), so the absence of a
 * point at `time` is exactly the warm-up / out-of-range condition that must
 * yield a placeholder (Requirements 10.3, 10.8).
 */
export function indicatorPointAt(
  points: LinePoint[],
  time: number | null | undefined,
): number | undefined {
  if (time === null || time === undefined || !Number.isFinite(time)) {
    return undefined;
  }
  for (let i = 0; i < points.length; i++) {
    if (points[i].time === time) return points[i].value;
  }
  return undefined;
}

/** A single formatted indicator line value at the crosshair time. */
export interface IndicatorLineReadout {
  /** The engine line id (e.g. 'sma', 'macd', 'bb-upper'). */
  lineId: string;
  /** The value formatted to precision, or NO_VALUE during warm-up / out-of-range. */
  value: string;
}

/** A formatted readout for one active indicator instance. */
export interface IndicatorReadout {
  instanceId: string;
  indicatorId: string;
  /** Human-facing label for the indicator (falls back to the id). */
  label: string;
  /** One entry per plotted line, in plot order. */
  lines: IndicatorLineReadout[];
  /** True when the indicator could not be computed (lookback > candle count). */
  insufficientData: boolean;
}

/** An active indicator paired with its already-computed plot for a frame. */
export interface IndicatorReadoutInput {
  instanceId: string;
  indicatorId: string;
  label: string;
  plot: IndicatorPlot;
}

/** The complete crosshair readout for one crosshair position. */
export interface CrosshairReadout {
  /** The crosshair time, or null when the crosshair is off the chart. */
  time: number | null;
  /** True when a loaded candle exists at `time`. */
  hasCandle: boolean;
  /** Formatted OHLC (placeholders when `hasCandle` is false). */
  ohlc: OhlcReadout;
  /** Formatted per-indicator readouts (placeholders during warm-up). */
  indicators: IndicatorReadout[];
}

/** The empty readout used when the crosshair leaves the chart. */
export const EMPTY_CROSSHAIR_READOUT: CrosshairReadout = {
  time: null,
  hasCandle: false,
  ohlc: { ...EMPTY_OHLC_READOUT },
  indicators: [],
};

/**
 * Build the full crosshair readout for a crosshair position. PURE: given the
 * canonical candles, the active indicators' precomputed plots, the crosshair
 * time, and the instrument precision, it returns a fully-formatted readout with
 * placeholders for every warm-up / out-of-range field.
 *
 * Requirements: 10.1, 10.2 (precision formatting), 10.3 (indicator warm-up
 * placeholder), 10.8 (out-of-range OHLC placeholder).
 */
export function buildCrosshairReadout(args: {
  time: number | null | undefined;
  candles: ChartCandle[];
  indicators: IndicatorReadoutInput[];
  precision: number;
}): CrosshairReadout {
  const { time, candles, indicators, precision } = args;
  const normalizedTime =
    time === null || time === undefined || !Number.isFinite(time)
      ? null
      : time;

  const candle = findCandleAt(candles, normalizedTime);

  const indicatorReadouts: IndicatorReadout[] = indicators.map((ind) => {
    const insufficientData = ind.plot.insufficientData === true;
    const lines: IndicatorLineReadout[] = ind.plot.lines.map((ln) => ({
      lineId: ln.id,
      value: insufficientData
        ? NO_VALUE
        : formatValue(indicatorPointAt(ln.points, normalizedTime), precision),
    }));
    return {
      instanceId: ind.instanceId,
      indicatorId: ind.indicatorId,
      label: ind.label,
      lines,
      insufficientData,
    };
  });

  return {
    time: normalizedTime,
    hasCandle: candle !== undefined,
    ohlc: candle
      ? {
          open: formatValue(candle.open, precision),
          high: formatValue(candle.high, precision),
          low: formatValue(candle.low, precision),
          close: formatValue(candle.close, precision),
        }
      : { ...EMPTY_OHLC_READOUT },
    indicators: indicatorReadouts,
  };
}
