// Feature: professional-charting-suite, Property 17
//
// Property-based test for Property 17: "Fibonacci retracement levels match the
// canonical ratios" (Validates Requirement 5.10).
//
// For any two price anchors p1 and p2, fibLevels returns exactly one entry per
// canonical ratio, in canonical order, where each entry's price equals
// p1 + (p2 - p1) * ratio. In particular ratio 0 maps to p1 and ratio 1.0 maps
// to p2.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { fibLevels, FIB_RATIOS } from '@/charting/engines';

const RUNS = 100;

/** A finite price value generator. */
const price = () =>
  fc.double({ min: -100_000, max: 100_000, noNaN: true, noDefaultInfinity: true });

/**
 * Tolerance for floating-point comparison. Prices span up to ~1e5 and ratios
 * are < 1, so a small relative tolerance scaled by the magnitudes involved
 * comfortably absorbs IEEE-754 rounding from the multiply-add.
 */
function tolerance(p1: number, p2: number): number {
  const scale = Math.max(1, Math.abs(p1), Math.abs(p2));
  return scale * 1e-9;
}

describe('Property 17: Fibonacci retracement levels match the canonical ratios', () => {
  it('returns one entry per canonical ratio, in order, at the interpolated price', () => {
    fc.assert(
      fc.property(price(), price(), (p1, p2) => {
        const levels = fibLevels(p1, p2);

        // One entry per canonical ratio, in canonical order.
        expect(levels).toHaveLength(FIB_RATIOS.length);
        levels.forEach((level, i) => {
          expect(level.ratio).toBe(FIB_RATIOS[i]);
        });

        // Each price equals p1 + (p2 - p1) * ratio.
        const tol = tolerance(p1, p2);
        for (const { ratio, price: levelPrice } of levels) {
          const expected = p1 + (p2 - p1) * ratio;
          expect(Math.abs(levelPrice - expected)).toBeLessThanOrEqual(tol);
        }
      }),
      { numRuns: RUNS },
    );
  });

  it('maps ratio 0 to p1 and ratio 1.0 to p2', () => {
    fc.assert(
      fc.property(price(), price(), (p1, p2) => {
        const levels = fibLevels(p1, p2);

        const zero = levels.find((l) => l.ratio === 0);
        const one = levels.find((l) => l.ratio === 1.0);
        expect(zero).toBeDefined();
        expect(one).toBeDefined();

        // ratio 0: p1 + (p2 - p1) * 0 collapses to p1. Use loose (===)
        // equality so the signed-zero case (-0 vs +0, which Object.is/`toBe`
        // distinguish) is treated as equal.
        expect(zero!.price === p1).toBe(true);
        // ratio 1.0: p1 + (p2 - p1) * 1.0 equals p2 mathematically, but the
        // multiply-add can round near subnormals, so compare within tolerance.
        expect(Math.abs(one!.price - p2)).toBeLessThanOrEqual(tolerance(p1, p2));
      }),
      { numRuns: RUNS },
    );
  });
});
