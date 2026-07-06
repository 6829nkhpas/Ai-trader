// Feature: professional-charting-suite, Property 30
//
// Property-based test for Property 30: "Wheel zoom keeps the visible candle
// count within bounds" (Validates Requirement 10.6).
//
// For any sequence of zoom operations, the resulting number of visible candles
// is always at least 5 and at most 5,000. We exercise the pure clamp helpers
// (`clampVisibleCandleCount` and `clampVisibleRange`) directly: every logical
// range the renderer applies passes through `clampVisibleRange` first, so if
// that function always yields an in-bounds span the visible candle count can
// never escape [5, 5000].
//
// The generators deliberately span the interesting input space: candle counts
// and range spans below 5, above 5000, exactly on the bounds, inverted
// (to < from) ranges, and non-finite edges (NaN, ±Infinity).

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  MIN_VISIBLE_CANDLES,
  MAX_VISIBLE_CANDLES,
  clampVisibleCandleCount,
  clampVisibleRange,
} from '@/charting/zoom';

const RUNS = 100;

// Float tolerance for span comparisons after midpoint reconstruction
// (from = center - span/2, to = center + span/2 reintroduces rounding error).
const EPS = 1e-6;

/**
 * Numbers covering the full clamp input space: values comfortably inside the
 * bound, just below the minimum, just above the maximum, exactly on each
 * bound, plus the non-finite edge cases the helpers must absorb.
 */
const candleCount = () =>
  fc.oneof(
    // Broad finite range that straddles both bounds.
    fc.double({ min: -10_000, max: 20_000, noNaN: true }),
    // Pin the interesting boundary/edge values explicitly.
    fc.constantFrom(
      MIN_VISIBLE_CANDLES,
      MAX_VISIBLE_CANDLES,
      MIN_VISIBLE_CANDLES - 0.0001,
      MAX_VISIBLE_CANDLES + 0.0001,
      0,
      Number.NaN,
      Number.POSITIVE_INFINITY,
      Number.NEGATIVE_INFINITY,
    ),
  );

/** A finite-ish logical-range edge (occasionally non-finite). */
const edge = () =>
  fc.oneof(
    fc.double({ min: -50_000, max: 50_000, noNaN: true }),
    fc.constantFrom(Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY),
  );

describe('Property 30: wheel zoom keeps the visible candle count within bounds', () => {
  it('clampVisibleCandleCount always returns a value within [5, 5000]', () => {
    fc.assert(
      fc.property(candleCount(), (count) => {
        const clamped = clampVisibleCandleCount(count);
        expect(Number.isFinite(clamped)).toBe(true);
        expect(clamped).toBeGreaterThanOrEqual(MIN_VISIBLE_CANDLES);
        expect(clamped).toBeLessThanOrEqual(MAX_VISIBLE_CANDLES);
      }),
      { numRuns: RUNS },
    );
  });

  it('clampVisibleCandleCount preserves in-bounds values exactly', () => {
    fc.assert(
      fc.property(
        fc.double({ min: MIN_VISIBLE_CANDLES, max: MAX_VISIBLE_CANDLES, noNaN: true }),
        (count) => {
          expect(clampVisibleCandleCount(count)).toBe(count);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('clampVisibleRange always yields a span within [5, 5000]', () => {
    fc.assert(
      fc.property(edge(), edge(), (from, to) => {
        const result = clampVisibleRange(from, to);
        const span = result.to - result.from;

        // The result must always be usable: both edges finite and the span
        // within the bound (allowing tiny float drift from reconstruction).
        expect(Number.isFinite(result.from)).toBe(true);
        expect(Number.isFinite(result.to)).toBe(true);
        expect(span).toBeGreaterThanOrEqual(MIN_VISIBLE_CANDLES - EPS);
        expect(span).toBeLessThanOrEqual(MAX_VISIBLE_CANDLES + EPS);
      }),
      { numRuns: RUNS },
    );
  });

  it('clampVisibleRange preserves the center when it has to clamp the span', () => {
    fc.assert(
      fc.property(
        fc.double({ min: -50_000, max: 50_000, noNaN: true }),
        candleCount(),
        (center, rawSpan) => {
          // Build a range with the given (possibly out-of-bounds) span centered
          // on `center`. Only meaningful for finite spans.
          fc.pre(Number.isFinite(rawSpan));
          const half = rawSpan / 2;
          const from = center - half;
          const to = center + half;
          fc.pre(Number.isFinite(from) && Number.isFinite(to));

          const result = clampVisibleRange(from, to);
          const resultCenter = (result.from + result.to) / 2;

          // Whether or not clamping occurred, the midpoint is preserved so the
          // cursor-centered zoom behavior is retained.
          expect(Math.abs(resultCenter - center)).toBeLessThanOrEqual(
            EPS + Math.abs(center) * EPS,
          );
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('clampVisibleRange returns the input unchanged when already in bounds', () => {
    fc.assert(
      fc.property(
        fc.double({ min: -50_000, max: 50_000, noNaN: true }),
        fc.double({ min: MIN_VISIBLE_CANDLES, max: MAX_VISIBLE_CANDLES, noNaN: true }),
        (from, span) => {
          const to = from + span;
          // Guard against float rounding pushing the realized span out of bounds.
          fc.pre(to - from >= MIN_VISIBLE_CANDLES && to - from <= MAX_VISIBLE_CANDLES);

          const result = clampVisibleRange(from, to);
          expect(result.from).toBe(from);
          expect(result.to).toBe(to);
        },
      ),
      { numRuns: RUNS },
    );
  });
});
