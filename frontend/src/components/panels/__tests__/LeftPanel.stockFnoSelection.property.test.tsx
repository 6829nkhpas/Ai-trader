// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

// Feature: fno-data-and-search-fix — dynamic stock-option F&O selection.
//
// Selecting a STOCK option contract from search (an `FNO` result whose
// `underlying` is not a configured index, e.g. NIFTY/BANKNIFTY) must:
//   1. Ask the backend to start ingesting that underlying's chain via the
//      `fno_request_underlying` command (bounded/validated server-side).
//   2. When the command returns `true` (it is a real F&O underlying), open the
//      F&O workspace: activeProfile === 'FNO', fnoUnderlying === the underlying,
//      fnoExpiry === '' (nearest).
//   3. When the command returns `false` (no NFO contracts), fall back to charting
//      the symbol exactly like an equity selection — F&O mode is NOT activated.
//
// This exercises the REAL LeftPanel handler by rendering the component and
// driving a real search → select against MOCKED `search_instruments` and
// `fno_request_underlying` IPC seams.

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import fc from 'fast-check';

const tauri = vi.hoisted(() => ({ invokeMock: null as any }));

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (...args: any[]) => tauri.invokeMock(...args),
}));

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

interface StockFnoResult {
  kind: 'FNO';
  tradingsymbol: string;
  underlying: string;
  expiry: string;
  strike: number | null;
  optionType: 'CE' | 'PE' | 'FUT';
}

/** A stock (non-index) option contract, e.g. RELIANCE / TCS. */
const stockFnoArb: fc.Arbitrary<StockFnoResult> = fc
  .record({
    underlying: fc.constantFrom('RELIANCE', 'TCS', 'INFY', 'HDFCBANK'),
    optionType: fc.constantFrom<'CE' | 'PE' | 'FUT'>('CE', 'PE', 'FUT'),
    strike: fc.integer({ min: 500, max: 4000 }),
  })
  .map(({ underlying, optionType, strike }) => {
    const eff = optionType === 'FUT' ? null : strike;
    const tradingsymbol =
      optionType === 'FUT' ? `${underlying}26JULFUT` : `${underlying}26JUL${strike}${optionType}`;
    return { kind: 'FNO' as const, tradingsymbol, underlying, expiry: '2026-07-28', strike: eff, optionType };
  });

/** Install invoke so `fno_request_underlying` resolves to `requestResult`. */
function installInvoke(result: StockFnoResult, requestResult: boolean) {
  tauri.invokeMock = vi.fn(async (cmd: string) => {
    if (cmd === 'search_instruments') return [result];
    if (cmd === 'fno_request_underlying') return requestResult;
    if (cmd === 'load_workspace') return '{}';
    // fno_list_chains → keep default configured underlyings (indexes only).
    return undefined;
  });
}

function seed() {
  useTradeStore.setState({
    activeProfile: 'INTRADAY',
    fnoUnderlying: 'NIFTY 50',
    fnoExpiry: '2024-12-26',
    watchlist: [],
    selectedSymbol: 'SEED',
  });
}

beforeEach(() => {
  seed();
  (global as any).fetch = vi.fn(async () => ({ ok: false, json: async () => ({}) }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function searchAndSelect(result: StockFnoResult) {
  cleanup();
  render(React.createElement(LeftPanel));
  const input = screen.getByLabelText('Search symbols') as HTMLInputElement;
  fireEvent.change(input, { target: { value: result.underlying.slice(0, 4) } });
  const row = await screen.findByText(result.tradingsymbol, {}, { timeout: 3000 });
  fireEvent.click(row);
}

describe('Stock option selection activates F&O when the backend accepts the underlying', () => {
  it('a valid stock option opens F&O with the stock underlying and reset expiry', async () => {
    await fc.assert(
      fc.asyncProperty(stockFnoArb, async (result) => {
        seed();
        installInvoke(result, true); // backend confirms it is an F&O underlying

        await searchAndSelect(result);

        await waitFor(() => {
          expect(useTradeStore.getState().activeProfile).toBe('FNO');
        });
        const s = useTradeStore.getState();
        expect(s.fnoUnderlying).toBe(result.underlying);
        expect(s.fnoExpiry).toBe('');
        // The request command was invoked with the stock underlying.
        expect(tauri.invokeMock).toHaveBeenCalledWith('fno_request_underlying', {
          underlying: result.underlying,
        });

        cleanup();
        vi.clearAllMocks();
      }),
      { numRuns: 6 },
    );
  });

  it('concrete example: RELIANCE26JUL2500CE opens F&O for RELIANCE', async () => {
    const result: StockFnoResult = {
      kind: 'FNO',
      tradingsymbol: 'RELIANCE26JUL2500CE',
      underlying: 'RELIANCE',
      expiry: '2026-07-28',
      strike: 2500,
      optionType: 'CE',
    };
    installInvoke(result, true);
    await searchAndSelect(result);
    await waitFor(() => expect(useTradeStore.getState().activeProfile).toBe('FNO'));
    expect(useTradeStore.getState().fnoUnderlying).toBe('RELIANCE');
    expect(useTradeStore.getState().fnoExpiry).toBe('');
  });
});

describe('Stock option selection falls back to charting when the backend rejects it', () => {
  it('when fno_request_underlying returns false, it charts the symbol and does NOT activate F&O', async () => {
    const result: StockFnoResult = {
      kind: 'FNO',
      tradingsymbol: 'XYZ26JUL100CE',
      underlying: 'XYZ',
      expiry: '2026-07-28',
      strike: 100,
      optionType: 'CE',
    };
    installInvoke(result, false); // no NFO data → rejected

    await searchAndSelect(result);

    await waitFor(() => {
      expect(useTradeStore.getState().selectedSymbol).toBe('XYZ26JUL100CE');
    });
    // F&O mode must NOT be activated on a rejected underlying.
    expect(useTradeStore.getState().activeProfile).toBe('INTRADAY');
  });
});
