// Feature: professional-charting-suite, Property 2
//
// Property-based test for Property 2: "EMA uses the standard smoothing factor"
// (Validates Requirement 2.9).
//
// For any candle series and any valid period, the Exponential Moving Average
// satisfies the recurrence
//
//   ema[i] = price[i] * alpha + ema[i-1] * (1 - alpha)   with alpha = 2 / (period + 1)
//
// where `price` is the candle close series. The engine seeds the EMA with the
// simple moving average of the first `period` closes (emitted at candle index
// `period - 1`); from the next emitted point onward the recurrence above must
// hold between consecutive emitted EMA points.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { getIndicator } from '@/charting/engines';
import type { ChartCandle, LinePoint } from '@/charting/types';

const RUNS = 100;

/** Relative tolerance for floating-point equality of the EMA recurrence. */
const EPS = 1e-7;

const approxEqual = (a: number, b: number): boolean =>
  Math.abs(a - b) <= EPS * Math.max(1, Math.abs(a), Math.abs(b));

/** A single finite price value generator. */
const price = () =>
  fc.double({ min: 0.0001, max: 100_000, noNaN: true, noDefaultInfinity: true });

/** Generate a well-formed OHLC candle at a fixed time. */
const candleAt = (time: number): fc.Arbitrary<ChartCandle> =>
  fc.record({ a: price(), b: price(), c: price(), d: price() }).map(({ a, b, c, d }) => ({
    time,
    open: a,
    high: Math.max(a, b, c, d),
    low: Math.min(a, b, c, d),
    close: b,
  }));

/** Generate a candle series with strictly ascending unique timestamps. */
const candleSeries = (minLength: number) =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), { minLength, maxLength: 80 })
    .chain((times) => {
      const sorted = [...times].sort((x, y) => x - y);
      return fc.tuple(...sorted.map((t) => candleAt(t)));
    })
    .map((arr) => arr as ChartCandle[]);

/** Build a (series, period) pair where period never exceeds the candle count. */
const seriesAndPeriod = () =>
  fc.integer({ min: 1, max: 40 }).chain((period) =>
    candleSeries(period).map((series) => ({ series, period })),
  );

describe('Property 2: EMA uses the standard smoothing factor', () => {
  const ema = getIndicator('ema');

  it('is registered as an overlay indicator', () => {
    expect(ema).toBeDefined();
  });

  it('consecutive emitted EMA points satisfy ema[i] = price[i]*alpha + ema[i-1]*(1-alpha)', () => {
    fc.assert(
      fc.property(seriesAndPeriod(), ({ series, period }) => {
        const plot = ema!.compute(series, { period });

        // With period <= candle count the plot is always sufficient.
        expect(plot.insufficientData).not.toBe(true);
        expect(plot.lines).toHaveLength(1);

        const points: LinePoint[] = plot.lines[0].points;
        const alpha = 2 / (period + 1);

        // The first emitted EMA point is the seed (SMA of the first `period`
        // closes) anchored to candle index `period - 1`. The recurrence is
        // checked between every pair of consecutive emitted points, where the
        // price is the close of the candle the later point is anchored to.
        const closeAt = new Map<number, number>();
        for (const c of series) closeAt.set(c.time, c.close);

        for (let i = 1; i < points.length; i++) {
          const priceI = closeAt.get(points[i].time)!;
          const expected = priceI * alpha + points[i - 1].value * (1 - alpha);
          expect(approxEqual(points[i].value, expected)).toBe(true);
        }
      }),
      { numRuns: RUNS },
    );
  });

  it('seeds the first emitted EMA point with the SMA of the first `period` closes', () => {
    fc.assert(
      fc.property(seriesAndPeriod(), ({ series, period }) => {
        const plot = ema!.compute(series, { period });
        const points = plot.lines[0].points;

        // First emitted point is anchored to candle index period - 1.
        expect(points.length).toBeGreaterThan(0);
        expect(points[0].time).toBe(series[period - 1].time);

        let sum = 0;
        for (let i = 0; i < period; i++) sum += series[i].close;
        const expectedSeed = sum / period;
        expect(approxEqual(points[0].value, expectedSeed)).toBe(true);
      }),
      { numRuns: RUNS },
    );
  });
});
