/**
 * useFnoSnapshotCache — persists the last good F&O viewState to localStorage.
 *
 * When the market is closed and no live data arrives, this cache provides the
 * historical fallback so the user sees the last known OI/IV/HUD data instead
 * of the "DATA UNAVAILABLE" panel. Keyed by underlying so each index has its
 * own cached snapshot.
 */

import { useRef, useEffect, useCallback } from 'react';
import type { FnoViewState } from './viewModel';

type GoodViewState = FnoViewState & { kind: 'ready' | 'partial' };

const CACHE_KEY_PREFIX = 'fno_snapshot_cache_';

/** Build the localStorage key for a given underlying. */
function cacheKey(underlying: string): string {
  return CACHE_KEY_PREFIX + underlying.replace(/\s+/g, '_').toUpperCase();
}

/** Try to read a cached viewState from localStorage. */
function loadFromCache(underlying: string): GoodViewState | null {
  try {
    const raw = localStorage.getItem(cacheKey(underlying));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as GoodViewState;
    if (parsed && (parsed.kind === 'ready' || parsed.kind === 'partial')) {
      return parsed;
    }
  } catch {
    // Corrupt cache — ignore.
  }
  return null;
}

/** Write a viewState to localStorage. */
function saveToCache(underlying: string, state: GoodViewState): void {
  try {
    localStorage.setItem(cacheKey(underlying), JSON.stringify(state));
  } catch {
    // Storage full or unavailable — ignore.
  }
}

/**
 * Hook that manages a persistent F&O snapshot cache.
 *
 * - On mount, loads the cached snapshot for the current underlying.
 * - Whenever a ready/partial viewState arrives, caches it.
 * - Returns the last good viewState (from memory or localStorage).
 */
export function useFnoSnapshotCache(
  viewState: FnoViewState | null,
  underlying: string,
) {
  const lastGood = useRef<GoodViewState | null>(null);

  // On mount or underlying change, load from localStorage.
  useEffect(() => {
    lastGood.current = loadFromCache(underlying);
  }, [underlying]);

  // Whenever a ready/partial viewState arrives, cache it (memory + disk).
  const cacheIfGood = useCallback(
    (vs: FnoViewState | null) => {
      if (vs && (vs.kind === 'ready' || vs.kind === 'partial')) {
        const good = vs as GoodViewState;
        lastGood.current = good;
        saveToCache(underlying, good);
      }
    },
    [underlying],
  );

  // Auto-cache whenever viewState changes.
  useEffect(() => {
    cacheIfGood(viewState);
  }, [viewState, cacheIfGood]);

  return lastGood;
}
