// Feature: professional-charting-suite, Property 27
//
// Property-based test for Property 27: "Strategy summary reports a consistent
// count and numeric net result" (Validates Requirement 8.9).
//
// For any registered strategy and any candle series, summarizing the produced
// signals yields a `count` equal to the number of produced signals
// (`signals.length`) and a `netResult` that is a finite numeric value.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  STRATEGY_REGISTRY,
  listStrategies,
  getStrategy,
} from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

const RUNS = 100;

/** A finite, strictly-positive price value generator. */
const price = () =>
  fc.double({ min: 0.0001, max: 5_000, noNaN: true, noDefaultInfinity: true });

/**
 * Generate a well-formed candle series with strictly ascending unique
 * timestamps (seconds). high/low bracket open/close so every candle is valid.
 */
const candleSeries = (): fc.Arbitrary<ChartCandle[]> =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), { minLength: 0, maxLength: 60 })
    .chain((times) => {
      const sorted = [...times].sort((x, y) => x - y);
      if (sorted.length === 0) return fc.constant([] as ChartCandle[]);
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

describe('Property 27: Strategy summary reports a consistent count and numeric net result', () => {
  it('count equals signals.length and netResult is finite for every strategy and any candle series', () => {
    const ids = listStrategies();
    expect(ids.length).toBeGreaterThan(0);

    fc.assert(
      fc.property(fc.constantFrom(...ids), candleSeries(), (id, candles) => {
        const def = getStrategy(id);
        expect(def).toBeDefined();

        const signals = def!.evaluate(candles, def!.defaults);
        const summary = def!.summarize(signals, candles);

        // count is consistent with the number of produced signals
        expect(summary.count).toBe(signals.length);

        // netResult is a finite numeric value
        expect(typeof summary.netResult).toBe('number');
        expect(Number.isFinite(summary.netResult)).toBe(true);
      }),
      { numRuns: RUNS },
    );
  });

  it('holds across registered strategy definitions iterated directly', () => {
    fc.assert(
      fc.property(candleSeries(), (candles) => {
        for (const def of Object.values(STRATEGY_REGISTRY)) {
          const signals = def.evaluate(candles, def.defaults);
          const summary = def.summarize(signals, candles);

          expect(summary.count).toBe(signals.length);
          expect(Number.isFinite(summary.netResult)).toBe(true);
        }
      }),
      { numRuns: RUNS },
    );
  });
});
