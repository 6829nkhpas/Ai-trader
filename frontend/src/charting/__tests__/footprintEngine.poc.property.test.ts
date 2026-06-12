// Feature: professional-charting-suite, Property 22
//
// Property-based test for Property 22: "Footprint POC is the greatest-volume
// level with close tie-break" (Validates Requirement 6.7).
//
// For any footprint candle, the POC is the price level (cell) with the greatest
// total volume (bid + ask). When multiple levels tie for greatest volume, the
// POC is the level closest to the candle's close, and on an equidistant tie the
// lower price is chosen for determinism. `poc` is null only when the candle has
// no cells.
//
// The test asserts the engine output against an INDEPENDENT oracle: it computes
// the maximum per-cell volume, filters to the tying cells, and selects the one
// nearest the close (lower price on an equidistant tie) — a different algorithm
// from the engine's single-pass reduction.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { buildFootprint } from '@/charting/engines';
import type { FootprintCell } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';
import type { OrderFlowTick } from '@/store/useTradeStore';

const RUNS = 100;

/** Finite, positive price value generator (bounded to avoid heap blowups). */
const price = () =>
  fc.double({ min: 0.0001, max: 5_000, noNaN: true, noDefaultInfinity: true });

/** Finite, non-negative volume value generator. */
const volume = () =>
  fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true });

/**
 * Generate a well-formed candle series with strictly ascending unique
 * timestamps (seconds). high/low bracket open/close so each candle is valid.
 */
const candleSeries = (): fc.Arbitrary<ChartCandle[]> =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), { minLength: 1, maxLength: 30 })
    .chain((times) => {
      const sorted = [...times].sort((x, y) => x - y);
      return fc.tuple(
        ...sorted.map((t) =>
          fc
            .record({ a: price(), b: price(), c: price(), d: price() })
            .map(({ a, b, c, d }) => ({
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
 * Generate order-flow ticks spread across a wide millisecond range so some fall
 * inside candle buckets (producing live cells) and some leave candles synthetic.
 * Property 22 holds for both live and synthetic cells.
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

/** Bounded tick size generator (5–100) to keep synthetic cell counts small. */
const tickSizeArb = () =>
  fc.double({ min: 5, max: 100, noNaN: true, noDefaultInfinity: true });

/**
 * Independent oracle: the expected POC price for a set of cells given the
 * candle close. Greatest total volume wins; ties broken by proximity to close,
 * then by lower price. Returns null for an empty set.
 */
function expectedPoc(cells: FootprintCell[], close: number): number | null {
  if (cells.length === 0) return null;

  const vol = (c: FootprintCell) => c.bid + c.ask;
  const maxVol = cells.reduce((m, c) => Math.max(m, vol(c)), -Infinity);

  const tying = cells.filter((c) => vol(c) === maxVol);

  let best = tying[0];
  let bestDist = Math.abs(best.price - close);
  for (let i = 1; i < tying.length; i += 1) {
    const dist = Math.abs(tying[i].price - close);
    if (dist < bestDist || (dist === bestDist && tying[i].price < best.price)) {
      best = tying[i];
      bestDist = dist;
    }
  }
  return best.price;
}

describe('Property 22: Footprint POC is the greatest-volume level with close tie-break', () => {
  it('poc is the greatest-volume cell, ties broken by proximity to close then lower price', () => {
    fc.assert(
      fc.property(candleSeries(), tickArray(), tickSizeArb(), (candles, ticks, tickSize) => {
        const fps = buildFootprint(candles, ticks, { tickSize });

        expect(fps).toHaveLength(candles.length);

        candles.forEach((candle, idx) => {
          const fp = fps[idx];

          if (fp.cells.length === 0) {
            // poc is null only when there are no cells.
            expect(fp.poc).toBeNull();
            return;
          }

          // poc must be non-null and correspond to an actual cell.
          expect(fp.poc).not.toBeNull();
          const pocCell = fp.cells.find((c) => c.price === fp.poc);
          expect(pocCell).toBeDefined();

          // The poc cell's total volume must be the maximum across all cells.
          const maxVol = fp.cells.reduce((m, c) => Math.max(m, c.bid + c.ask), -Infinity);
          expect(pocCell!.bid + pocCell!.ask).toBe(maxVol);

          // And among the tying max-volume cells it must be the close-tie-break
          // winner (independent oracle).
          expect(fp.poc).toBe(expectedPoc(fp.cells, candle.close));
        });
      }),
      { numRuns: RUNS },
    );
  });
});
