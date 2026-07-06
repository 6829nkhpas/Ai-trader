// Feature: professional-charting-suite, Property 8
//
// Property-based test for Property 8: "Pane removal redistributes height with
// no gap" (Validates Requirement 3.6).
//
// For any set of pane height fractions, removing one pane and redistributing
// with the pure `redistribute(layouts, removed)` helper produces a layout that:
//   - sums to exactly 1.0 (the full available height) with no unallocated gap,
//   - no longer contains the removed pane,
//   - renumbers `order` to a contiguous 0..n-1 range while preserving the
//     surviving panes' relative top-to-bottom order,
//   - never throws and never mutates its input.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { redistribute, type PaneLayout } from '@/charting/paneManager';

const RUNS = 100;

// Floating-point tolerance for the "sums to 1.0" invariant. The helper forces
// the last pane to absorb residual so the sum is exact in exact arithmetic;
// epsilon guards only against the comparison itself accumulating error.
const EPSILON = 1e-9;

/**
 * A layout of distinct panes with arbitrary (possibly unnormalized) height
 * fractions and arbitrary `order` values. paneIds are unique within a layout;
 * `order` values are shuffled so the helper's ordering logic is exercised.
 */
const layoutArb = (): fc.Arbitrary<PaneLayout[]> =>
  fc
    .integer({ min: 0, max: 8 })
    .chain((n) =>
      fc.record({
        // Arbitrary, possibly-unnormalized, possibly-zero height fractions.
        heights: fc.array(
          fc.double({ min: 0, max: 1000, noNaN: true, noDefaultInfinity: true }),
          { minLength: n, maxLength: n },
        ),
        // A permutation of 0..n-1 for the `order` field.
        order: fc
          .constant(Array.from({ length: n }, (_, i) => i))
          .chain((base) => fc.shuffledSubarray(base, { minLength: n, maxLength: n })),
      }),
    )
    .map(({ heights, order }) =>
      heights.map((h, i) => ({
        paneId: `pane-${i}`,
        heightFraction: h,
        order: order[i],
      })),
    );

describe('Property 8: pane removal redistributes height with no gap', () => {
  it('removing an existing pane yields fractions summing to exactly 1.0 with no gap', () => {
    fc.assert(
      fc.property(
        layoutArb().filter((ls) => ls.length > 0),
        fc.nat(),
        (layouts, pick) => {
          const removed = layouts[pick % layouts.length].paneId;

          const result = redistribute(layouts, removed);

          // One fewer pane than we started with.
          expect(result.length).toBe(layouts.length - 1);

          // The removed pane is gone.
          expect(result.some((l) => l.paneId === removed)).toBe(false);

          if (result.length > 0) {
            // Fractions sum to exactly 1.0 (no unallocated gap).
            const sum = result.reduce((s, l) => s + l.heightFraction, 0);
            expect(Math.abs(sum - 1)).toBeLessThanOrEqual(EPSILON);

            // order is contiguous 0..n-1.
            const orders = result.map((l) => l.order);
            expect(orders).toEqual(orders.map((_, i) => i));
          }
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('preserves the surviving panes relative top-to-bottom order', () => {
    fc.assert(
      fc.property(
        layoutArb().filter((ls) => ls.length > 0),
        fc.nat(),
        (layouts, pick) => {
          const removed = layouts[pick % layouts.length].paneId;

          const result = redistribute(layouts, removed);

          // Expected surviving order: original panes minus the removed one,
          // sorted by their original `order` field.
          const expectedIds = [...layouts]
            .filter((l) => l.paneId !== removed)
            .sort((a, b) => a.order - b.order)
            .map((l) => l.paneId);

          expect(result.map((l) => l.paneId)).toEqual(expectedIds);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('renormalizes to 1.0 when removing a pane absent from the layout', () => {
    fc.assert(
      fc.property(
        layoutArb().filter((ls) => ls.length > 0),
        (layouts) => {
          const result = redistribute(layouts, 'not-a-real-pane');

          // No pane removed, so the count is unchanged.
          expect(result.length).toBe(layouts.length);

          const sum = result.reduce((s, l) => s + l.heightFraction, 0);
          expect(Math.abs(sum - 1)).toBeLessThanOrEqual(EPSILON);

          const orders = result.map((l) => l.order);
          expect(orders).toEqual(orders.map((_, i) => i));
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('returns an empty layout when the last remaining pane is removed', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 1000, noNaN: true, noDefaultInfinity: true }),
        (height) => {
          const sole: PaneLayout[] = [{ paneId: 'only', heightFraction: height, order: 0 }];
          expect(redistribute(sole, 'only')).toEqual([]);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('is pure: it does not mutate its input layout', () => {
    fc.assert(
      fc.property(
        layoutArb().filter((ls) => ls.length > 0),
        fc.nat(),
        (layouts, pick) => {
          const removed = layouts[pick % layouts.length].paneId;
          const snapshot = layouts.map((l) => ({ ...l }));

          redistribute(layouts, removed);

          expect(layouts).toEqual(snapshot);
        },
      ),
      { numRuns: RUNS },
    );
  });
});
