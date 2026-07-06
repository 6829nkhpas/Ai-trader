// Feature: professional-charting-suite, Property 26
//
// Property-based test for Property 26: "Strategy signals are well-formed and
// anchored to candles" (Validates Requirement 8.2).
//
// For any strategy with sufficient lookback and for any candle series, every
// produced signal must:
//   - have a `kind` in the allowed set
//     (entry-long | exit-long | entry-short | exit-short),
//   - have a finite numeric `price`,
//   - have a `time` that matches the timestamp of some candle in the series
//     (the signal is anchored to a candle), and
//   - have a `price` equal to that anchoring candle's close.
//
// Every registered strategy is exercised against arbitrary, sufficiently-long
// candle series so the contract holds across the whole registry.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { listStrategies, getStrategy } from '@/charting/engines';
import type { SignalKind, StrategyDef } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

const RUNS = 100;

const ALLOWED_KINDS: ReadonlySet<SignalKind> = new Set<SignalKind>([
  'entry-long',
  'exit-long',
  'entry-short',
  'exit-short',
]);

/** Every registered strategy definition. */
const STRATEGY_DEFS: StrategyDef[] = listStrategies().map((id) => getStrategy(id)!);

/**
 * The largest required lookback across all registered strategies under their
 * default parameters. A series comfortably longer than this guarantees every
 * strategy has sufficient data and can therefore emit signals.
 */
const MAX_LOOKBACK = Math.max(
  ...STRATEGY_DEFS.map((def) => def.requiredLookback(def.defaults)),
);

/**
 * A random-walk OHLC candle series of at least `minLen` candles with strictly
 * ascending timestamps and valid high/low envelopes. The random walk produces
 * enough variation to trigger crossover, mean-reversion, and breakout signals.
 */
const candleSeries = (minLen: number): fc.Arbitrary<ChartCandle[]> =>
  fc
    .array(
      fc.record({
        move: fc.double({ min: -5, max: 5, noNaN: true, noDefaultInfinity: true }),
        spread: fc.double({ min: 0.1, max: 5, noNaN: true, noDefaultInfinity: true }),
      }),
      { minLength: minLen, maxLength: minLen + 60 },
    )
    .map((moves) => {
      const out: ChartCandle[] = [];
      let price = 100;
      for (let i = 0; i < moves.length; i += 1) {
        const open = price;
        const close = Math.max(0.01, open + moves[i].move);
        const high = Math.max(open, close) + moves[i].spread;
        const low = Math.max(0.001, Math.min(open, close) - moves[i].spread);
        out.push({ time: 1_000 + i * 60, open, high, low, close } as ChartCandle);
        price = close;
      }
      return out;
    });

describe('Property 26: strategy signals are well-formed and anchored to candles', () => {
  it('every registered strategy emits signals with allowed kinds, finite prices, and candle-anchored times', () => {
    fc.assert(
      fc.property(candleSeries(MAX_LOOKBACK + 10), (candles) => {
        // Map each candle time to its close for anchoring checks.
        const closeByTime = new Map<number, number>();
        for (const c of candles) closeByTime.set(c.time, c.close);

        for (const def of STRATEGY_DEFS) {
          // Sanity: the series is long enough for this strategy.
          expect(candles.length).toBeGreaterThanOrEqual(def.requiredLookback(def.defaults));

          const signals = def.evaluate(candles, def.defaults);

          for (const sig of signals) {
            // Kind is in the allowed set.
            expect(
              ALLOWED_KINDS.has(sig.kind),
              `${def.id} emitted disallowed kind "${sig.kind}"`,
            ).toBe(true);

            // Price is a finite number.
            expect(
              Number.isFinite(sig.price),
              `${def.id} signal price not finite`,
            ).toBe(true);

            // Time is anchored to an actual candle.
            expect(
              closeByTime.has(sig.time),
              `${def.id} signal time ${sig.time} not anchored to a candle`,
            ).toBe(true);

            // Price equals that candle's close.
            expect(
              sig.price,
              `${def.id} signal price should equal the anchoring candle close`,
            ).toBe(closeByTime.get(sig.time));
          }
        }
      }),
      { numRuns: RUNS },
    );
  });
});
