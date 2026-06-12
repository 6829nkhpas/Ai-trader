// Feature: professional-charting-suite, Property 18
//
// Property-based test for Property 18: "Footprint delta and cumulative delta
// are correct sums" (Validates Requirements 6.4, 6.5, 6.8).
//
// For any footprint candle, its delta equals total ask volume minus total bid
// volume and its total volume equals the sum of all cell volumes; for any
// footprint series, the cumulative delta at each index equals the running sum
// of per-candle deltas from the leftmost candle, and the final value equals the
// sum of all deltas.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { buildFootprint, cumulativeDelta } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';
import type { OrderFlowTick } from '@/store/useTradeStore';

const RUNS = 100;

/** Tolerance for floating-point equality of summed volumes/deltas. */
const EPS = 1e-6;

const approxEqual = (a: number, b: number): boolean =>
  Math.abs(a - b) <= EPS * Math.max(1, Math.abs(a), Math.abs(b));

/** A single finite, non-negative price value generator. */
const price = () =>
  fc.double({ min: 0.0001, max: 5_000, noNaN: true, noDefaultInfinity: true });

/** A finite, non-negative volume value generator. */
const volume = () =>
  fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true });

/**
 * Generate a well-formed candle series with strictly ascending unique
 * timestamps (seconds). high/low bracket open/close.
 */
const candleSeries = (): fc.Arbitrary<ChartCandle[]> =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), { minLength: 1, maxLength: 30 })
    .chain((times) => {
      const sorted = [...times].sort((x, y) => x - y);
      return fc.tuple(
        ...sorted.map((t) =>
          fc.record({ a: price(), b: price(), c: price(), d: price() }).map(({ a, b, c, d }) => ({
            time: t,
            open: a,
            close: b,
            high: Math.max(a, b, c, d),
            low: Math.min(a, b, c, d),
          })),
        ),
      );
    })
    .map((arr) => arr as ChartCandle[]);

/**
 * Generate order-flow ticks. Timestamps are spread across a wide range
 * (milliseconds) so some fall inside candle buckets (producing live cells) and
 * some fall outside (leaving candles synthetic). The property holds regardless.
 */
const tickArray = (): fc.Arbitrary<OrderFlowTick[]> =>
  fc.array(
    fc.record({
      timestamp: fc.integer({ min: 0, max: 1_000_000_000 }),
      price_level: price(),
      bid_volume: volume(),
      ask_volume: volume(),
      delta: fc.double({ min: -10_000, max: 10_000, noNaN: true, noDefaultInfinity: true }),
    }),
    { maxLength: 80 },
  );

describe('Property 18: Footprint delta and cumulative delta are correct sums', () => {
  it('each candle delta equals sum(ask) - sum(bid) and totalVolume equals sum(bid + ask)', () => {
    fc.assert(
      fc.property(
        candleSeries(),
        tickArray(),
        fc.double({ min: 5, max: 100, noNaN: true, noDefaultInfinity: true }),
        (candles, ticks, tickSize) => {
          const fps = buildFootprint(candles, ticks, { tickSize });

          expect(fps).toHaveLength(candles.length);

          for (const fp of fps) {
            const bidSum = fp.cells.reduce((acc, c) => acc + c.bid, 0);
            const askSum = fp.cells.reduce((acc, c) => acc + c.ask, 0);

            expect(approxEqual(fp.delta, askSum - bidSum)).toBe(true);
            expect(approxEqual(fp.totalVolume, bidSum + askSum)).toBe(true);
          }
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('cumulative delta is the running prefix sum of per-candle deltas, ending at the total', () => {
    fc.assert(
      fc.property(
        candleSeries(),
        tickArray(),
        fc.double({ min: 5, max: 100, noNaN: true, noDefaultInfinity: true }),
        (candles, ticks, tickSize) => {
          const fps = buildFootprint(candles, ticks, { tickSize });
          const cum = cumulativeDelta(fps);

          expect(cum).toHaveLength(fps.length);

          let running = 0;
          for (let i = 0; i < fps.length; i++) {
            running += fps[i].delta;
            expect(approxEqual(cum[i], running)).toBe(true);
          }

          if (fps.length > 0) {
            const totalDelta = fps.reduce((acc, fp) => acc + fp.delta, 0);
            expect(approxEqual(cum[cum.length - 1], totalDelta)).toBe(true);
          }
        },
      ),
      { numRuns: RUNS },
    );
  });
});
