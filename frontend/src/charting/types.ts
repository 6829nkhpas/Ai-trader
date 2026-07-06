// Feature: professional-charting-suite
//
// Shared value types for the Professional Charting Suite.
//
// These types are the typed vocabulary shared by the pure charting engines
// (chart-type transforms, indicator math, footprint/profile aggregation,
// strategy evaluation, drawing geometry, validation) and the rendering
// adapters that consume their output. They are intentionally framework-free
// and side-effect-free so the engines remain deterministic and testable.

/**
 * Lightweight-charts compatible OHLC candle with a numeric time.
 *
 * Re-exported from the existing `utils/chartTypes` definition so the charting
 * module shares a single canonical candle shape with the rest of the app
 * rather than introducing a competing type.
 */
export type { ChartCandle, VolumeBar } from '../utils/chartTypes';

/**
 * A single point on a value series (e.g. an indicator line). `time` is the
 * candle timestamp the value is anchored to; `value` is the plotted number.
 */
export interface LinePoint {
  time: number;
  value: number;
}

/**
 * Visual styling for a plotted line. Used by indicators and drawings so the
 * renderer can draw a line without knowing how it was computed.
 */
export interface LineStyleSpec {
  color: string;
  lineWidth: number;
  lineStyle: 'solid' | 'dashed' | 'dotted';
}

/**
 * An inclusive numeric range with an optional integer constraint. Engines use
 * these specs to declare the valid domain of each configurable parameter so
 * validation can be performed generically.
 */
export interface NumericRange {
  /** Inclusive lower bound. */
  min: number;
  /** Inclusive upper bound. */
  max: number;
  /** When true, only integer values within [min, max] are accepted. */
  integer: boolean;
}

/**
 * The result of validating/transforming a value of type `T`.
 *
 * On success it carries the (possibly coerced) value. On failure it identifies
 * the offending parameter so the UI can highlight it and present a message,
 * while the caller retains its last valid state.
 */
export type ValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; errorParam: string; message: string };
