// Feature: professional-charting-suite, Property 23
//
// Property-based test for Property 23: "Volume profile binning conserves
// volume and row count" (Validates Requirements 7.2, 7.6).
//
// For any candle/volume range and for any configured row count in 1–1000, the
// profile produces exactly that many rows (the clamped requested count) and the
// sum of the row volumes equals the total traded volume contributed by the
// candles in the range.
//
// The test asserts the engine output against INDEPENDENT oracles:
//  - row count: re-derives the clamped row count (default 24, rounded, clamped
//    into [1, 1000]) without calling the engine's private normalizer.
//  - conservation: sums the matched per-candle input volumes directly and
//    compares against `profile.totalVolume` and against the sum of the
//    individual `profile.rows[*].volume` values, within a float tolerance.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { buildProfile } from '@/charting/engines';
import type { ChartCandle, VolumeBar } from '@/charting/types';

const RUNS = 100;

const DEFAULT_ROWS = 24;
const MIN_ROWS = 1;
const MAX_ROWS = 1000;

/** Finite, positive price generator (bounded to avoid heap blowups). */
const price = () =>
  fc.double({ min: 0.0001, max: 5_000, noNaN: true, noDefaultInfinity: true });

/** Finite, non-negative volume generator. */
const volume = () =>
  fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true });

/**
 * Generate a well-formed candle series (strictly ascending unique times) paired
 * with one matching volume bar per candle (matched by time). high/low bracket
 * open/close so each candle is valid.
 */
const candlesWithVolumes = (): fc.Arbitrary<{
  candles: ChartCandle[];
  volumes: VolumeBar[];
}> =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), { minLength: 0, maxLength: 30 })
    .chain((times) => {
      const sorted = [...times].sort((x, y) => x - y);
      return fc.tuple(
        ...sorted.map((t) =>
          fc
            .record({ a: price(), b: price(), c: price(), d: price(), v: volume() })
            .map(({ a, b, c, d, v }) => ({
              candle: {
                time: t,
                open: a,
                close: b,
                high: Math.max(a, b, c, d),
                low: Math.min(a, b, c, d),
              } as ChartCandle,
              vol: { time: t, value: v } as VolumeBar,
            })),
        ),
      );
    })
    .map((pairs) => ({
      candles: pairs.map((p) => p.candle),
      volumes: pairs.map((p) => p.vol),
    }));

/**
 * Row-count input generator: sometimes omitted (→ default 24), otherwise an
 * integer spanning well below MIN and above MAX so clamping is exercised.
 */
const rowsArb = (): fc.Arbitrary<number | undefined> =>
  fc.oneof(
    fc.constant<number | undefined>(undefined),
    fc.integer({ min: -50, max: 1500 }),
  );

/** Independent oracle for the clamped row count. */
function expectedRows(rows: number | undefined): number {
  if (typeof rows !== 'number' || !Number.isFinite(rows)) return DEFAULT_ROWS;
  const rounded = Math.round(rows);
  if (rounded < MIN_ROWS) return MIN_ROWS;
  if (rounded > MAX_ROWS) return MAX_ROWS;
  return rounded;
}

describe('Property 23: Volume profile binning conserves volume and row count', () => {
  it('produces exactly the clamped requested row count and conserves total volume', () => {
    fc.assert(
      fc.property(candlesWithVolumes(), rowsArb(), ({ candles, volumes }, rows) => {
        const profile = buildProfile(candles, volumes, { rows });

        // ── Row-count invariant (Requirement 7.2) ────────────────────────────
        const wantRows = expectedRows(rows);
        expect(profile.rows).toHaveLength(wantRows);

        // ── Volume conservation (Requirement 7.6) ────────────────────────────
        // Every candle is in range (default 'visible') and has a matching,
        // non-negative volume bar, so the expected total is the simple sum.
        const expectedTotal = volumes.reduce((sum, v) => sum + v.value, 0);

        const rowSum = profile.rows.reduce((sum, r) => sum + r.volume, 0);

        // Float tolerance scaled to the magnitude of the summed volume.
        const tol = 1e-6 + 1e-9 * expectedTotal;

        expect(Math.abs(profile.totalVolume - expectedTotal)).toBeLessThanOrEqual(tol);
        expect(Math.abs(rowSum - expectedTotal)).toBeLessThanOrEqual(tol);
        // totalVolume must agree with the summed row volumes.
        expect(Math.abs(profile.totalVolume - rowSum)).toBeLessThanOrEqual(tol);
      }),
      { numRuns: RUNS },
    );
  });
});
