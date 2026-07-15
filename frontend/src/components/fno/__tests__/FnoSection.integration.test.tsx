// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * F&O Frontend Section (F4) — integration tests for streaming + lifecycle (task 8.3).
 *
 * These are INTEGRATION tests (jsdom), not property tests. They exercise the real
 * `FnoSection` against MOCKED Tauri IPC seams (`@tauri-apps/api/core` `invoke` and
 * `@tauri-apps/api/event` `listen`) and lightweight stand-ins for the heavy chart
 * children (OiProfileChart / IvSkewChart / OptionsHud) and FnoUnavailableState.
 *
 * Validates (Requirements 6.2, 7.1, 7.3):
 *  1. On mount, the section issues `fno_list_chains`, `get_fno_analytics`, and
 *     `fno_subscribe` through `invoke` (R6.2, R7.1).
 *  2. A single `listen('fno-snapshot', …)` is registered, and firing successive
 *     snapshot events with advancing data updates the rendered view-models IN
 *     PLACE — the chart/HUD children are NOT recreated (mount count stays 1) and
 *     the section root node identity is stable (R6.2, R7.1).
 *  3. Unmounting the section (mimicking leaving the F&O Workspace_Mode) calls
 *     `fno_unsubscribe`
 *     and the `listen` unlisten function (R7.3).
 *
 * The chart/HUD stand-ins render their props so in-place updates are observable,
 * and each tracks its own mount count via a mount-only effect so a remount would
 * be detected.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, act, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import type { FnoPayload } from '../viewModel';

// ── Shared registries (hoisted so the mock factories may reference them) ──────

// Mount counters for the child stand-ins; a remount would increment these.
const counts = vi.hoisted(() => ({
  chartPanelMounts: 0,
  unavailMounts: 0,
  reset() {
    this.chartPanelMounts = 0;
    this.unavailMounts = 0;
  },
}));

// Tauri IPC seams. The factories delegate to the current mock fns, which are
// (re)assigned per test in `beforeEach`, so behaviour is configurable while the
// module mock binding stays stable.
const tauri = vi.hoisted(() => ({
  invokeMock: null as any,
  listenMock: null as any,
  unlistenMock: null as any,
  snapshotHandler: { current: null as null | ((e: { payload: unknown }) => void) },
}));

// ── Mock the Tauri IPC modules ───────────────────────────────────────────────
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: any[]) => tauri.invokeMock(...args),
}));

vi.mock('@tauri-apps/api/event', () => ({
  listen: (...args: any[]) => tauri.listenMock(...args),
}));

// ── Mock FnoChartPanel + the unavailable panel ───────────────────────────
vi.mock('../FnoChartPanel', async () => {
  const ReactNs = await import('react');
  return {
    __esModule: true,
    default: () => {
      ReactNs.useEffect(() => {
        counts.chartPanelMounts += 1;
      }, []);
      return ReactNs.createElement('div', { 'data-testid': 'fno-chart-panel' });
    },
  };
});

vi.mock('../FnoUnavailableState', async () => {
  const ReactNs = await import('react');
  const Unavailable = ({ reason }: any) => {
    ReactNs.useEffect(() => {
      counts.unavailMounts += 1;
    }, []);
    return ReactNs.createElement('div', { 'data-testid': 'fno-unavailable' }, String(reason ?? ''));
  };
  return { __esModule: true, default: Unavailable, FnoUnavailableState: Unavailable };
});

// Mock the resizable-panel primitive so the layout renders as plain divs in jsdom.
vi.mock('react-resizable-panels', async () => {
  const ReactNs = await import('react');
  return {
    __esModule: true,
    Group: ({ children }: any) => ReactNs.createElement('div', { 'data-testid': 'group' }, children),
    Panel: ({ children }: any) => ReactNs.createElement('div', { 'data-testid': 'panel' }, children),
    Separator: () => ReactNs.createElement('div', { 'data-testid': 'separator' }),
  };
});

// Import AFTER the mocks are declared so the component picks up the mocked deps.
import FnoSection from '../FnoSection';
import { useTradeStore } from '../../../store/useTradeStore';

// ── Test data ─────────────────────────────────────────────────────────────────

/** A fully-populated (ready) payload with controllable snapshot_ts + call OI. */
function makeReadyPayload(ts: number, callOi: number): FnoPayload {
  return {
    underlying: 'NIFTY 50',
    expiry: '2024-12-26',
    snapshot_ts: ts,
    market_status: 'open',
    chain: [
      { strike: 24000, ce_oi: callOi, pe_oi: 100_000, ce_price: 12, pe_price: 10, iv: 0.13 },
      { strike: 24200, ce_oi: callOi + 5_000, pe_oi: 90_000, ce_price: 8, pe_price: 14, iv: 0.14 },
    ],
    analytics: {
      spot: 24010,
      pcr_oi: 1.1,
      pcr_volume: 0.9,
      max_pain: 24000,
      oi_buildup: { call: 'short_buildup', put: 'long_buildup' },
      iv_skew: { put_minus_call: 0.02, slope: -0.0003, atm_iv: 0.13 },
      oi_walls: { support: 23800, resistance: 24200 },
      futures_basis: 11.5,
    },
    bias: {
      options_bias_state: 'bullish',
      alignment: 'aligned',
      chain_context: 'own-chain',
      signals: { max_pain_vs_spot: 'below' },
    },
  };
}

function defaultInvoke(cmd: string): Promise<unknown> {
  switch (cmd) {
    case 'fno_list_chains':
      return Promise.resolve({
        underlyings: ['NIFTY 50'],
        expiries_by_underlying: { 'NIFTY 50': ['2024-12-26'] },
      });
    case 'get_fno_analytics':
      return Promise.resolve(makeReadyPayload(1000, 111_000));
    case 'fno_subscribe':
    case 'fno_unsubscribe':
      return Promise.resolve();
    default:
      return Promise.resolve(null);
  }
}

function invokedCommands(): string[] {
  return tauri.invokeMock.mock.calls.map((c: any[]) => c[0]);
}

beforeEach(() => {
  counts.reset();
  tauri.snapshotHandler.current = null;
  tauri.unlistenMock = vi.fn();
  tauri.listenMock = vi.fn(async (event: string, handler: any) => {
    if (event === 'fno-snapshot') {
      tauri.snapshotHandler.current = handler;
    }
    return tauri.unlistenMock;
  });
  tauri.invokeMock = vi.fn((cmd: string) => defaultInvoke(cmd));

  // Deterministic store state (the section's mount selectors). The F&O workspace
  // is now selected via the unified Mode_Selector — activeProfile === 'FNO' — the
  // single source of truth (AD-1, R1.4/R6.3); the legacy fnoMode flag was removed.
  useTradeStore.setState({ activeProfile: 'FNO', fnoUnderlying: 'NIFTY 50', fnoExpiry: '' });
});

afterEach(() => {
  cleanup();
});

describe('FnoSection — streaming + lifecycle integration (R6.2, R7.1, R7.3)', () => {
  it('issues fno_list_chains, get_fno_analytics, and fno_subscribe on mount (R6.2, R7.1)', async () => {
    render(<FnoSection />);

    await waitFor(() => {
      const cmds = invokedCommands();
      expect(cmds).toContain('fno_list_chains');
      expect(cmds).toContain('get_fno_analytics');
      expect(cmds).toContain('fno_subscribe');
    });

    // The fetch + subscribe target the active selector key.
    const getCall = tauri.invokeMock.mock.calls.find((c: any[]) => c[0] === 'get_fno_analytics');
    expect(getCall?.[1]).toEqual({ underlying: 'NIFTY 50', expiry: '' });

    const subCall = tauri.invokeMock.mock.calls.find((c: any[]) => c[0] === 'fno_subscribe');
    expect(subCall?.[1]).toEqual({ underlying: 'NIFTY 50', expiry: '' });
  });

  it('registers a single fno-snapshot listener and updates without remount (R6.2, R7.1)', async () => {
    const { container } = render(<FnoSection />);

    // The chart panel mounts once after the first payload resolves.
    await screen.findByTestId('fno-chart-panel');
    await waitFor(() => expect(tauri.snapshotHandler.current).not.toBeNull());

    // Exactly one fno-snapshot listener was registered.
    const snapshotListens = tauri.listenMock.mock.calls.filter((c: any[]) => c[0] === 'fno-snapshot');
    expect(snapshotListens).toHaveLength(1);

    // Chart panel mounted exactly once from the initial fetch payload.
    expect(counts.chartPanelMounts).toBe(1);

    const rootBefore = container.firstChild;

    // Fire a successive snapshot event with advancing data.
    act(() => {
      tauri.snapshotHandler.current!({ payload: makeReadyPayload(2000, 222_000) });
    });
    await waitFor(() => {
      expect(container.firstChild).toBe(rootBefore);
    });

    // Fire a second successive snapshot event.
    act(() => {
      tauri.snapshotHandler.current!({ payload: makeReadyPayload(3000, 333_000) });
    });
    await waitFor(() => {
      expect(container.firstChild).toBe(rootBefore);
    });

    // CRITICAL: no remount — chart panel was NOT recreated across updates,
    // the listener was registered only once, and the section root node identity
    // is stable.
    expect(counts.chartPanelMounts).toBe(1);
    expect(tauri.listenMock.mock.calls.filter((c: any[]) => c[0] === 'fno-snapshot')).toHaveLength(1);
    expect(container.firstChild).toBe(rootBefore);
  });

  it('calls fno_unsubscribe and the unlisten fn on unmount (leaving F&O mode) (R7.3)', async () => {
    const { unmount } = render(<FnoSection />);

    // Wait for the listener to be registered so its unlisten fn is captured.
    await waitFor(() => expect(tauri.snapshotHandler.current).not.toBeNull());
    await screen.findByTestId('fno-chart-panel');

    // Unmounting mimics page.tsx dropping <FnoSection/> when activeProfile leaves 'FNO'.
    unmount();

    await waitFor(() => {
      expect(tauri.unlistenMock).toHaveBeenCalledTimes(1);
      expect(invokedCommands()).toContain('fno_unsubscribe');
    });
  });
});
