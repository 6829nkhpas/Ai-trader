// Feature: professional-charting-suite, Property 6
//
// Property-based test for Property 6: "Live append equals full recompute"
// for the StrategyEngine (rule-based trading strategies).
// (Validates Requirement 8.7.)
//
// Statement (design.md, Property 6): for any engine that supports live updates
// and any candle series, appending a new candle and incrementally updating
// yields the same result as recomputing over the full extended series.
//
// Formalization for strategies. Each `StrategyDef.evaluate(candles, params)` is
// pure, deterministic, and causal: a signal anchored to candle `i` depends only
// on candles up to and including `i` (SMA/RSI use rolling windows ending at
// `i`; breakout uses the prior window strictly before `i`). Therefore appending
// a brand-new candle to the end of the series can only add a new tail signal —
// it must never retroactively change a signal that was already anchored to an
// earlier candle.
//
// We verify this directly. Let `full` be a sufficiently-long series and
// `prefix = full.slice(0, -1)` (the series just before the newest candle
// arrives). The prefix's last candle is `full[len - 2]`, so every signal the
// prefix can produce is anchored to a time strictly less than the newest
// candle's time. We compute signals over both series and assert that the full
// signals anchored to times BEFORE the newest candle's time are identical (same
// order, time, price, and kind) to the signals produced over the prefix. The
// signal (if any) anchored to the newest candle is the allowed "append" delta
// and is intentionally excluded.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { listStrategies, getStrategy } from '@/charting/engines';
import type { Signal } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

const RUNS = 100;

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
    close: c,
  }));

/**
 * Generate a candle series with strictly ascending unique timestamps. The
 * length is large enough (>= 40) that every registered strategy with its
 * default parameters (breakout needs lookback + 1 = 21, RSI needs period + 1 =
 * 15, MA-cross needs slow = 21) has sufficient data for both the full series
 * and the one-shorter prefix.
 */
const candleSeries = () =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 5_000_000 }), { minLength: 40, maxLength: 90 })
    .chain((times) => {
      const sorted = [...times].sort((x, y) => x - y);
      return fc.tuple(...sorted.map((t) => candleAt(t)));
    })
    .map((arr) => arr as ChartCandle[]);

/** Compare two signals for exact equality of time, price, and kind. */
const sameSignal = (a: Signal, b: Signal): boolean =>
  a.time === b.time && a.price === b.price && a.kind === b.kind;

describe('Property 6: Live append equals full recompute (strategies)', () => {
  const ids = listStrategies();

  it('has registered strategies to exercise', () => {
    expect(ids.length).toBeGreaterThan(0);
  });

  for (const id of ids) {
    const def = getStrategy(id)!;

    it(`${id}: appending a candle does not change earlier signals`, () => {
      fc.assert(
        fc.property(candleSeries(), (full) => {
          const prefix = full.slice(0, -1);
          const newestTime = full[full.length - 1].time;

          const fullSignals = def.evaluate(full, {});
          const prefixSignals = def.evaluate(prefix, {});

          // Signals from the full series that are anchored to candles BEFORE
          // the newly-appended candle. These must match the prefix exactly.
          const fullSettled = fullSignals.filter((s) => s.time < newestTime);

          expect(
            fullSettled.length,
            `${id}: settled signal count changed after append: ` +
              `full(before newest)=${fullSettled.length} prefix=${prefixSignals.length}`,
          ).toBe(prefixSignals.length);

          for (let i = 0; i < fullSettled.length; i++) {
            expect(
              sameSignal(fullSettled[i], prefixSignals[i]),
              `${id}: signal ${i} changed retroactively after append: ` +
                `full=${JSON.stringify(fullSettled[i])} ` +
                `prefix=${JSON.stringify(prefixSignals[i])}`,
            ).toBe(true);
          }
        }),
        { numRuns: RUNS },
      );
    });
  }
});
