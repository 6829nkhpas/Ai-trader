// Feature: professional-charting-suite
//
// Unit tests for workspace persist/restore failure handling
// (Validates Requirements 5.12, 11.4, 11.5).
//
// These tests exercise the in-memory fallback path that runs outside the Tauri
// runtime (the lazy `@tauri-apps/api/core` bridge is absent in the node test
// environment, so `getInvoke()` resolves to null). In that mode:
//
//  - loadWorkspace(symbol) for an unsaved symbol resolves to DEFAULT_WORKSPACE
//    — a restore "miss"/failure applies the defaults (Requirement 11.4).
//  - flushWorkspace(symbol, state) returns false (no persistent backend) but
//    always records the latest state in the in-memory store first, so the
//    workspace is retained for the session and a subsequent loadWorkspace
//    returns it (persist-failure retains in-memory state, Requirement 11.5;
//    drawing-set retention, Requirement 5.12).
//  - Saving a newer state after a prior persist updates the retained state,
//    modelling retry-on-next-change semantics (Requirement 11.5).
//  - resetWorkspacePersistence(symbol?) clears retained state for isolation.

import { afterEach, describe, expect, it, vi } from 'vitest';

// Simulate running OUTSIDE the Tauri runtime: the lazy `@tauri-apps/api/core`
// bridge is unavailable, so `getInvoke()` resolves to a falsy value and the
// persistence layer uses its in-memory session fallback (Requirement 11.6).
// This mocks only the IPC boundary — the persistence logic under test (memory
// retention, flush-failure reporting, retry-on-next-change) is the real code.
vi.mock('@tauri-apps/api/core', () => ({ invoke: undefined }));

import {
  DEFAULT_WORKSPACE,
  SAVE_DEBOUNCE_MS,
  flushWorkspace,
  loadWorkspace,
  resetWorkspacePersistence,
  saveWorkspace,
  type WorkspaceState,
} from '@/charting/workspace';

/** Build a distinct, fully-formed workspace state for assertions. */
function makeState(overrides: Partial<WorkspaceState> = {}): WorkspaceState {
  return {
    version: 1,
    chartType: 'line',
    chartTypeParams: {},
    activeIndicators: [],
    drawings: [
      // A representative drawing; shape is opaque to the persistence layer.
      { id: 'd1', type: 'trendline', locked: false } as unknown as WorkspaceState['drawings'][number],
    ],
    paneLayout: [],
    ...overrides,
  };
}

afterEach(() => {
  // Forget all retained in-memory state and any pending debounce timers so each
  // test starts from a clean session.
  resetWorkspacePersistence();
  vi.useRealTimers();
});

describe('workspace restore-failure handling (Requirement 11.4)', () => {
  it('resolves an unsaved symbol to the default workspace', async () => {
    const restored = await loadWorkspace('NEVER_SAVED');
    expect(restored).toEqual(DEFAULT_WORKSPACE);
  });

  it('still returns defaults after the symbol was reset (retains nothing stale)', async () => {
    await flushWorkspace('AAPL', makeState());
    resetWorkspacePersistence('AAPL');

    const restored = await loadWorkspace('AAPL');
    expect(restored).toEqual(DEFAULT_WORKSPACE);
  });
});

describe('workspace persist-failure handling (Requirements 11.5, 5.12)', () => {
  it('flushWorkspace reports failure outside Tauri but retains in-memory state', async () => {
    const state = makeState({ chartType: 'area' });

    // No persistent backend present -> reports false (caller may retry).
    const persisted = await flushWorkspace('MSFT', state);
    expect(persisted).toBe(false);

    // ...yet the latest state is retained in memory, so a restore returns it
    // rather than discarding the workspace.
    const restored = await loadWorkspace('MSFT');
    expect(restored).toEqual(state);
  });

  it('retains the last successfully recorded drawing set across a failed persist', async () => {
    const withDrawings = makeState({
      drawings: [
        { id: 'a', type: 'rect', locked: true } as unknown as WorkspaceState['drawings'][number],
        { id: 'b', type: 'fib', locked: false } as unknown as WorkspaceState['drawings'][number],
      ],
    });

    await flushWorkspace('TSLA', withDrawings);

    const restored = await loadWorkspace('TSLA');
    expect(restored.drawings).toEqual(withDrawings.drawings);
  });

  it('updates retained state on the next change (retry-on-next-change)', async () => {
    const first = makeState({ chartType: 'line' });
    const second = makeState({ chartType: 'candlestick', chartTypeParams: {} });

    await flushWorkspace('NVDA', first);
    expect(await loadWorkspace('NVDA')).toEqual(first);

    // A subsequent change supersedes the retained copy.
    await flushWorkspace('NVDA', second);
    expect(await loadWorkspace('NVDA')).toEqual(second);
  });

  it('debounced saveWorkspace retains the latest state immediately and after the debounce window', async () => {
    vi.useFakeTimers();
    const latest = makeState({ chartType: 'area', chartTypeParams: { renkoBoxSize: 21 } });

    saveWorkspace('AMD', makeState({ chartType: 'line' }));
    saveWorkspace('AMD', latest); // collapses with the first into one write

    // In-memory store is updated synchronously, before the debounce fires.
    expect(await loadWorkspace('AMD')).toEqual(latest);

    // Advancing past the debounce window flushes without error and keeps the
    // latest state retained.
    await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS + 1);
    expect(await loadWorkspace('AMD')).toEqual(latest);
  });
});

describe('test isolation (resetWorkspacePersistence)', () => {
  it('clears retained state for a single symbol without affecting others', async () => {
    await flushWorkspace('SYM_A', makeState({ chartType: 'line' }));
    await flushWorkspace('SYM_B', makeState({ chartType: 'area' }));

    resetWorkspacePersistence('SYM_A');

    expect(await loadWorkspace('SYM_A')).toEqual(DEFAULT_WORKSPACE);
    expect(await loadWorkspace('SYM_B')).toEqual(makeState({ chartType: 'area' }));
  });

  it('clears all retained state when called without a symbol', async () => {
    await flushWorkspace('SYM_A', makeState());
    await flushWorkspace('SYM_B', makeState());

    resetWorkspacePersistence();

    expect(await loadWorkspace('SYM_A')).toEqual(DEFAULT_WORKSPACE);
    expect(await loadWorkspace('SYM_B')).toEqual(DEFAULT_WORKSPACE);
  });
});
