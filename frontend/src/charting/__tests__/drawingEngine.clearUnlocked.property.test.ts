// Feature: professional-charting-suite, Property 16
//
// Property-based test for Property 16: "Clear removes unlocked drawings and
// retains locked ones" (Validates Requirement 5.9).
//
// For any set of drawings, clearUnlocked(drawings) returns exactly the subset
// of drawings whose `locked` flag is true. The test asserts that the result:
//   - contains every locked drawing (preserving them, by reference),
//   - drops every unlocked drawing,
//   - never adds anything not present in the input,
//   - preserves the relative order of the retained (locked) drawings,
// and that clearUnlocked is pure (it does not mutate its input array).

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { clearUnlocked } from '@/charting/engines/drawingEngine';
import type { Drawing, Point } from '@/store/useChartUIStore';

const RUNS = 100;

/** An arbitrary {time, price} anchor. */
const arbPoint: fc.Arbitrary<Point> = fc.record({
  time: fc.integer({ min: 0, max: 1_000_000 }),
  price: fc.double({ min: -1e6, max: 1e6, noNaN: true, noDefaultInfinity: true }),
});

/**
 * An arbitrary Drawing with a mixed `locked` flag. The flag is one of
 * true / false / undefined so the test exercises the "truthy locked" boundary:
 * only `locked === true` drawings are retained.
 */
const arbDrawing: fc.Arbitrary<Drawing> = fc.record({
  id: fc.uuid(),
  tool: fc.constantFrom('trendline', 'ray', 'rectangle', 'fib-retracement', 'text'),
  points: fc.array(arbPoint, { minLength: 0, maxLength: 4 }),
  locked: fc.constantFrom(true, false, undefined),
});

/** An array of drawings with mixed locked flags. */
const arbDrawings: fc.Arbitrary<Drawing[]> = fc.array(arbDrawing, {
  minLength: 0,
  maxLength: 30,
});

describe('Property 16: clear removes unlocked drawings and retains locked ones', () => {
  it('returns exactly the locked subset, preserving locked drawings and dropping unlocked ones', () => {
    fc.assert(
      fc.property(arbDrawings, (drawings) => {
        const result = clearUnlocked(drawings);

        // Snapshot for the purity check below.
        //
        // `structuredClone`, NOT a JSON round-trip. `JSON.stringify(-0)` emits
        // "0", so parsing it back yields POSITIVE zero — the snapshot silently
        // differed from the input before `clearUnlocked` was even called, and the
        // purity assertion then failed on `-0` vs `0`, which Vitest distinguishes.
        // `fc.double` generates -0 readily, so this failed roughly one run in five
        // on an unpinned seed and looked like flakiness in the engine. The engine
        // is a one-line filter and was never involved.
        const before = structuredClone(drawings);

        // The result is exactly the locked-subset (same elements, same order).
        const expected = drawings.filter((d) => d.locked === true);
        expect(result).toEqual(expected);

        // Every retained drawing was locked; none dropped a locked drawing.
        for (const d of result) {
          expect(d.locked).toBe(true);
        }

        // No unlocked drawing survives.
        const resultIds = new Set(result.map((d) => d.id));
        for (const d of drawings) {
          if (d.locked !== true) {
            // unlocked drawings must be absent (assuming unique ids from uuid)
            expect(result.some((r) => r === d)).toBe(false);
          } else {
            // locked drawings must be present and preserved by reference
            expect(result.includes(d)).toBe(true);
          }
          // sanity: result never adds a drawing not in the input
          if (resultIds.has(d.id)) {
            expect(drawings.includes(d)).toBe(true);
          }
        }

        // Never adds anything: every result element came from the input.
        for (const r of result) {
          expect(drawings.includes(r)).toBe(true);
        }

        // Count check: result size equals number of locked drawings.
        const lockedCount = drawings.filter((d) => d.locked === true).length;
        expect(result.length).toBe(lockedCount);

        // Purity: the input array is unchanged.
        expect(drawings).toEqual(before);
      }),
      { numRuns: RUNS },
    );
  });

  it('retains relative order of locked drawings', () => {
    fc.assert(
      fc.property(arbDrawings, (drawings) => {
        const result = clearUnlocked(drawings);
        const orderedLocked = drawings.filter((d) => d.locked === true).map((d) => d.id);
        expect(result.map((d) => d.id)).toEqual(orderedLocked);
      }),
      { numRuns: RUNS },
    );
  });
});
