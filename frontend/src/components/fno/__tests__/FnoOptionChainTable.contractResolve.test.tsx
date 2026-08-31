// @vitest-environment jsdom
//
// Why clicking a strike in the ladder never loaded the chart.
//
// `openContractChart` used to run the contract resolver ONLY when
// `__TAURI_INTERNALS__` was present, and otherwise wrote
// `` `${underlying}${strike}${type}` `` straight into `selectedSymbol`. In a
// browser that is always the second branch, and the string it builds
// ("NIFTY24500CE") is not a tradingsymbol — a real NFO symbol carries the expiry
// ("NIFTY25JAN24500CE"). `isFnoSymbol` accepts it anyway (ends in CE, has a
// digit), so the chart tried to plot a contract that does not exist and drew
// nothing.
//
// `fno_resolve_option_contract` has been implemented for the web all along; it
// reads the true tradingsymbol out of `option_chain_snapshots`. These tests pin
// that it is called, that its answer reaches the store, and that a miss leaves the
// chart alone rather than pointing it at a fabricated symbol.
import React from 'react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock('@/lib/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/bridge')>()),
  bridgeInvoke: invokeMock,
}));

import FnoOptionChainTable from '../FnoOptionChainTable';
import { useTradeStore } from '@/store/useTradeStore';
import type { FnoViewState } from '../viewModel';

type ReadyView = FnoViewState & { kind: 'ready' | 'partial' };

const STRIKE = 24100;

function view(): ReadyView {
  return {
    kind: 'ready',
    snapshotTs: Date.now(),
    marketStatus: 'open',
    oi: {
      points: [
        { strike: 24000, callOi: 10, putOi: 10, callPrice: 100, putPrice: 90, iv: 18 },
        { strike: STRIKE, callOi: 20, putOi: 20, callPrice: 80, putPrice: 70, iv: 18 },
      ],
    },
    hud: {
      context: { underlying: 'NIFTY', expiry: '2026-09-24', spot: 24050 },
      maxPain: 24050,
      futuresBasis: null,
    },
  } as unknown as ReadyView;
}

/** Click the first Call LTP cell of the STRIKE row. */
function clickACall(container: HTMLElement) {
  const cells = Array.from(container.querySelectorAll('td'));
  const target = cells.find((c) => c.textContent?.includes('80'));
  fireEvent.click(target ?? cells[0]);
}

let symbolSnapshot: string;

beforeEach(() => {
  symbolSnapshot = useTradeStore.getState().selectedSymbol;
  useTradeStore.setState({ selectedSymbol: 'NIFTY 50' } as never);
  invokeMock.mockReset();
});

afterEach(() => {
  cleanup();
  useTradeStore.setState({ selectedSymbol: symbolSnapshot } as never);
  vi.clearAllMocks();
});

describe('FnoOptionChainTable — the ladder resolves a real contract', () => {
  it('asks the resolver for the real tradingsymbol, with no Tauri gate', async () => {
    invokeMock.mockResolvedValue({ tradingsymbol: 'NIFTY26SEP24100CE' });
    const { container } = render(
      <FnoOptionChainTable viewState={view()} fnoExpiry="" expiries={[]} />,
    );

    clickACall(container);
    await vi.waitFor(() => expect(invokeMock).toHaveBeenCalled());

    // `window.__TAURI_INTERNALS__` is absent under jsdom — exactly the browser
    // case that used to skip this call entirely.
    expect(invokeMock).toHaveBeenCalledWith(
      'fno_resolve_option_contract',
      expect.objectContaining({ underlying: 'NIFTY', optionType: 'CE' }),
    );
  });

  it('charts the resolved tradingsymbol, not a fabricated short symbol', async () => {
    invokeMock.mockResolvedValue({ tradingsymbol: 'NIFTY26SEP24100CE' });
    const { container } = render(
      <FnoOptionChainTable viewState={view()} fnoExpiry="" expiries={[]} />,
    );

    clickACall(container);
    await vi.waitFor(() =>
      expect(useTradeStore.getState().selectedSymbol).toBe('NIFTY26SEP24100CE'),
    );
    // The old fabricated form carried no expiry and could never resolve.
    expect(useTradeStore.getState().selectedSymbol).not.toMatch(/^NIFTY\d+CE$/);
  });

  it('leaves the chart alone when no contract is listed at that strike', async () => {
    // The adapter returns null for an unresolvable strike by contract. Pointing
    // the chart at a guess would be fabricated data.
    invokeMock.mockResolvedValue(null);
    const { container } = render(
      <FnoOptionChainTable viewState={view()} fnoExpiry="" expiries={[]} />,
    );

    clickACall(container);
    await vi.waitFor(() => expect(invokeMock).toHaveBeenCalled());
    expect(useTradeStore.getState().selectedSymbol).toBe('NIFTY 50');
  });

  it('leaves the chart alone when the resolver rejects', async () => {
    invokeMock.mockRejectedValue(new Error('questdb down'));
    const { container } = render(
      <FnoOptionChainTable viewState={view()} fnoExpiry="" expiries={[]} />,
    );

    clickACall(container);
    await vi.waitFor(() => expect(invokeMock).toHaveBeenCalled());
    expect(useTradeStore.getState().selectedSymbol).toBe('NIFTY 50');
  });
});
