// Feature: professional-charting-suite, Property 20
//
// Property: a candle with no order-flow ticks produces NO cells.
//
// This replaces the former "falls back to a flagged synthetic distribution"
// property, which asserted the opposite: that a tick-less candle was filled in
// with a generated bell-curve bid/ask spread. That generated distribution was
// never measured, and because Delta, total volume, POC, the imbalance levels and
// the running Cumulative_Delta are all derived from the cells, inventing the cells
// meant inventing every order-flow number the chart displays.
//
// The engine now reports the absence instead: `cells` is empty, the derived
// metrics are zero/null, and `hasOrderFlow` is false so the renderer can say so.
// Candles that DO have ticks in their bucket are unchanged — computed from live
// data, `hasOrderFlow: true`.
//
// Generators stay bounded (price 0.0001..5000, tickSize 5..100) to keep the row
// count small.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { buildFootprint } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';
import type { OrderFlowTick } from '@/store/useTradeStore';

const RUNS = 100;

/** Bounded finite price generator (Requirement: keep synthetic rows finite). */
const price = () =>
  fc.double({ min: 0.0001, max: 5_000, noNaN: true, noDefaultInfinity: true });

/** Bounded finite, non-negative volume generator. */
const volume = () =>
  fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true });

/** Bounded tick size — large enough to keep the synthetic row count small. */
const tickSize = () =>
  fc.double({ min: 5, max: 100, noNaN: true, noDefaultInfinity: true });

/**
 * Well-formed candle series with strictly ascending unique timestamps
 * (seconds); high/low bracket open/close.
 */
const candleSeries = (): fc.Arbitrary<ChartCandle[]> =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), { minLength: 1, maxLength: 20 })
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
 * Regular-grid candle series: times are 0, interval, 2*interval, ... so a tick
 * placed at exactly `candle.time * 1000` is deterministically bucketed to that
 * candle. Each candle carries a boolean flag indicating whether it should
 * receive a live tick.
 */
const griddedScenario = (): fc.Arbitrary<{
  candles: ChartCandle[];
  hasTick: boolean[];
}> =>
  fc
    .tuple(
      fc.integer({ min: 60, max: 3_600 }), // interval (seconds)
      fc.array(
        fc.record({
          a: price(),
          b: price(),
          c: price(),
          d: price(),
          tick: fc.boolean(),
        }),
        { minLength: 2, maxLength: 12 },
      ),
    )
    .map(([interval, rows]) => {
      const candles: ChartCandle[] = rows.map((r, i) => ({
        time: i * interval,
        open: r.a,
        close: r.b,
        high: Math.max(r.a, r.b, r.c, r.d),
        low: Math.min(r.a, r.b, r.c, r.d),
      }));
      const hasTick = rows.map((r) => r.tick);
      return { candles, hasTick };
    });

describe('Footprint reports absent order flow instead of inventing it', () => {
  it('an empty tick array yields no cells and no derived metrics for every candle', () => {
    fc.assert(
      fc.property(candleSeries(), tickSize(), (candles, ts) => {
        // No ticks at all → nothing measured, so nothing reported.
        const fps = buildFootprint(candles, [] as OrderFlowTick[], { tickSize: ts });

        expect(fps).toHaveLength(candles.length);

        for (const fp of fps) {
          expect(fp.hasOrderFlow).toBe(false);
          // The core guarantee: no fabricated cells.
          expect(fp.cells).toHaveLength(0);
          // And nothing derived from them is presented as a measurement.
          expect(fp.delta).toBe(0);
          expect(fp.totalVolume).toBe(0);
          expect(fp.poc).toBeNull();
          expect(fp.imbalances).toHaveLength(0);
        }
      }),
      { numRuns: RUNS },
    );
  });

  it('candles with bucketed ticks are live; candles without ticks report no flow', () => {
    fc.assert(
      fc.property(griddedScenario(), tickSize(), price(), volume(), (scenario, ts, plvl, vol) => {
        const { candles, hasTick } = scenario;

        // Build one tick per flagged candle, placed exactly at its start time so
        // the engine's bucketing assigns it deterministically to that candle.
        const ticks: OrderFlowTick[] = [];
        candles.forEach((candle, i) => {
          if (hasTick[i]) {
            ticks.push({
              timestamp: candle.time * 1000,
              price_level: plvl,
              bid_volume: vol,
              ask_volume: vol,
              delta: 0,
            });
          }
        });

        const fps = buildFootprint(candles, ticks, { tickSize: ts });
        expect(fps).toHaveLength(candles.length);

        fps.forEach((fp, i) => {
          if (hasTick[i]) {
            // Real order flow in this bucket → measured cells.
            expect(fp.hasOrderFlow).toBe(true);
            expect(fp.cells.length).toBeGreaterThan(0);
          } else {
            // No ticks in this bucket → reported as absent, NOT filled in. This is
            // the mixed-series case: a gap must stay a gap even when its
            // neighbours have data, which is exactly where a fabricated cluster
            // was most misleading.
            expect(fp.hasOrderFlow).toBe(false);
            expect(fp.cells).toHaveLength(0);
            expect(fp.totalVolume).toBe(0);
          }
        });
      }),
      { numRuns: RUNS },
    );
  });
});
