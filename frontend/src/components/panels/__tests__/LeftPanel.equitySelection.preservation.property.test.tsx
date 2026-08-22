// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

// Feature: fno-data-and-search-fix (bugfix)
//
// Property 3 (Preservation), frontend store/handler seam —
// "Non-Buggy Inputs Behave Identically" — equity search routing (R3.4):
//
//   For all selections where NEITHER bug condition holds — `handleSelectResult`
//   (LeftPanel) receives an `EQ` search result — the selection SHALL route to
//   the price chart via `selectedSymbol` (single view) or the ACTIVE pane
//   (split view) exactly as today, and SHALL NEVER activate F&O mode:
//     activeProfile is UNCHANGED (never becomes 'FNO')
//     fnoUnderlying / fnoExpiry are UNCHANGED
//     single view  -> selectedSymbol === result.symbol
//     split  view  -> active pane symbol === result.symbol, sibling untouched
//
//   Validates: Requirements 3.4
//
// *** PRESERVATION TEST — OBSERVATION-FIRST — EXPECTED TO PASS ON UNFIXED CODE ***
//
// This is the non-buggy counterpart of the Defect B exploration test
// (LeftPanel.fnoSelection.bug.property.test.tsx). It records the behavior the
// fix (task 3.4) must preserve byte-for-byte: equity selections keep flowing to
// the chart target and never touch F&O state. The fix branches `handleSelectResult`
// on `kind`, so this test pins the `EQ` branch's existing behavior. It MUST PASS
// on unfixed code — that is what makes it a preservation baseline.
//
// This exercises the REAL LeftPanel handler by rendering the component and
// driving a real search -> select interaction against a MOCKED
// `search_instruments` IPC seam. The chart-routing target is read from the real
// `useTradeStore` / `useChartUIStore`, so the routing observed is production
// behavior, not a re-implementation.

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
import { useTradeStore, type TradeProfile } from '@/store/useTradeStore';
import { useChartUIStore } from '@/store/useChartUIStore';

/** An equity (non-F&O) search result — the preservation input space (R3.4). */
interface EqSearchResult {
  kind: 'EQ';
  symbol: string;
  name: string;
  exchange: string;
}

const EQ_SYMBOLS = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'SBIN', 'ITC'];
// Every non-FNO profile the store can be in before an equity selection; the
// selection must leave ALL of them unchanged.
const NON_FNO_PROFILES: TradeProfile[] = ['INTRADAY', 'SWING', 'INVESTOR'];

/** Generator for equity results. */
const eqResultArb: fc.Arbitrary<EqSearchResult> = fc.record({
  kind: fc.constant('EQ' as const),
  symbol: fc.constantFrom(...EQ_SYMBOLS),
  name: fc.string({ maxLength: 20 }),
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

beforeEach(() => {
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
  // Two+ chars trigger the debounced (400ms) search -> search_instruments.
  fireEvent.change(input, { target: { value: result.symbol.slice(0, 3) } });

  const row = await screen.findByText(result.symbol, {}, { timeout: 3000 });
  fireEvent.click(row);
}

describe('Preservation (equity selection): routes to chart and never activates F&O — EXPECTED PASS on unfixed code', () => {
  it('single view: an EQ selection sets selectedSymbol and leaves F&O state (activeProfile/fnoUnderlying/fnoExpiry) unchanged', async () => {
    await fc.assert(
      fc.asyncProperty(
        eqResultArb,
        fc.constantFrom(...NON_FNO_PROFILES),
        async (result, profile) => {
          // Seed a discriminating, non-target state: NOT F&O mode, a distinct
          // fnoUnderlying/expiry and a different selectedSymbol so each asserted
          // field is genuinely observable.
          useChartUIStore.setState({ splitView: false, activePaneId: 'A' });
          useTradeStore.setState({
            activeProfile: profile,
            fnoUnderlying: 'BANKNIFTY',
            fnoExpiry: '2024-12-26',
            watchlist: [],
            selectedSymbol: '__PRESERVE_SENTINEL__',
          });
          installInvoke(result);

          await searchAndSelect(result);

          // Single view routes the equity to the sole charted symbol.
          await waitFor(() => {
            expect(useTradeStore.getState().selectedSymbol).toBe(result.symbol);
          });

          const s = useTradeStore.getState();
          // F&O state is never touched by an equity selection (R3.4).
          expect(s.activeProfile).toBe(profile);
          expect(s.activeProfile).not.toBe('FNO');
          expect(s.fnoUnderlying).toBe('BANKNIFTY');
          expect(s.fnoExpiry).toBe('2024-12-26');

          cleanup();
          vi.clearAllMocks();
        },
      ),
      { numRuns: 5 },
    );
  });

  it('split view: an EQ selection sets ONLY the active pane symbol; the sibling pane and F&O state are unchanged', async () => {
    await fc.assert(
      fc.asyncProperty(
        eqResultArb,
        fc.constantFrom<'A' | 'B'>('A', 'B'),
        async (result, activePaneId) => {
          const sibling = activePaneId === 'A' ? 'B' : 'A';
          useChartUIStore.setState({
            splitView: true,
            activePaneId,
            panes: [
              { id: 'A', symbol: 'SIB_A', timeframe: '10m', chartType: 'candlestick' },
              { id: 'B', symbol: 'SIB_B', timeframe: '10m', chartType: 'candlestick' },
            ],
          });
          const siblingBefore = {
            ...useChartUIStore.getState().panes.find((p) => p.id === sibling)!,
          };
          useTradeStore.setState({
            activeProfile: 'INTRADAY',
            fnoUnderlying: 'BANKNIFTY',
            fnoExpiry: '2024-12-26',
            watchlist: [],
            selectedSymbol: '__PRESERVE_SENTINEL__',
          });
          installInvoke(result);

          await searchAndSelect(result);

          // The active pane took the equity symbol...
          await waitFor(() => {
            const active = useChartUIStore.getState().panes.find((p) => p.id === activePaneId)!;
            expect(active.symbol).toBe(result.symbol);
          });

          // ...the sibling pane is byte-for-byte unchanged...
          const siblingAfter = useChartUIStore.getState().panes.find((p) => p.id === sibling)!;
          expect(siblingAfter).toEqual(siblingBefore);

          // ...the single-view selectedSymbol is NOT touched in split view...
          expect(useTradeStore.getState().selectedSymbol).toBe('__PRESERVE_SENTINEL__');

          // ...and F&O mode is never activated (R3.4).
          const s = useTradeStore.getState();
          expect(s.activeProfile).toBe('INTRADAY');
          expect(s.fnoUnderlying).toBe('BANKNIFTY');
          expect(s.fnoExpiry).toBe('2024-12-26');

          cleanup();
          vi.clearAllMocks();
        },
      ),
      { numRuns: 5 },
    );
  });
});
