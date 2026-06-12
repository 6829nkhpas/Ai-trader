// Feature: professional-charting-suite
//
// Unit tests for the *drawing interaction paths* (task 6.9). Full DOM pointer
// simulation for `useDrawingInteraction` is heavy and non-deterministic, so we
// test the pure, deterministic store-level behaviours those interaction paths
// drive:
//   - drag-edit geometry: `updateDrawingPoints(id, points)` rewrites an
//     unlocked drawing's points (Req 5.5)
//   - eraser delete: `removeDrawing(id)` deletes the targeted unlocked drawing
//     (Req 10.7)
//   - hover highlight: `setHoveredDrawing(id)` sets `hoveredDrawingId`, a
//     different id changes the hover state, and clearing to null works (Req 10.5)

import { describe, it, expect, beforeEach } from 'vitest';

import { useChartUIStore, type Drawing, type Point } from '@/store/useChartUIStore';

function store() {
  return useChartUIStore.getState();
}

function makeDrawing(overrides: Partial<Drawing> = {}): Drawing {
  return {
    id: 'draw-1',
    tool: 'trendline',
    points: [
      { time: 100, price: 10 },
      { time: 200, price: 20 },
    ],
    ...overrides,
  };
}

beforeEach(() => {
  // Reset every interaction-relevant slice between tests for isolation.
  useChartUIStore.setState({ drawings: [], hoveredDrawingId: null, selectedDrawingId: null });
});

describe('drawing interaction paths', () => {
  describe('drag updates geometry — updateDrawingPoints (Req 5.5)', () => {
    it('rewrites an unlocked drawing\'s points to the dragged geometry', () => {
      store().addDrawing(makeDrawing());

      const dragged: Point[] = [
        { time: 150, price: 15 },
        { time: 250, price: 25 },
      ];
      store().updateDrawingPoints('draw-1', dragged);

      const updated = store().drawings.find((d) => d.id === 'draw-1')!;
      expect(updated.points).toEqual(dragged);
    });

    it('only updates the targeted drawing, leaving siblings untouched', () => {
      store().addDrawing(makeDrawing({ id: 'a' }));
      store().addDrawing(makeDrawing({ id: 'b' }));
      const bBefore = store().drawings.find((d) => d.id === 'b')!.points;

      store().updateDrawingPoints('a', [{ time: 0, price: 0 }]);

      expect(store().drawings.find((d) => d.id === 'a')!.points).toEqual([
        { time: 0, price: 0 },
      ]);
      expect(store().drawings.find((d) => d.id === 'b')!.points).toEqual(bBefore);
    });

    it('does not move a locked drawing (locked drawings are immutable, Req 5.7)', () => {
      const original = makeDrawing({ id: 'locked-1', locked: true });
      store().addDrawing(original);

      store().updateDrawingPoints('locked-1', [{ time: 999, price: 999 }]);

      expect(store().drawings.find((d) => d.id === 'locked-1')!.points).toEqual(
        original.points,
      );
    });
  });

  describe('eraser deletes clicked drawing — removeDrawing (Req 10.7)', () => {
    it('deletes the targeted unlocked drawing', () => {
      store().addDrawing(makeDrawing({ id: 'a' }));
      store().addDrawing(makeDrawing({ id: 'b' }));

      store().removeDrawing('a');

      const ids = store().drawings.map((d) => d.id);
      expect(ids).toEqual(['b']);
    });

    it('clears hover/selection that pointed at the erased drawing', () => {
      store().addDrawing(makeDrawing({ id: 'a' }));
      store().setHoveredDrawing('a');
      store().setSelectedDrawing('a');

      store().removeDrawing('a');

      expect(store().drawings).toHaveLength(0);
      expect(store().hoveredDrawingId).toBeNull();
      expect(store().selectedDrawingId).toBeNull();
    });

    it('does not delete a locked drawing (Req 5.7)', () => {
      store().addDrawing(makeDrawing({ id: 'locked-1', locked: true }));

      store().removeDrawing('locked-1');

      expect(store().drawings.map((d) => d.id)).toEqual(['locked-1']);
    });
  });

  describe('hover state differs — setHoveredDrawing (Req 10.5)', () => {
    it('sets hoveredDrawingId for the drawing under the pointer', () => {
      store().addDrawing(makeDrawing({ id: 'a' }));

      store().setHoveredDrawing('a');

      expect(store().hoveredDrawingId).toBe('a');
    });

    it('changes the hover state when a different drawing is hovered', () => {
      store().addDrawing(makeDrawing({ id: 'a' }));
      store().addDrawing(makeDrawing({ id: 'b' }));

      store().setHoveredDrawing('a');
      expect(store().hoveredDrawingId).toBe('a');

      store().setHoveredDrawing('b');
      expect(store().hoveredDrawingId).toBe('b');
      expect(store().hoveredDrawingId).not.toBe('a');
    });

    it('clears the hover state to null when the pointer leaves all drawings', () => {
      store().addDrawing(makeDrawing({ id: 'a' }));
      store().setHoveredDrawing('a');

      store().setHoveredDrawing(null);

      expect(store().hoveredDrawingId).toBeNull();
    });
  });
});
