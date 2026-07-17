// Feature: useGhostLine — bound failed-remove id accumulation
//
// Unit tests for the pure `pruneFailedIds` helper exported from useGhostLine.
// This helper bounds how long a failed-to-remove entity id is retried, so ids
// that belong to a torn-down (and recreated) widget cannot accumulate in the
// hook's ref forever. See useGhostLine.ts for the full rationale.
//
// Semantics under test (maxAttempts = 2):
//   - fails once        → retained for one more retry (attempt count 1)
//   - fails maxAttempts → dropped (attempt count reaches the bound)
//   - a successful remove resets that id's consecutive-failure counter

import { describe, it, expect } from 'vitest';

import { pruneFailedIds } from '@/hooks/useGhostLine';

describe('pruneFailedIds', () => {
  it('retains an id that fails only once', () => {
    const attempts = new Map<string, number>();
    const retry = pruneFailedIds(['id_a'], attempts, 2);

    expect(retry).toEqual(['id_a']);
    expect(attempts.get('id_a')).toBe(1);
  });

  it('drops an id once it reaches maxAttempts consecutive failures', () => {
    const attempts = new Map<string, number>();
    // First failure — retained (attempt 1, below the bound).
    let retry = pruneFailedIds(['id_a'], attempts, 2);
    expect(retry).toEqual(['id_a']);
    expect(attempts.get('id_a')).toBe(1);

    // Second consecutive failure — attempt reaches maxAttempts=2 → dropped.
    retry = pruneFailedIds(['id_a'], attempts, 2);
    expect(retry).toEqual([]);
    // Counter is removed so the map cannot grow across runs for dead ids.
    expect(attempts.has('id_a')).toBe(false);
  });

  it('drops a previously-failing id whose next failure reaches the bound, and clears counters for ids that removed cleanly', () => {
    const attempts = new Map<string, number>();
    attempts.set('id_a', 1); // one prior failure
    attempts.set('id_b', 1); // one prior failure

    const tracked = ['id_a', 'id_b'];
    // id_a removes cleanly this pass; id_b fails again (next=2 >= 2 → drop).
    const retry = pruneFailedIds(['id_b'], attempts, 2, tracked);

    expect(retry).toEqual([]);
    expect(attempts.has('id_a')).toBe(false); // successful remove resets counter
    expect(attempts.has('id_b')).toBe(false); // dropped after reaching the bound
  });

  it('clears the attempt counter for a healthy id even when no full tracked set is provided', () => {
    const attempts = new Map<string, number>();
    attempts.set('id_a', 1); // prior failure (one below the bound)
    attempts.set('id_b', 1); // prior failure

    // Without the tracked set, the helper still drops counters for ids that
    // are no longer in `failed` (they removed cleanly). Here only id_a fails,
    // and its next attempt (2) reaches the bound so it is dropped — proving
    // id_b's counter was cleared independently of id_a's outcome.
    const retry = pruneFailedIds(['id_a'], attempts, 2);

    expect(retry).toEqual([]); // id_a reaches the bound on this pass
    expect(attempts.has('id_a')).toBe(false); // dropped at the bound
    expect(attempts.has('id_b')).toBe(false); // reset (it removed cleanly)
  });

  it('handles an empty failed list by clearing stale counters', () => {
    const attempts = new Map<string, number>();
    attempts.set('id_a', 1);
    attempts.set('id_b', 1);

    const retry = pruneFailedIds([], attempts, 2, ['id_a', 'id_b']);

    expect(retry).toEqual([]);
    expect(attempts.size).toBe(0); // everything removed cleanly → all reset
  });

  it('a successful remove resets the consecutive-failure counter to zero', () => {
    const attempts = new Map<string, number>();
    attempts.set('id_a', 1); // one failure

    // id_a removes cleanly this pass (not in failed). The counter resets.
    pruneFailedIds([], attempts, 2, ['id_a']);
    expect(attempts.has('id_a')).toBe(false);

    // A subsequent failure starts fresh from 1, not from 2.
    const retry = pruneFailedIds(['id_a'], attempts, 2, ['id_a']);
    expect(retry).toEqual(['id_a']);
    expect(attempts.get('id_a')).toBe(1);
  });

  it('does not carry prior-generation failed ids forward unconditionally', () => {
    // Simulate redraws where id_dead always fails (dead widget) and id_ok
    // fails once then succeeds. id_dead must be dropped after reaching the
    // bound; id_ok must have its counter cleared once it succeeds.
    const attempts = new Map<string, number>();

    // Pass 1: both fail (attempt 1 each — below the bound, retained).
    let retry = pruneFailedIds(['id_dead', 'id_ok'], attempts, 2, ['id_dead', 'id_ok']);
    expect(retry.sort()).toEqual(['id_dead', 'id_ok']);

    // Pass 2: id_dead fails again (next=2 >= 2 → dropped); id_ok succeeds.
    retry = pruneFailedIds(['id_dead'], attempts, 2, ['id_dead', 'id_ok']);
    expect(retry).toEqual([]);
    expect(attempts.has('id_dead')).toBe(false); // dropped after 2 failures
    expect(attempts.has('id_ok')).toBe(false); // successful remove reset it
  });
});
