// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

// Feature: fno-data-and-search-fix (bugfix)
//
// Property 2 (Bug Condition), frontend store/handler seam —
// "Configured F&O Underlyings Are Searchable and Activate F&O Mode":
//
//   For all selections satisfying isBugCondition_B — `handleSelectResult`
//   (LeftPanel) receives an `FNO` search result whose `underlying` is a
//   configured underlying (e.g. NIFTY24DEC24000CE, underlying: "NIFTY 50") —
//   after selection:
//     activeProfile === 'FNO'
//     fnoUnderlying === selected_result.underlying
//     fnoExpiry === ''   (reset to nearest)
//
//   Validates: Requirements 1.4, 1.5, 2.5, 2.6
//
// *** EXPLORATION TEST — EXPECTED TO FAIL ON UNFIXED CODE ***
//
// The unfixed `handleSelectResult` funnels EVERY result — equity or F&O —
// through `routeSymbolToChart`, which only sets `selectedSymbol` (the price
// chart). It never calls `setActiveProfile('FNO')` or `setFnoUnderlying(...)`,
// so selecting an F&O contract routes to a price chart and never activates the
// F&O workspace. The failure of this test is the informative, expected outcome.
// DO NOT fix the test or the code here; task 3.4 branches the handler on `kind`
// and task 3.5 re-runs this same test to confirm the fix.
//
// This exercises the REAL LeftPanel handler by rendering the component and
// driving a real search → select interaction against a MOCKED
// `search_instruments` IPC seam.

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import fc from 'fast-check';

// ── Configurable transport seam ───────────────────────────────────────────────
// The panel reaches the backend through `lib/bridge`, so the mock goes there
// rather than at `@tauri-apps/api/core`: the routing under test is the same on
// desktop IPC and on the website's HTTP path.
const tauri = vi.hoisted(() => ({ invokeMock: null as any }));

vi.mock('../../../lib/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/bridge')>()),
  bridgeInvoke: (...args: any[]) => tauri.invokeMock(...args),
}));

// Stub the heavy / store-coupled child panels so the render stays focused on
// the search + selection handler under test.
vi.mock('../left-panel/LiveAssetHUD', () => ({
  __esModule: true,
  default: () => React.createElement('div', { 'data-testid': 'live-asset-hud' }),
}));
vi.mock('../left-panel/SentimentBlock', () => ({
  __esModule: true,
  default: () => React.createElement('div', { 'data-testid': 'sentiment-block' }),
}));
vi.mock('../../quant/deep-quant/MultiTfPatternsView', () => ({
  __esModule: true,
  default: () => React.createElement('div', { 'data-testid': 'multi-tf-patterns' }),
}));

import LeftPanel from '../LeftPanel';
import { useTradeStore } from '@/store/useTradeStore';

/** An FNO search result whose underlying is the configured index (R3.6). */
interface FnoSearchResult {
  kind: 'FNO';
  tradingsymbol: string;
  underlying: string;
  expiry: string;
  strike: number | null;
  optionType: 'CE' | 'PE' | 'FUT';
}

const CONFIGURED_UNDERLYING = 'NIFTY 50';

/** Generator for configured-underlying FNO results (CE/PE carry a strike). */
const configuredFnoResultArb: fc.Arbitrary<FnoSearchResult> = fc
  .record({
    expiry: fc.constantFrom('2024-12-26', '2025-01-30', '2025-02-27'),
    optionType: fc.constantFrom<'CE' | 'PE' | 'FUT'>('CE', 'PE', 'FUT'),
    strike: fc.oneof(fc.integer({ min: 20000, max: 30000 }), fc.constant(null)),
  })
  .map(({ expiry, optionType, strike }) => {
    const eff = optionType === 'FUT' ? null : (strike ?? 24000);
    const tag = expiry.replace(/-/g, '');
    const tradingsymbol =
      optionType === 'FUT' ? `NIFTY${tag}FUT` : `NIFTY${tag}${eff}${optionType}`;
    return {
      kind: 'FNO' as const,
      tradingsymbol,
      underlying: CONFIGURED_UNDERLYING,
      expiry,
      strike: eff,
      optionType,
    };
  });

function installInvoke(result: FnoSearchResult) {
  tauri.invokeMock = vi.fn(async (cmd: string) => {
    if (cmd === 'search_instruments') return [result];
    if (cmd === 'load_workspace') return '{}';
    // save_workspace and anything else resolve benignly.
    return undefined;
  });
}

beforeEach(() => {
  // Seed the store to NON-target values so every asserted transition is
  // discriminating (activeProfile flips to FNO, fnoUnderlying changes, expiry
  // resets). Defaults would otherwise mask the fnoUnderlying assertion.
  useTradeStore.setState({
    activeProfile: 'INTRADAY',
    fnoUnderlying: 'BANKNIFTY',
    fnoExpiry: '2024-12-26',
    watchlist: [],
    selectedSymbol: 'RELIANCE',
  });
  // Quotes fetch — no network in jsdom; return a benign non-ok response.
  (global as any).fetch = vi.fn(async () => ({ ok: false, json: async () => ({}) }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Render LeftPanel, search for `query`, and click the FNO result. */
async function searchAndSelect(result: FnoSearchResult) {
  cleanup(); // isolate the DOM for this iteration
  render(React.createElement(LeftPanel));

  const input = screen.getByLabelText('Search symbols') as HTMLInputElement;
  // Two+ chars trigger the debounced search (handleInputChange → handleSearch).
  fireEvent.change(input, { target: { value: 'NIFTY' } });

  // The debounced (400ms) search invokes `search_instruments`; wait for the
  // result row to appear, then select it.
  const row = await screen.findByText(result.tradingsymbol, {}, { timeout: 3000 });
  fireEvent.click(row);
}

describe('Defect B (F&O selection): search selection must activate F&O mode — EXPECTED FAIL on unfixed code', () => {
  it('selecting a configured FNO result activates F&O mode with the selected underlying and reset expiry', async () => {
    await fc.assert(
      fc.asyncProperty(configuredFnoResultArb, async (result) => {
        // Re-seed to non-target values each iteration so the transition is
        // always discriminating (beforeEach only runs once per `it`).
        useTradeStore.setState({
          activeProfile: 'INTRADAY',
          fnoUnderlying: 'BANKNIFTY',
          fnoExpiry: '2024-12-26',
          watchlist: [],
          selectedSymbol: 'RELIANCE',
        });
        installInvoke(result);

        await searchAndSelect(result);

        // Give the handler's synchronous store writes a tick to settle.
        await waitFor(() => {
          // EXPECTED FAIL on unfixed code: activeProfile stays 'INTRADAY'.
          expect(useTradeStore.getState().activeProfile).toBe('FNO');
        });

        const s = useTradeStore.getState();
        expect(s.fnoUnderlying).toBe(result.underlying);
        expect(s.fnoExpiry).toBe('');

        cleanup();
        vi.clearAllMocks();
      }),
      { numRuns: 4 },
    );
  });

  it('concrete example: NIFTY24DEC24000CE (underlying NIFTY 50) activates F&O mode', async () => {
    const result: FnoSearchResult = {
      kind: 'FNO',
      tradingsymbol: 'NIFTY24DEC24000CE',
      underlying: CONFIGURED_UNDERLYING,
      expiry: '2024-12-26',
      strike: 24000,
      optionType: 'CE',
    };
    installInvoke(result);

    await searchAndSelect(result);

    await waitFor(() => {
      // EXPECTED FAIL on unfixed code: only selectedSymbol is set.
      expect(useTradeStore.getState().activeProfile).toBe('FNO');
    });
    const s = useTradeStore.getState();
    expect(s.fnoUnderlying).toBe('NIFTY 50');
    expect(s.fnoExpiry).toBe('');
  });
});
