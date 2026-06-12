// Feature: professional-charting-suite, Property 28
//
// Property-based test for Property 28: "Canonical candle series is sorted and
// de-duplicated" (Validates Requirement 9.6).
//
// For any set of raw candles (including out-of-order arrivals, duplicate
// timestamps, and mixed-case symbols), and any requested symbol, the canonical
// series produced by `canonicalCandles(raw, symbol)`:
//   - is sorted strictly ascending by `time`,
//   - contains no duplicate timestamps,
//   - contains only candles for the requested symbol (case-insensitive),
//   - is produced without ever throwing.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { canonicalCandles } from '@/charting/engines';
import type { OhlcCandle } from '@/store/useTradeStore';

const RUNS = 100;

/** A single finite OHLC value generator. */
const price = () =>
  fc.double({ min: 0.0001, max: 100_000, noNaN: true, noDefaultInfinity: true });

/**
 * Map a base symbol to an arbitrary mixed-case variant so we exercise the
 * case-insensitive matching contract (e.g. "btcusdt" -> "BtCuSdT").
 */
const mixedCase = (symbol: string): fc.Arbitrary<string> =>
  fc
    .array(fc.boolean(), { minLength: symbol.length, maxLength: symbol.length })
    .map((flags) =>
      symbol
        .split('')
        .map((ch, i) => (flags[i] ? ch.toUpperCase() : ch.toLowerCase()))
        .join(''),
    );

/**
 * A raw OHLC candle generator drawing its symbol from a small alphabet of base
 * symbols (with arbitrary casing) and a small range of timestamps so that
 * duplicate and out-of-order timestamps occur frequently across a buffer.
 */
const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'];

const rawCandle = (): fc.Arbitrary<OhlcCandle> =>
  fc
    .constantFrom(...SYMBOLS)
    .chain((base) => mixedCase(base))
    .chain((symbol) =>
      fc.record({
        symbol: fc.constant(symbol),
        // Small ms range, multiplied so distinct ms can collapse to the same
        // whole-second `time`, exercising de-duplication thoroughly.
        start_timestamp_ms: fc.integer({ min: 0, max: 20 }).map((s) => s * 1000),
        open: price(),
        high: price(),
        low: price(),
        close: price(),
        volume: fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true }),
      }),
    );

/** A buffer of raw candles, typically containing duplicates / out-of-order times. */
const rawBuffer = (): fc.Arbitrary<OhlcCandle[]> =>
  fc.array(rawCandle(), { minLength: 0, maxLength: 60 });

describe('Property 28: canonical candle series is sorted and de-duplicated', () => {
  it('output is strictly ascending, de-duplicated, symbol-filtered, and never throws', () => {
    fc.assert(
      fc.property(rawBuffer(), fc.constantFrom(...SYMBOLS), (raw, baseSymbol) => {
        // Use an arbitrary-cased requested symbol to assert case-insensitivity.
        const requested = baseSymbol.toLowerCase();

        // Never throws.
        const series = canonicalCandles(raw, requested);

        // Strictly ascending by time, which also implies no duplicate timestamps.
        for (let i = 1; i < series.length; i += 1) {
          expect(series[i].time).toBeGreaterThan(series[i - 1].time);
        }

        // No duplicate timestamps (redundant with strict ascending, asserted
        // directly for clarity).
        const times = series.map((c) => c.time);
        expect(new Set(times).size).toBe(times.length);

        // Every emitted candle's timestamp came from a raw candle whose symbol
        // matches the requested symbol case-insensitively.
        const wanted = requested.toUpperCase();
        const expectedTimes = new Set(
          raw
            .filter((c) => c.symbol.toUpperCase() === wanted)
            .map((c) => Math.floor(c.start_timestamp_ms / 1000)),
        );
        for (const t of times) {
          expect(expectedTimes.has(t)).toBe(true);
        }

        // Completeness: every requested-symbol timestamp is represented.
        expect(new Set(times)).toEqual(expectedTimes);
      }),
      { numRuns: RUNS },
    );
  });

  it('handles uppercase, lowercase, and mixed-case requested symbols identically', () => {
    fc.assert(
      fc.property(rawBuffer(), fc.constantFrom(...SYMBOLS), (raw, baseSymbol) => {
        const lower = canonicalCandles(raw, baseSymbol.toLowerCase());
        const upper = canonicalCandles(raw, baseSymbol.toUpperCase());
        expect(lower).toEqual(upper);
      }),
      { numRuns: RUNS },
    );
  });

  it('never throws and returns an empty series for a symbol not present', () => {
    fc.assert(
      fc.property(rawBuffer(), (raw) => {
        const series = canonicalCandles(raw, 'NOSUCHSYMBOL');
        expect(series).toEqual([]);
      }),
      { numRuns: RUNS },
    );
  });
});
