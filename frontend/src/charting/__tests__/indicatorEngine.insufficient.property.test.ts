// Feature: professional-charting-suite, Property 4
//
// Property-based test for Property 4: "Insufficient data omits computation and
// signals insufficiency" (Validates Requirements 2.6, 3.8).
//
// For any registered indicator (overlay or oscillator) and any candle series
// strictly shorter than that indicator's required lookback, the engine must:
//   - produce NO plotted output (plot.lines is empty, no bands), and
//   - flag the result as insufficient data (plot.insufficientData === true),
//   - WITHOUT altering the input candle series.
//
// The series length for each indicator is chosen as a random fraction of its
// own minLookback, guaranteeing a length in [0, minLookback - 1] — i.e. always
// one candle short of (or further from) the threshold.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { listIndicators } from '@/charting/engines';
import type { IndicatorDef, IndicatorParams } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

const RUNS = 100;

/**
 * The largest default lookback across all registered indicators. A candle pool
 * this long lets us carve a short prefix for every indicator from one series.
 */
const MAX_LOOKBACK = Math.max(
  ...listIndicators().map((def) => def.minLookback(def.defaults)),
);

/**
 * A random-walk OHLC candle series of exactly `len` candles with strictly
 * ascending timestamps, valid high/low envelopes, and positive volume.
 */
const candlePool = (len: number): fc.Arbitrary<ChartCandle[]> =>
  fc
    .array(
      fc.record({
        move: fc.double({ min: -5, max: 5, noNaN: true, noDefaultInfinity: true }),
        spread: fc.double({ min: 0.1, max: 5, noNaN: true, noDefaultInfinity: true }),
        vol: fc.double({ min: 1, max: 10_000, noNaN: true, noDefaultInfinity: true }),
      }),
      { minLength: len, maxLength: len },
    )
    .map((moves) => {
      const out: ChartCandle[] = [];
      let price = 100;
      for (let i = 0; i < moves.length; i += 1) {
        const open = price;
        const close = Math.max(0.01, open + moves[i].move);
        const high = Math.max(open, close) + moves[i].spread;
        const low = Math.max(0.001, Math.min(open, close) - moves[i].spread);
        out.push({
          time: 1_000 + i * 60,
          open,
          high,
          low,
          close,
          volume: moves[i].vol,
        } as ChartCandle);
        price = close;
      }
      return out;
    });

describe('Property 4: insufficient data omits computation and signals insufficiency', () => {
  it('every indicator given fewer candles than its lookback emits no plot and flags insufficient data', () => {
    fc.assert(
      fc.property(
        // A pool long enough to slice a short prefix for any indicator, and a
        // per-indicator fraction in [0,1) used to pick a length < minLookback.
        candlePool(MAX_LOOKBACK),
        fc.double({ min: 0, max: 0.999999, noNaN: true, noDefaultInfinity: true }),
        (pool, fraction) => {
          const defs: IndicatorDef[] = listIndicators();

          for (const def of defs) {
            const params: IndicatorParams = def.defaults;
            const lookback = def.minLookback(params);

            // A length strictly below the indicator's required lookback.
            // floor(fraction * lookback) lands in [0, lookback - 1].
            const len = Math.floor(fraction * lookback);
            expect(len).toBeLessThan(lookback);

            const candles = pool.slice(0, len);
            // Snapshot to verify the input series is left unchanged.
            const before = JSON.stringify(candles);

            const plot = def.compute(candles, params);

            // Insufficiency is signalled.
            expect(
              plot.insufficientData,
              `${def.id} (len=${len}, lookback=${lookback}) should flag insufficientData`,
            ).toBe(true);

            // No plotted output: no lines and no bands.
            expect(
              plot.lines.length,
              `${def.id} (len=${len}, lookback=${lookback}) should plot no lines`,
            ).toBe(0);
            expect(
              plot.bands === undefined || plot.bands.length === 0,
              `${def.id} (len=${len}, lookback=${lookback}) should plot no bands`,
            ).toBe(true);

            // The input series is left unchanged.
            expect(
              JSON.stringify(candles),
              `${def.id} should not mutate the input series`,
            ).toBe(before);
          }
        },
      ),
      { numRuns: RUNS },
    );
  });
});
