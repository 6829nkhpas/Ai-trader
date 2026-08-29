// Feature: professional-charting-suite, Property 19
//
// Property-based test for Property 19: "Footprint clustering groups by tick
// size and conserves volume" (Validates Requirements 6.1, 6.2, 6.9).
//
// For any candle and order-flow ticks with a given tick size:
//   - every cluster cell's price is an exact multiple of the tick size; and
//   - for candles built from live ticks, the cell bid/ask sums equal the
//     grouped tick volumes (clustering conserves volume); and
//   - regrouping the same data under a different tick size preserves the
//     candle's total volume.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { buildFootprint } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';
import type { OrderFlowTick } from '@/store/useTradeStore';

const RUNS = 100;

/** Tolerance for floating-point equality of summed volumes/prices. */
const EPS = 1e-6;

const approxEqual = (a: number, b: number): boolean =>
  Math.abs(a - b) <= EPS * Math.max(1, Math.abs(a), Math.abs(b));

/**
 * `price` is bounded to 0.0001..5000 and tick sizes to 5..100 so the
 * synthetic-cell fallback (which spans high..low at tickSize granularity)
 * never allocates excessively — range / tickSize stays small.
 */
const price = () =>
  fc.double({ min: 0.0001, max: 5_000, noNaN: true, noDefaultInfinity: true });

const volume = () =>
  fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true });

const tickSize = () =>
  fc.double({ min: 5, max: 100, noNaN: true, noDefaultInfinity: true });

/**
 * Generate a well-formed candle series with strictly ascending unique
 * timestamps (seconds). high/low bracket open/close.
 */
const candleSeries = (): fc.Arbitrary<ChartCandle[]> =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), { minLength: 1, maxLength: 20 })
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

const tickRecord = () =>
  fc.record({
    timestamp: fc.integer({ min: 0, max: 1_000_000_000 }),
    price_level: price(),
    bid_volume: volume(),
    ask_volume: volume(),
    delta: fc.double({ min: -10_000, max: 10_000, noNaN: true, noDefaultInfinity: true }),
  });

const tickArray = (): fc.Arbitrary<OrderFlowTick[]> =>
  fc.array(tickRecord(), { maxLength: 60 });

/** A single candle paired with at least one tick, forcing a live cluster. */
const singleCandleWithTicks = (): fc.Arbitrary<{
  candle: ChartCandle;
  ticks: OrderFlowTick[];
}> =>
  fc
    .tuple(
      fc.integer({ min: 0, max: 1_000_000 }),
      fc.record({ a: price(), b: price(), c: price(), d: price() }),
      fc.array(tickRecord(), { minLength: 1, maxLength: 60 }),
    )
    .map(([t, { a, b, c, d }, ticks]) => ({
      candle: {
        time: t,
        open: a,
        close: b,
        high: Math.max(a, b, c, d),
        low: Math.min(a, b, c, d),
      } as ChartCandle,
      ticks,
    }));

describe('Property 19: Footprint clustering groups by tick size and conserves volume', () => {
  it('every cluster cell price is an exact multiple of the tick size', () => {
    fc.assert(
      fc.property(candleSeries(), tickArray(), tickSize(), (candles, ticks, ts) => {
        const fps = buildFootprint(candles, ticks, { tickSize: ts });

        for (const fp of fps) {
          for (const cell of fp.cells) {
            const k = Math.round(cell.price / ts);
            expect(approxEqual(cell.price, k * ts)).toBe(true);
          }
        }
      }),
      { numRuns: RUNS },
    );
  });

  it('for a live candle, total cell volume equals the sum of contributing tick volumes', () => {
    // A single candle has no inferrable interval, so every tick is assigned to
    // it: the cluster is live and must conserve the full tick volume.
    fc.assert(
      fc.property(singleCandleWithTicks(), tickSize(), ({ candle, ticks }, ts) => {
        const fps = buildFootprint([candle], ticks, { tickSize: ts });

        expect(fps).toHaveLength(1);
        const fp = fps[0];
        expect(fp.hasOrderFlow).toBe(true);

        const tickVolume = ticks.reduce((acc, t) => acc + t.bid_volume + t.ask_volume, 0);
        const cellVolume = fp.cells.reduce((acc, c) => acc + c.bid + c.ask, 0);

        expect(approxEqual(cellVolume, tickVolume)).toBe(true);
        expect(approxEqual(fp.totalVolume, tickVolume)).toBe(true);
      }),
      { numRuns: RUNS },
    );
  });

  it('regrouping the same data under a different tick size preserves total volume', () => {
    fc.assert(
      fc.property(
        singleCandleWithTicks(),
        tickSize(),
        tickSize(),
        ({ candle, ticks }, ts1, ts2) => {
          const a = buildFootprint([candle], ticks, { tickSize: ts1 })[0];
          const b = buildFootprint([candle], ticks, { tickSize: ts2 })[0];

          // Both clusters are live (single candle owns every tick), so total
          // volume is invariant under the tick-size regrouping.
          expect(a.hasOrderFlow).toBe(true);
          expect(b.hasOrderFlow).toBe(true);
          expect(approxEqual(a.totalVolume, b.totalVolume)).toBe(true);
        },
      ),
      { numRuns: RUNS },
    );
  });
});
