// Feature: professional-charting-suite, Property 6
//
// Property-based test for Property 6: "Live append equals full recompute"
// applied to the footprint engine (Validates Requirement 6.10).
//
// `buildFootprint` processes each candle independently from the ticks in its
// own time bucket, so appending a new (in-progress) candle to the right of the
// series must never retroactively change the footprint of any earlier candle.
//
// Formalization: for any candle series of length >= 3 and any tick buffer,
// computing buildFootprint over the full series yields, for every candle except
// the last (possibly in-progress) one, footprint candles identical to computing
// buildFootprint over the series with its last candle removed. We assert deep
// equality across the entire overlapping prefix.
//
// Note on bucketing: the engine infers the candle interval from the smallest
// positive gap between candle starts, and assigns all ticks to candle 0 when a
// single candle is supplied. To exercise genuine live-append semantics we
// generate candles on a *regular* time grid and keep the prefix length >= 2, so
// the inferred interval is identical whether or not the trailing candle is
// present. This isolates the property under test (no retroactive mutation)
// rather than the interval-inference edge cases.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { buildFootprint } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';
import type { OrderFlowTick } from '@/store/useTradeStore';

const RUNS = 100;

/** A finite, bounded price value generator (avoids heap blowups). */
const price = () =>
  fc.double({ min: 0.0001, max: 5_000, noNaN: true, noDefaultInfinity: true });

/** A finite, non-negative volume value generator. */
const volume = () =>
  fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true });

/**
 * Generate a well-formed candle series on a *regular* time grid (seconds), with
 * at least 3 candles so the overlapping prefix has >= 2 candles and the engine's
 * inferred bucket interval is stable across the full / prefix recomputations.
 * high/low bracket open/close.
 */
const candleSeries = (): fc.Arbitrary<ChartCandle[]> =>
  fc
    .record({
      start: fc.integer({ min: 0, max: 100_000 }),
      intervalSec: fc.integer({ min: 30, max: 3_600 }),
      count: fc.integer({ min: 3, max: 30 }),
    })
    .chain(({ start, intervalSec, count }) => {
      const times = Array.from({ length: count }, (_, i) => start + i * intervalSec);
      return fc.tuple(
        ...times.map((t) =>
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
 * Generate order-flow ticks spread across a wide timestamp range (milliseconds)
 * so some fall inside candle buckets (producing live cells) and some fall
 * outside (leaving candles synthetic). The property holds regardless.
 */
const tickArray = (): fc.Arbitrary<OrderFlowTick[]> =>
  fc.array(
    fc.record({
      timestamp: fc.integer({ min: 0, max: 200_000_000 }),
      price_level: price(),
      bid_volume: volume(),
      ask_volume: volume(),
      delta: fc.double({ min: -10_000, max: 10_000, noNaN: true, noDefaultInfinity: true }),
    }),
    { maxLength: 80 },
  );

describe('Property 6: Footprint live append equals full recompute', () => {
  it('appending a new candle does not retroactively change earlier footprint candles', () => {
    fc.assert(
      fc.property(
        candleSeries(),
        tickArray(),
        fc.double({ min: 5, max: 100, noNaN: true, noDefaultInfinity: true }),
        (candles, ticks, tickSize) => {
          // Full recompute over the extended series (with the appended candle).
          const full = buildFootprint(candles, ticks, { tickSize });
          // Recompute over the series *before* the trailing candle was appended.
          const prefix = buildFootprint(candles.slice(0, -1), ticks, { tickSize });

          expect(full).toHaveLength(candles.length);
          expect(prefix).toHaveLength(candles.length - 1);

          // Every candle in the overlapping prefix must be byte-for-byte identical:
          // appending the new candle changed nothing about the earlier ones.
          for (let i = 0; i < prefix.length; i++) {
            expect(full[i]).toEqual(prefix[i]);
          }
        },
      ),
      { numRuns: RUNS },
    );
  });
});
