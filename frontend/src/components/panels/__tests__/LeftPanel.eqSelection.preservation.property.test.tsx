// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

// Feature: fno-data-and-search-fix (bugfix)
//
// Property 3 (Preservation), frontend store/handler seam —
// "Non-Buggy Inputs Behave Identically" for equity search selection (R3.4):
//
//   For all selections where neither bug condition holds — specifically an
//   ordinary equity (`kind: 'EQ'`) search result selected through the REAL
//   `handleSelectResult` (LeftPanel) — the selection SHALL route to the price
//   chart via `selectedSymbol` (single view) or the active pane (split view)
//   EXACTLY as today, and SHALL NEVER activate F&O mode:
//     - single view:  selectedSymbol === resultSymbol; activeProfile unchanged
//     - split view:   active pane symbol === resultSymbol; selectedSymbol and
//                     activeProfile unchanged
//     - fnoUnderlying / fnoExpiry are never touched by an equity selection
//
//   Validates: Requirements 3.4
//
// *** PRESERVATION BASELINE — OBSERVATION-FIRST, EXPECTED TO PASS ON UNFIXED CODE ***
//
// This is the equity counterpart to the Defect B exploration test
// (LeftPanel.fnoSelection.bug.property.test.tsx). Observation-first: we run the
// UNFIXED `handleSelectResult` on equity selections and record its ACTUAL
// behavior — it funnels the pick through `routeSymbolToChart`, which sets only
// `selectedSymbol` (single view) or the active pane (split view) and never
// touches `activeProfile`/`fnoUnderlying`/`fnoExpiry`. The fix (task 3.4)
// branches the handler ONLY on `kind === 'FNO'`; the `kind === 'EQ'` path is
// left unchanged, so this baseline must keep passing after the fix (task 3.6).
//
// This exercises the REAL LeftPanel handler by rendering the component and
// driving a real search -> select interaction against a MOCKED
// `search_instruments` IPC seam.

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import fc from 'fast-check';

// ── Configurable Tauri IPC seam ───────────────────────────────────────────────
const tauri = vi.hoisted(() => ({ invokeMock: null as any }));

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: any[]) => tauri.invokeMock(...args),
}));

// Stub the heavy / store-coupled child panels so the render stays focused on
// the search + selection handler under test (mirrors the Defect B bug test).
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
import { useChartUIStore } from '@/store/useChartUIStore';

/** An ordinary equity search result (the non-buggy, preserved path). */
interface EqSearchResult {
  kind: 'EQ';
  symbol: string;
  name: string;
  exchange: string;
}

// A pool of equity symbols that deliberately EXCLUDES the seeded selectedSymbol
// sentinel below, so every asserted transition is discriminating.
const EQ_SYMBOLS = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'SBIN'];
const SEED_SYMBOL = 'SEED_SENTINEL';

/** Generator for equity results drawn from the pool. */
const eqResultArb: fc.Arbitrary<EqSearchResult> = fc.record({
  kind: fc.constant('EQ' as const),
  symbol: fc.constantFrom(...EQ_SYMBOLS),
  name: fc.constantFrom('Reliance Industries', 'Tata Consultancy', '', 'Infosys Ltd'),
  exchange: fc.constantFrom('NSE', 'BSE'),
});

function installInvoke(result: EqSearchResult) {
  tauri.invokeMock = vi.fn(async (cmd: string) => {
    if (cmd === 'search_instruments') return [result];
    if (cmd === 'load_workspace') return '{}';
    // save_workspace and anything else resolve benignly.
    return undefined;
  });
}

/** Reset both stores to a known, NON-target baseline for a discriminating assert. */
function seedStores() {
  useTradeStore.setState({
    activeProfile: 'INTRADAY',
    fnoUnderlying: 'BANKNIFTY',
    fnoExpiry: '2024-12-26',
    watchlist: [],
    selectedSymbol: SEED_SYMBOL,
  });
  useChartUIStore.setState({
    splitView: false,
    activePaneId: 'A',
    panes: [
      { id: 'A', symbol: SEED_SYMBOL, timeframe: '10m', chartType: 'candlestick' },
      { id: 'B', symbol: SEED_SYMBOL, timeframe: '10m', chartType: 'candlestick' },
    ],
  });
}

beforeEach(() => {
  seedStores();
  // Quotes fetch — no network in jsdom; return a benign non-ok response.
  (global as any).fetch = vi.fn(async () => ({ ok: false, json: async () => ({}) }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

/** Render LeftPanel, search, and click the equity result row. */
async function searchAndSelect(result: EqSearchResult) {
  cleanup(); // isolate the DOM for this iteration
  render(React.createElement(LeftPanel));

  const input = screen.getByLabelText('Search symbols') as HTMLInputElement;
  // Two+ chars trigger the debounced search (handleInputChange -> handleSearch).
  fireEvent.change(input, { target: { value: 'STOCK' } });

  // The debounced (400ms) search invokes `search_instruments`; wait for the
  // equity row (renders the symbol) to appear, then select it.
  const row = await screen.findByText(result.symbol, {}, { timeout: 3000 });
  fireEvent.click(row);
}

describe('Preservation (R3.4): equity selection routes to the chart and never activates F&O', () => {
  it('single view — selecting an EQ result sets selectedSymbol and leaves F&O state untouched', async () => {
    await fc.assert(
      fc.asyncProperty(eqResultArb, async (result) => {
        seedStores(); // re-seed each iteration (beforeEach only runs once per `it`)
        useChartUIStore.setState({ splitView: false });
        installInvoke(result);

        await searchAndSelect(result);

        // OBSERVED baseline: the equity symbol is routed to the single chart.
        await waitFor(() => {
          expect(useTradeStore.getState().selectedSymbol).toBe(result.symbol);
        });

        const s = useTradeStore.getState();
        // F&O mode is never activated by an equity selection (R3.4).
        expect(s.activeProfile).toBe('INTRADAY');
        expect(s.activeProfile).not.toBe('FNO');
        // The F&O selector state is untouched.
        expect(s.fnoUnderlying).toBe('BANKNIFTY');
        expect(s.fnoExpiry).toBe('2024-12-26');

        cleanup();
        vi.clearAllMocks();
      }),
      { numRuns: 5 },
    );
  });

  it('split view — selecting an EQ result sets ONLY the active pane symbol and never activates F&O', async () => {
    await fc.assert(
      fc.asyncProperty(
        eqResultArb,
        fc.constantFrom<'A' | 'B'>('A', 'B'),
        async (result, activePaneId) => {
          seedStores();
          useChartUIStore.setState({ splitView: true, activePaneId });
          installInvoke(result);

          await searchAndSelect(result);

          const sibling = activePaneId === 'A' ? 'B' : 'A';

          // OBSERVED baseline: the active pane receives the symbol...
          await waitFor(() => {
            const pane = useChartUIStore.getState().panes.find((p) => p.id === activePaneId)!;
            expect(pane.symbol).toBe(result.symbol);
          });

          const chartState = useChartUIStore.getState();
          // ...the sibling pane is untouched...
          const siblingPane = chartState.panes.find((p) => p.id === sibling)!;
          expect(siblingPane.symbol).toBe(SEED_SYMBOL);
          // ...the single-view selectedSymbol is NOT changed in split view...
          expect(useTradeStore.getState().selectedSymbol).toBe(SEED_SYMBOL);
          // ...and F&O mode is never activated.
          expect(useTradeStore.getState().activeProfile).toBe('INTRADAY');

          cleanup();
          vi.clearAllMocks();
        },
      ),
      { numRuns: 5 },
    );
  });

  it('concrete example: selecting RELIANCE charts it without activating F&O', async () => {
    const result: EqSearchResult = {
      kind: 'EQ',
      symbol: 'RELIANCE',
      name: 'Reliance Industries',
      exchange: 'NSE',
    };
    seedStores();
    useChartUIStore.setState({ splitView: false });
    installInvoke(result);

    await searchAndSelect(result);

    await waitFor(() => {
      expect(useTradeStore.getState().selectedSymbol).toBe('RELIANCE');
    });
    const s = useTradeStore.getState();
    expect(s.activeProfile).toBe('INTRADAY');
    expect(s.fnoUnderlying).toBe('BANKNIFTY');
    expect(s.fnoExpiry).toBe('2024-12-26');
  });
});
