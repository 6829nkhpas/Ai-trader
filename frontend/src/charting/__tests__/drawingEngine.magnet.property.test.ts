// Feature: professional-charting-suite, Property 14
//
// Property-based test for Property 14: "Magnet snaps to the nearest OHLC
// within threshold, else to the pointer" (Validates Requirement 5.6).
//
// For any pointer position, candle, pixels-per-price density, and snap
// threshold, magnet mode snaps the anchor's price to the nearest of the
// candle's open/high/low/close when that value's pixel distance is within the
// threshold, and otherwise places the anchor at the exact pointer coordinates.
// In every case the anchor's time is left unchanged.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { magnetSnap } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';
import type { Point } from '@/store/useChartUIStore';

const RUNS = 100;

/** A finite price value generator. */
const price = () =>
  fc.double({ min: -100_000, max: 100_000, noNaN: true, noDefaultInfinity: true });

/** A finite pointer position generator. */
const pointArb = (): fc.Arbitrary<Point> =>
  fc.record({
    time: fc.integer({ min: 0, max: 1_000_000 }),
    price: price(),
  });

/** A candle with arbitrary finite OHLC values. */
const candleArb = (): fc.Arbitrary<ChartCandle> =>
  fc.record({
    time: fc.integer({ min: 0, max: 1_000_000 }),
    open: price(),
    high: price(),
    low: price(),
    close: price(),
  });

/** Non-negative pixels-per-price density. */
const pxPerPriceArb = () =>
  fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true });

/** Non-negative snap threshold in pixels. */
const thresholdArb = () =>
  fc.double({ min: 0, max: 1_000, noNaN: true, noDefaultInfinity: true });

/**
 * Reference computation of the nearest OHLC value and its pixel distance,
 * mirroring the engine's first-wins tie-break over [open, high, low, close].
 */
function nearestOHLC(pointer: Point, candle: ChartCandle, pxPerPrice: number) {
  const candidates = [candle.open, candle.high, candle.low, candle.close];
  let bestValue = candidates[0];
  let bestDistPx = Infinity;
  for (const value of candidates) {
    const distPx = Math.abs(pointer.price - value) * pxPerPrice;
    if (distPx < bestDistPx) {
      bestDistPx = distPx;
      bestValue = value;
    }
  }
  return { bestValue, bestDistPx };
}

describe('Property 14: magnet snaps to the nearest OHLC within threshold, else to the pointer', () => {
  it('snaps to the nearest OHLC when within threshold, otherwise returns the pointer; time never changes', () => {
    fc.assert(
      fc.property(
        pointArb(),
        candleArb(),
        pxPerPriceArb(),
        thresholdArb(),
        (pointer, candle, pxPerPrice, thresholdPx) => {
          const result = magnetSnap(pointer, candle, pxPerPrice, thresholdPx);

          // Time is always preserved.
          expect(result.time).toBe(pointer.time);

          const { bestValue, bestDistPx } = nearestOHLC(pointer, candle, pxPerPrice);

          if (bestDistPx <= thresholdPx) {
            // Within threshold: price snaps to the nearest OHLC value.
            expect(result.price).toBe(bestValue);
          } else {
            // Outside threshold: anchor stays at the exact pointer price.
            expect(result.price).toBe(pointer.price);
          }
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('uses the default 10 px threshold when none is supplied', () => {
    fc.assert(
      fc.property(pointArb(), candleArb(), pxPerPriceArb(), (pointer, candle, pxPerPrice) => {
        const result = magnetSnap(pointer, candle, pxPerPrice);
        expect(result.time).toBe(pointer.time);

        const { bestValue, bestDistPx } = nearestOHLC(pointer, candle, pxPerPrice);
        if (bestDistPx <= 10) {
          expect(result.price).toBe(bestValue);
        } else {
          expect(result.price).toBe(pointer.price);
        }
      }),
      { numRuns: RUNS },
    );
  });
});
