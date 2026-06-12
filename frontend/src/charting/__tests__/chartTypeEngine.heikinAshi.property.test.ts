// Feature: professional-charting-suite, Property 1
//
// Property-based test for Property 1: "Heikin Ashi close is the source candle
// average" (Validates Requirement 1.7).
//
// For any candle series, every computed Heikin Ashi candle's close equals the
// arithmetic average of the corresponding source candle's open, high, low, and
// close, and each HA open equals the average of the previous HA open and close
// (seeded from the first source candle as (open + close) / 2).

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { computeHeikinAshi } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

const RUNS = 100;

/** A relative tolerance for floating-point equality of computed averages. */
const EPS = 1e-9;

const approxEqual = (a: number, b: number): boolean =>
  Math.abs(a - b) <= EPS * Math.max(1, Math.abs(a), Math.abs(b));

/** A single finite price value generator. */
const price = () =>
  fc.double({ min: 0.0001, max: 100_000, noNaN: true, noDefaultInfinity: true });

/**
 * Generate an arbitrary OHLC candle at a fixed time. high/low are derived to
 * bracket open/close so the candle is well-formed, but the property holds
 * regardless of OHLC ordering.
 */
const candleAt = (time: number): fc.Arbitrary<ChartCandle> =>
  fc.record({ a: price(), b: price(), c: price(), d: price() }).map(({ a, b, c, d }) => {
    const open = a;
    const close = b;
    const high = Math.max(a, b, c, d);
    const low = Math.min(a, b, c, d);
    return { time, open, high, low, close };
  });

/** Generate a candle series with strictly ascending unique timestamps. */
const candleSeries = (): fc.Arbitrary<ChartCandle[]> =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), { minLength: 1, maxLength: 60 })
    .chain((times) => {
      const sorted = [...times].sort((x, y) => x - y);
      return fc.tuple(...sorted.map((t) => candleAt(t)));
    })
    .map((arr) => arr as ChartCandle[]);

describe('Property 1: Heikin Ashi close is the source candle average', () => {
  it('each HA close equals (open + high + low + close) / 4 of the source candle', () => {
    fc.assert(
      fc.property(candleSeries(), (series) => {
        const ha = computeHeikinAshi(series);

        expect(ha).toHaveLength(series.length);
        for (let i = 0; i < series.length; i++) {
          const src = series[i];
          const expectedClose = (src.open + src.high + src.low + src.close) / 4;
          expect(approxEqual(ha[i].close, expectedClose)).toBe(true);
          // HA candles stay anchored to the source candle's time.
          expect(ha[i].time).toBe(src.time);
        }
      }),
      { numRuns: RUNS },
    );
  });

  it('each HA open equals the average of the previous HA open and close (seeded from the first source candle)', () => {
    fc.assert(
      fc.property(candleSeries(), (series) => {
        const ha = computeHeikinAshi(series);

        // First HA open is seeded from the first source candle.
        const expectedFirstOpen = (series[0].open + series[0].close) / 2;
        expect(approxEqual(ha[0].open, expectedFirstOpen)).toBe(true);

        // Subsequent HA opens follow the recurrence.
        for (let i = 1; i < ha.length; i++) {
          const expectedOpen = (ha[i - 1].open + ha[i - 1].close) / 2;
          expect(approxEqual(ha[i].open, expectedOpen)).toBe(true);
        }
      }),
      { numRuns: RUNS },
    );
  });
});
