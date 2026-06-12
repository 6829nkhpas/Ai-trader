// Feature: professional-charting-suite, Property 29
//
// Property-based test for Property 29: "Live update of the latest candle
// changes only that candle" (Validates Requirement 9.3).
//
// For any canonical series and any live update whose timestamp equals the most
// recent candle, applying the update modifies only the most recent candle and
// leaves all preceding candles unchanged (same references). We also assert the
// classification of appends (strictly newer timestamp) and repaints
// (out-of-order timestamp) behave per the engine's documented contract.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { applyLatestCandleUpdate } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

const RUNS = 100;

/** A single finite OHLC value generator. */
const price = () =>
  fc.double({ min: 0.0001, max: 100_000, noNaN: true, noDefaultInfinity: true });

/** Generate a ChartCandle at a fixed time with arbitrary finite OHLC values. */
const candleAt = (time: number): fc.Arbitrary<ChartCandle> =>
  fc.record({ open: price(), high: price(), low: price(), close: price() }).map(
    (ohlc) => ({ time, ...ohlc }),
  );

/**
 * Generate a non-empty canonical series: strictly ascending, unique
 * timestamps. We build it from a sorted set of distinct integer times so the
 * input already satisfies the canonical invariant the helper assumes.
 */
const canonicalSeries = (): fc.Arbitrary<ChartCandle[]> =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), {
      minLength: 1,
      maxLength: 40,
    })
    .chain((times) => {
      const sorted = [...times].sort((a, b) => a - b);
      return fc.tuple(...sorted.map((t) => candleAt(t)));
    })
    .map((arr) => arr as ChartCandle[]);

describe('Property 29: live update of the latest candle changes only that candle', () => {
  it('update (time === last.time) replaces only the last candle, keeping earlier candles unchanged', () => {
    fc.assert(
      fc.property(canonicalSeries(), price(), price(), price(), price(), (
        series,
        open,
        high,
        low,
        close,
      ) => {
        const lastTime = series[series.length - 1].time;
        const update: ChartCandle = { time: lastTime, open, high, low, close };

        const result = applyLatestCandleUpdate(series, update);

        // Classification is an in-place update.
        expect(result.kind).toBe('update');
        // Same length: no candle added or removed.
        expect(result.series).toHaveLength(series.length);

        // Every earlier candle is unchanged by reference (only the last changes).
        for (let i = 0; i < series.length - 1; i += 1) {
          expect(result.series[i]).toBe(series[i]);
        }

        // The last candle is exactly the update.
        expect(result.series[result.series.length - 1]).toEqual(update);
        expect(result.candle).toEqual(update);

        // The input series is not mutated.
        expect(series[series.length - 1].time).toBe(lastTime);
      }),
      { numRuns: RUNS },
    );
  });

  it('append (time > last.time) adds a new latest candle and leaves all existing candles unchanged', () => {
    fc.assert(
      fc.property(
        canonicalSeries(),
        fc.integer({ min: 1, max: 1000 }),
        price(),
        price(),
        price(),
        price(),
        (series, delta, open, high, low, close) => {
          const lastTime = series[series.length - 1].time;
          const update: ChartCandle = { time: lastTime + delta, open, high, low, close };

          const result = applyLatestCandleUpdate(series, update);

          expect(result.kind).toBe('append');
          expect(result.series).toHaveLength(series.length + 1);
          // All existing candles are unchanged by reference.
          for (let i = 0; i < series.length; i += 1) {
            expect(result.series[i]).toBe(series[i]);
          }
          // The appended candle is the new latest.
          expect(result.series[result.series.length - 1]).toEqual(update);
          expect(result.candle).toEqual(update);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('repaint (time < last.time) merges out-of-order updates into a sorted, de-duplicated series', () => {
    fc.assert(
      fc.property(
        // Need at least 2 candles so an older timestamp than the last exists.
        canonicalSeries().filter((s) => s.length >= 2),
        fc.double({ min: 0, max: 1, noNaN: true }),
        price(),
        price(),
        price(),
        price(),
        (series, pick, open, high, low, close) => {
          const lastTime = series[series.length - 1].time;
          // Choose an existing earlier timestamp strictly less than the last.
          const earlierTimes = series
            .slice(0, series.length - 1)
            .map((c) => c.time);
          const idx = Math.min(
            earlierTimes.length - 1,
            Math.floor(pick * earlierTimes.length),
          );
          const targetTime = earlierTimes[idx];
          const update: ChartCandle = { time: targetTime, open, high, low, close };

          const result = applyLatestCandleUpdate(series, update);

          expect(result.kind).toBe('repaint');
          // Strictly ascending and de-duplicated.
          for (let i = 1; i < result.series.length; i += 1) {
            expect(result.series[i].time).toBeGreaterThan(result.series[i - 1].time);
          }
          // The latest candle is preserved (update was older).
          expect(result.series[result.series.length - 1].time).toBe(lastTime);
          // The merged candle for targetTime is the update (last wins).
          const merged = result.series.find((c) => c.time === targetTime);
          expect(merged).toEqual(update);
          expect(result.candle).toEqual(update);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('empty series appends the update as the first candle', () => {
    fc.assert(
      fc.property(candleAt(42), (update) => {
        const result = applyLatestCandleUpdate([], update);
        expect(result.kind).toBe('append');
        expect(result.series).toEqual([update]);
        expect(result.candle).toEqual(update);
      }),
      { numRuns: RUNS },
    );
  });
});
