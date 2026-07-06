// Feature: professional-charting-suite, Property 12
//
// Property-based test for Property 12: "Drawing creation requires exactly the
// tool's anchor count" (Validates Requirements 5.2, 5.3).
//
// For any drawing tool in TOOL_REGISTRY and any array of placed anchors,
// isComplete(tool, anchors) is true if and only if the number of anchors meets
// the tool's required count:
//   - a fixed tool requires exactly its `anchorCount` anchors,
//   - a 'multi' tool requires at least MULTI_MIN_ANCHORS anchors.
// Placing fewer anchors than required is a cancellation and must produce no
// drawing (isComplete === false).

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  TOOL_REGISTRY,
  MULTI_MIN_ANCHORS,
  isComplete,
} from '@/charting/engines/drawingEngine';
import type { Point } from '@/store/useChartUIStore';

const RUNS = 100;

/** The required anchor count for a tool, resolving 'multi' to MULTI_MIN_ANCHORS. */
function expectedRequired(anchorCount: number | 'multi'): number {
  return anchorCount === 'multi' ? MULTI_MIN_ANCHORS : anchorCount;
}

/** An arbitrary {time, price} anchor. */
const arbPoint: fc.Arbitrary<Point> = fc.record({
  time: fc.integer({ min: 0, max: 1_000_000 }),
  price: fc.double({ min: -1e6, max: 1e6, noNaN: true, noDefaultInfinity: true }),
});

const TOOLS = Object.keys(TOOL_REGISTRY);

describe('Property 12: drawing creation requires exactly the tool\'s anchor count', () => {
  it('isComplete is true iff anchors.length >= the tool\'s required anchor count', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...TOOLS),
        // Span anchor counts from empty up to comfortably past the max requirement.
        fc.array(arbPoint, { minLength: 0, maxLength: MULTI_MIN_ANCHORS + 5 }),
        (tool, anchors) => {
          const required = expectedRequired(TOOL_REGISTRY[tool].anchorCount);
          const complete = isComplete(tool, anchors);

          // Complete iff enough anchors were placed.
          expect(complete).toBe(anchors.length >= required);

          // Restated for clarity: fewer anchors => no drawing (cancellation).
          if (anchors.length < required) {
            expect(complete).toBe(false);
          } else {
            expect(complete).toBe(true);
          }
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('every tool becomes complete exactly at its required anchor count and not one fewer', () => {
    for (const tool of TOOLS) {
      const required = expectedRequired(TOOL_REGISTRY[tool].anchorCount);
      const make = (n: number): Point[] =>
        Array.from({ length: n }, (_, i) => ({ time: i, price: i }));

      // One fewer than required is incomplete (when required >= 1).
      if (required >= 1) {
        expect(isComplete(tool, make(required - 1)), `${tool} should be incomplete with ${required - 1} anchors`).toBe(false);
      }
      // Exactly the required count completes the drawing.
      expect(isComplete(tool, make(required)), `${tool} should be complete with ${required} anchors`).toBe(true);
    }
  });
});
