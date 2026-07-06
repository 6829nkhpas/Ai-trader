// Feature: professional-charting-suite, Property 25
//
// Property-based test for Property 25: "Invalid fixed range is rejected and the
// prior profile is retained" (Validates Requirement 7.10).
//
// For any fixed-range selection whose end anchor is at or before the start
// anchor (end <= start) — or whose anchors are non-finite — the engine rejects
// the range and returns the previously supplied profile unchanged. When no
// previous profile is supplied, an empty profile (with the configured row
// count) is returned instead. A *valid* fixed range (end > start) instead
// produces a freshly computed profile over the inclusive [start, end] span.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { buildProfile } from '@/charting/engines';
import type { VolumeProfile } from '@/charting/engines';
import type { ChartCandle, VolumeBar } from '@/charting/types';

const RUNS = 100;

/** Bounded finite price generator. */
const price = () =>
  fc.double({ min: 0.0001, max: 5_000, noNaN: true, noDefaultInfinity: true });

/** Bounded finite, non-negative volume generator. */
const volumeValue = () =>
  fc.double({ min: 0, max: 10_000, noNaN: true, noDefaultInfinity: true });

/**
 * Well-formed candle series with strictly ascending unique timestamps; high/low
 * bracket open/close. Returns the candles and a matching set of volume bars.
 */
const candlesWithVolumes = (): fc.Arbitrary<{
  candles: ChartCandle[];
  volumes: VolumeBar[];
}> =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 1_000_000 }), { minLength: 1, maxLength: 20 })
    .chain((times) => {
      const sorted = [...times].sort((x, y) => x - y);
      return fc.tuple(
        ...sorted.map((t) =>
          fc
            .record({ a: price(), b: price(), c: price(), d: price(), v: volumeValue() })
            .map(({ a, b, c, d, v }) => ({
              candle: {
                time: t,
                open: a,
                close: b,
                high: Math.max(a, b, c, d),
                low: Math.min(a, b, c, d),
              } as ChartCandle,
              volume: { time: t, value: v, color: '#000' } as VolumeBar,
            })),
        ),
      );
    })
    .map((pairs) => ({
      candles: pairs.map((p) => p.candle),
      volumes: pairs.map((p) => p.volume),
    }));

/**
 * An arbitrary "previous profile" reference. Its concrete contents don't matter
 * for the invalid-range case — the property is that the *exact same object* is
 * returned — so we use a recognizable sentinel object.
 */
const previousProfile = (): fc.Arbitrary<VolumeProfile> =>
  fc.record({
    poc: fc.option(price(), { nil: null }),
    vah: fc.option(price(), { nil: null }),
    val: fc.option(price(), { nil: null }),
    totalVolume: volumeValue(),
  }).map(({ poc, vah, val, totalVolume }) => ({
    rows: [],
    poc,
    vah,
    val,
    totalVolume,
  }));

describe('Property 25: Invalid fixed range is rejected and the prior profile is retained', () => {
  it('returns the exact previousProfile reference unchanged for an invalid fixed range (end <= start)', () => {
    fc.assert(
      fc.property(
        candlesWithVolumes(),
        previousProfile(),
        // start anchor and a non-negative delta so end = start - delta <= start.
        fc.integer({ min: -1_000_000, max: 1_000_000 }),
        fc.integer({ min: 0, max: 1_000_000 }),
        ({ candles, volumes }, prev, start, delta) => {
          const end = start - delta; // end <= start → invalid range

          const result = buildProfile(candles, volumes, {
            range: { kind: 'fixed', start, end },
            previousProfile: prev,
          });

          // The very same object is handed back, untouched (Req 7.10).
          expect(result).toBe(prev);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('returns an empty profile (with the configured row count) when no previousProfile is supplied for an invalid range', () => {
    fc.assert(
      fc.property(
        candlesWithVolumes(),
        fc.integer({ min: -1_000_000, max: 1_000_000 }),
        fc.integer({ min: 0, max: 1_000_000 }),
        fc.integer({ min: 1, max: 64 }),
        ({ candles, volumes }, start, delta, rows) => {
          const end = start - delta; // end <= start → invalid range

          const result = buildProfile(candles, volumes, {
            range: { kind: 'fixed', start, end },
            rows,
            // previousProfile intentionally omitted
          });

          expect(result.rows).toHaveLength(rows);
          expect(result.poc).toBeNull();
          expect(result.vah).toBeNull();
          expect(result.val).toBeNull();
          expect(result.totalVolume).toBe(0);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('rejects non-finite fixed-range anchors and retains the previousProfile', () => {
    fc.assert(
      fc.property(
        candlesWithVolumes(),
        previousProfile(),
        fc.constantFrom(NaN, Infinity, -Infinity),
        fc.constantFrom(NaN, Infinity, -Infinity),
        ({ candles, volumes }, prev, start, end) => {
          const result = buildProfile(candles, volumes, {
            range: { kind: 'fixed', start, end },
            previousProfile: prev,
          });

          expect(result).toBe(prev);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('a valid fixed range (end > start) computes a fresh profile over the inclusive [start, end] span', () => {
    fc.assert(
      fc.property(
        candlesWithVolumes(),
        previousProfile(),
        fc.integer({ min: 1, max: 64 }),
        ({ candles, volumes }, prev, rows) => {
          // Use the candle time extent as the span so the range is valid and
          // covers every candle (times are unique & ascending).
          const start = candles[0].time;
          const end = candles[candles.length - 1].time + 1; // strictly > start

          const result = buildProfile(candles, volumes, {
            range: { kind: 'fixed', start, end },
            rows,
            previousProfile: prev,
          });

          // A valid range never returns the prior profile by reference.
          if (candles.length > 0) {
            expect(result).not.toBe(prev);
          }
          // Row-count invariant is preserved for a freshly computed profile.
          expect(result.rows).toHaveLength(rows);

          // Total volume equals the sum over candles inside the inclusive span,
          // which (by construction) is every candle.
          const expectedTotal = volumes.reduce((s, v) => s + v.value, 0);
          // Allow tiny floating-point drift from per-row distribution.
          expect(result.totalVolume).toBeCloseTo(expectedTotal, 6);
        },
      ),
      { numRuns: RUNS },
    );
  });
});
