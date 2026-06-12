// Feature: professional-charting-suite
//
// Canonical candle series selector.
//
// This module is the single normalization point that sits between the raw
// `useTradeStore.ohlcCandles` buffer and every pure charting engine. The store
// receives historical candles plus live OHLC ticks that can arrive out of
// order or repeat a timestamp (a live update replacing the in-progress
// candle). Engines (chart-type transforms, indicators, footprint, profile,
// strategies) assume a clean series; this selector enforces that contract:
//
//   - filtered to the active symbol,
//   - mapped to the shared `ChartCandle` shape (time in seconds),
//   - de-duplicated by timestamp (latest received candle wins),
//   - sorted strictly ascending by time.
//
// Keeping this invariant in one deterministic, side-effect-free function lets
// the renderer recover from out-of-order live updates by simply repainting
// from the canonical series (Requirement 9.6), and lets the in-place latest
// candle helper below update only the most recent candle (Requirement 9.3).

import type { OhlcCandle } from '../../store/useTradeStore';
import type { ChartCandle } from '../types';

/**
 * Convert a raw store timestamp (milliseconds) to the `ChartCandle` time unit
 * (whole seconds) used throughout the charting suite and `lightweight-charts`.
 */
function toChartTime(startTimestampMs: number): number {
  return Math.floor(startTimestampMs / 1000);
}

/**
 * Produce the canonical candle series for a symbol from the raw store buffer.
 *
 * Invariants of the returned array (Requirement 9.6):
 *   - every candle belongs to `symbol` (case-insensitive match);
 *   - timestamps are unique (de-duplicated); when the raw buffer contains more
 *     than one candle for the same timestamp, the last occurrence wins, which
 *     matches the store's "live update replaces the in-progress candle"
 *     semantics;
 *   - the series is sorted strictly ascending by `time`.
 *
 * The function is pure: it never mutates `raw` and returns a fresh array.
 *
 * @param raw    The raw OHLC candle buffer (e.g. `useTradeStore.ohlcCandles`).
 * @param symbol The active symbol to select candles for.
 */
export function canonicalCandles(raw: OhlcCandle[], symbol: string): ChartCandle[] {
  const wanted = symbol.toUpperCase();

  // De-duplicate by canonical time, keeping the last occurrence. A Map keyed by
  // time preserves insertion order while letting later candles overwrite
  // earlier ones for the same timestamp.
  const byTime = new Map<number, ChartCandle>();

  for (const c of raw) {
    if (!c || typeof c.symbol !== 'string') continue;
    if (c.symbol.toUpperCase() !== wanted) continue;
    if (!Number.isFinite(c.start_timestamp_ms)) continue;

    const time = toChartTime(c.start_timestamp_ms);
    byTime.set(time, {
      time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    });
  }

  return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
}

/**
 * The classification of a live candle update relative to the current canonical
 * series, returned by {@link applyLatestCandleUpdate}.
 *
 *   - `update`  — the incoming candle shares the latest candle's timestamp, so
 *                 only that last candle changes in place. The renderer can call
 *                 `series.update()` rather than `setData()` (Requirement 9.3).
 *   - `append`  — the incoming candle is newer than every existing candle and
 *                 becomes the new latest. Existing candles are untouched.
 *   - `repaint` — the incoming candle is older than the current latest (an
 *                 out-of-order update). The caller must repaint from the
 *                 returned canonical series (Requirement 9.6).
 */
export type LatestCandleUpdateKind = 'update' | 'append' | 'repaint';

export interface LatestCandleUpdateResult {
  /** What happened, so the renderer can choose `update()` vs `setData()`. */
  kind: LatestCandleUpdateKind;
  /** The resulting canonical series (always sorted, de-duplicated). */
  series: ChartCandle[];
  /** The candle that was inserted/replaced (the latest for update/append). */
  candle: ChartCandle;
}

/**
 * Apply a single live candle update to a canonical series, changing only the
 * latest candle whenever possible (Requirement 9.3).
 *
 * Behavior, given a series that is already sorted ascending and de-duplicated:
 *   - empty series                       → `append` the candle as the first;
 *   - `update.time === last.time`        → `update`: replace the last candle in
 *                                          place; all earlier candles are kept
 *                                          unchanged (same references);
 *   - `update.time > last.time`          → `append`: add as the new latest; all
 *                                          existing candles are kept unchanged;
 *   - `update.time < last.time`          → `repaint`: an out-of-order update;
 *                                          merge the candle (de-dup + re-sort)
 *                                          and signal a full repaint.
 *
 * The function is pure and never mutates the input series.
 *
 * @param series The current canonical series (assumed sorted + de-duplicated).
 * @param update The incoming candle to apply.
 */
export function applyLatestCandleUpdate(
  series: ChartCandle[],
  update: ChartCandle,
): LatestCandleUpdateResult {
  if (series.length === 0) {
    return { kind: 'append', series: [update], candle: update };
  }

  const last = series[series.length - 1];

  if (update.time === last.time) {
    // Replace only the last candle; keep every earlier candle by reference.
    const next = series.slice(0, series.length - 1);
    next.push(update);
    return { kind: 'update', series: next, candle: update };
  }

  if (update.time > last.time) {
    // Newer candle becomes the latest; existing candles are untouched.
    return { kind: 'append', series: [...series, update], candle: update };
  }

  // Out-of-order update: merge by timestamp (last wins) and re-sort so the
  // caller can repaint from a clean canonical series (Requirement 9.6).
  const byTime = new Map<number, ChartCandle>();
  for (const c of series) byTime.set(c.time, c);
  byTime.set(update.time, update);
  const repainted = Array.from(byTime.values()).sort((a, b) => a.time - b.time);
  return { kind: 'repaint', series: repainted, candle: update };
}
