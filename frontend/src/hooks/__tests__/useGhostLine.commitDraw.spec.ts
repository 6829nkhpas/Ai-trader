// Feature: ghost-line-forward-projection
//
// Unit tests for the pure `commitDraw` double-buffer helper exported from
// `useGhostLine`. The helper decides the next `entityIdsRef` value and which
// ids to remove from the TradingView chart immediately after a draw, without
// ever leaving a frame where zero lines are on the chart. Testing this logic
// directly keeps us off the heavy `widget`/`renderHook` mock path while still
// pinning the id-lifecycle invariants that eliminate the clear-then-draw
// flicker.
//
// Invariants:
//   · On a successful draw, the previous ids are slated for removal and the
//     newly-drawn ids become the current `entityIdsRef` value.
//   · On a stale-after-draw (a newer run owns the chart), the newly-drawn ids
//     are slated for removal and the previous ids remain current.
//   · `removeNow` and `next` together never select the same id (an id is
//     either kept or removed, not both), so nothing is double-owned.

import { describe, it, expect } from 'vitest';
import { commitDraw } from '../useGhostLine';

describe('commitDraw (double-buffer draw commit)', () => {
  it('on a successful draw, slates prevIds for removal and makes newIds current', () => {
    const prevIds = ['old-1', 'old-2'];
    const newIds = ['new-1', 'new-2', 'new-3'];
    const { next, removeNow } = commitDraw(prevIds, newIds, false);

    expect(next).toEqual(newIds);
    expect(removeNow).toEqual(prevIds);
  });

  it('on a stale-after-draw, slates newIds for removal and keeps prevIds current', () => {
    const prevIds = ['old-1', 'old-2'];
    const newIds = ['new-1', 'new-2'];
    const { next, removeNow } = commitDraw(prevIds, newIds, true);

    expect(next).toEqual(prevIds);
    expect(removeNow).toEqual(newIds);
  });

  it('on a stale-after-draw that aborted mid-flight (newIds empty), removes nothing and keeps prevIds', () => {
    // drawGhostSegments returns [] when it self-aborts; commitDraw must not
    // ask the caller to remove a phantom empty set, and must hand ownership
    // back to the prior line.
    const prevIds = ['old-1', 'old-2'];
    const { next, removeNow } = commitDraw(prevIds, [], true);

    expect(next).toEqual(prevIds);
    expect(removeNow).toEqual([]);
  });

  it('mirrors the production contract: failed removeNow ids fold back into next', () => {
    // This pins the self-healing agreement between commitDraw and the hook:
    // the helper picks (next, removeNow); the hook removes removeNow and
    // folds any that failed back into next. Simulate the fold the hook does.
    const prevIds = ['old-1', 'old-2'];
    const newIds = ['new-1', 'new-2'];
    const { next, removeNow } = commitDraw(prevIds, newIds, false);

    // Suppose 'old-1' fails to remove. The hook sets
    // entityIdsRef.current = [...next, ...failed] where failed = ['old-1'].
    const failedToRemove = ['old-1']; // subset of removeNow
    const refAfter = [...next, ...failedToRemove];

    // The new line is retained...
    expect(refAfter).toEqual(expect.arrayContaining(newIds));
    // ...and the failed-to-remove prev id stays tracked for retry...
    expect(refAfter).toContain('old-1');
    // ...without being double-counted by the helper's own `next`.
    expect(next).not.toContain('old-1');
  });

  it('a successful draw with no prior line makes the new line current and removes nothing', () => {
    const { next, removeNow } = commitDraw([], ['new-1', 'new-2'], false);

    expect(next).toEqual(['new-1', 'new-2']);
    expect(removeNow).toEqual([]);
  });

  it('a stale draw that produced no new ids keeps the prior line untouched', () => {
    const prevIds = ['old-1'];
    const { next, removeNow } = commitDraw(prevIds, [], true);

    expect(next).toEqual(prevIds);
    expect(removeNow).toEqual([]);
  });

  it('never selects the same id for both removal and retention', () => {
    // Repeated random-ish draws: the removed set and the kept set must be
    // disjoint regardless of input, otherwise we'd both keep and remove an id.
    for (let i = 0; i < 50; i++) {
      const prevIds = Array.from({ length: 3 }, (_, k) => `prev-${i}-${k}`);
      const newIds = Array.from({ length: 3 }, (_, k) => `new-${i}-${k}`);
      for (const stale of [false, true]) {
        const { next, removeNow } = commitDraw(prevIds, newIds, stale);
        const removeSet = new Set(removeNow);
        const nextSet = new Set(next);
        for (const id of removeSet) expect(nextSet.has(id)).toBe(false);
        for (const id of nextSet) expect(removeSet.has(id)).toBe(false);
      }
    }
  });

  it('does not mutate the input arrays', () => {
    const prevIds = ['old-1', 'old-2'];
    const newIds = ['new-1'];
    const prevCopy = [...prevIds];
    const newCopy = [...newIds];

    commitDraw(prevIds, newIds, false);
    expect(prevIds).toEqual(prevCopy);
    expect(newIds).toEqual(newCopy);

    commitDraw(prevIds, newIds, true);
    expect(prevIds).toEqual(prevCopy);
    expect(newIds).toEqual(newCopy);
  });
});
