// Feature: professional-charting-suite, Property 15
//
// Property 15: Locked drawings are immutable.
//
// "For any locked drawing, any attempt to modify its geometry or delete it
//  leaves the drawing and its geometry unchanged."
//
// Validates: Requirements 5.7
//
// We seed the store with an arbitrary set of drawings that mix locked and
// unlocked entries (each with a unique id) and exercise the two mutating
// actions that must respect the lock:
//   - updateDrawingPoints(id, points): a no-op on a locked drawing; its points
//     stay byte-for-byte unchanged. On an unlocked drawing it replaces points.
//   - removeDrawing(id): a no-op on a locked drawing; it stays present. On an
//     unlocked drawing it is removed.
// The store is reset before every run.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import { useChartUIStore, type Drawing, type Point } from '@/store/useChartUIStore';

function store() {
  return useChartUIStore.getState();
}

function resetState() {
  useChartUIStore.setState({
    drawings: [],
    selectedDrawingId: null,
    hoveredDrawingId: null,
  });
}

beforeEach(() => {
  resetState();
});

/** Deep clone for capturing an immutable "before" snapshot. */
function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

/** Arbitrary anchor point. */
function pointArb(): fc.Arbitrary<Point> {
  return fc.record({
    time: fc.integer({ min: 0, max: 2_000_000_000 }),
    // Normalize signed zero (-0) to +0: the store/JSON-clone path collapses
    // -0 to +0, and the lock invariant is about geometry, not the sign of zero.
    price: fc
      .double({ min: -1e6, max: 1e6, noNaN: true, noDefaultInfinity: true })
      .map((v) => (Object.is(v, -0) ? 0 : v)),
  });
}

/** A list of anchor points (at least one). */
function pointsArb(): fc.Arbitrary<Point[]> {
  return fc.array(pointArb(), { minLength: 1, maxLength: 5 });
}

/** Arbitrary drawing with a caller-supplied unique id. */
function drawingArb(id: string): fc.Arbitrary<Drawing> {
  return fc.record({
    tool: fc.constantFrom('trendline', 'ray', 'rect', 'fib', 'text'),
    points: pointsArb(),
    color: fc.constantFrom('#FF5722', '#2962FF', '#00C853'),
    locked: fc.boolean(),
  }).map((rec): Drawing => ({ id, ...rec }));
}

/** A non-empty set of drawings with unique ids, plus a target index. */
function drawingsAndTargetArb() {
  return fc.integer({ min: 1, max: 8 }).chain((n) =>
    fc.record({
      drawings: fc.tuple(
        ...Array.from({ length: n }, (_, i) => drawingArb(`draw-${i}`)),
      ),
      targetIndex: fc.integer({ min: 0, max: n - 1 }),
      newPoints: pointsArb(),
    }),
  );
}

describe('Property 15: locked drawings are immutable', () => {
  it('updateDrawingPoints leaves a locked drawing geometry unchanged but edits unlocked drawings', () => {
    fc.assert(
      fc.property(drawingsAndTargetArb(), ({ drawings, targetIndex, newPoints }) => {
        resetState();
        const seeded = clone(drawings as Drawing[]);
        useChartUIStore.setState({ drawings: clone(seeded) });

        const before = clone(store().drawings);
        const target = before[targetIndex];

        store().updateDrawingPoints(target.id, clone(newPoints));

        const after = store().drawings;
        const updated = after.find((d) => d.id === target.id)!;

        if (target.locked) {
          // Locked: geometry (and the whole drawing) is untouched.
          expect(updated).toEqual(target);
        } else {
          // Unlocked: points are replaced, everything else preserved.
          expect(updated.points).toEqual(newPoints);
          expect(updated.id).toBe(target.id);
          expect(updated.tool).toBe(target.tool);
          expect(updated.locked).toBe(target.locked);
        }

        // Every other drawing is left byte-for-byte unchanged.
        after.forEach((d) => {
          if (d.id !== target.id) {
            expect(d).toEqual(before.find((b) => b.id === d.id));
          }
        });
      }),
      { numRuns: 100 },
    );
  });

  it('removeDrawing keeps a locked drawing present but removes unlocked drawings', () => {
    fc.assert(
      fc.property(drawingsAndTargetArb(), ({ drawings, targetIndex }) => {
        resetState();
        const seeded = clone(drawings as Drawing[]);
        useChartUIStore.setState({ drawings: clone(seeded) });

        const before = clone(store().drawings);
        const target = before[targetIndex];

        store().removeDrawing(target.id);

        const after = store().drawings;
        const survivor = after.find((d) => d.id === target.id);

        if (target.locked) {
          // Locked: still present and unchanged.
          expect(survivor).toBeDefined();
          expect(survivor).toEqual(target);
          expect(after.length).toBe(before.length);
        } else {
          // Unlocked: removed.
          expect(survivor).toBeUndefined();
          expect(after.length).toBe(before.length - 1);
        }

        // All untargeted drawings remain unchanged.
        after.forEach((d) => {
          if (d.id !== target.id) {
            expect(d).toEqual(before.find((b) => b.id === d.id));
          }
        });
      }),
      { numRuns: 100 },
    );
  });
});
