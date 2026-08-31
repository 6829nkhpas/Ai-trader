// @vitest-environment jsdom
//
// "I clicked the box but no chart opens."
//
// The chart header read `BANKNIFTY57000CE · 10 · NFO` above "No data here". That
// symbol is fabricated — a real NFO symbol carries an expiry segment
// (`BANKNIFTY26SEP57000CE`, which returns 234 candles where the short form
// returns 0). An older ladder wrote it, and because `selectedSymbol` is persisted
// to `stratai.preferences`, it came back on every single load.
//
// Nothing repaired it: `useFnoAutoContract` skipped anything `isFnoSymbol`
// accepted, and that is a shape test (ends CE/PE, has a digit) which the
// fabricated symbol passes. Same story for an EXPIRED contract that was charted
// before its expiry passed.
//
// So the hook now asks whether the symbol is actually listed, and only repairs
// when the answer is no.
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock('@/lib/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/bridge')>()),
  bridgeInvoke: invokeMock,
}));

import { useFnoAutoContract } from '../useFnoAutoContract';
import { useTradeStore } from '@/store/useTradeStore';

const KEYS = ['activeProfile', 'selectedSymbol', 'fnoUnderlying'] as const;
let snapshot: Record<string, unknown>;

/** Answers the bridge per command; anything unlisted here resolves null. */
function bridge(handlers: Record<string, unknown>): void {
  invokeMock.mockImplementation((command: string) =>
    Promise.resolve(command in handlers ? handlers[command] : null),
  );
}

beforeEach(() => {
  const s = useTradeStore.getState() as unknown as Record<string, unknown>;
  snapshot = Object.fromEntries(KEYS.map((k) => [k, s[k]]));
  invokeMock.mockReset();
});

afterEach(() => {
  useTradeStore.setState(snapshot as never);
  vi.clearAllMocks();
});

function enterFno(symbol: string): void {
  useTradeStore.setState({ activeProfile: 'FNO', selectedSymbol: symbol } as never);
}

describe('useFnoAutoContract — repairing an unlisted contract', () => {
  it('replaces a fabricated short symbol with the listed contract at the same strike', async () => {
    enterFno('BANKNIFTY57000CE');
    bridge({
      fno_symbol_is_listed: false,
      fno_resolve_option_contract: {
        tradingsymbol: 'BANKNIFTY26SEP57000CE',
        underlying: 'BANKNIFTY',
        expiry: '2026-09-29',
        strike: 57000,
        option_type: 'CE',
      },
    });

    renderHook(() => useFnoAutoContract());

    await vi.waitFor(() =>
      expect(useTradeStore.getState().selectedSymbol).toBe('BANKNIFTY26SEP57000CE'),
    );
    // The user's strike and side are kept — repair, not a reset to ATM.
    expect(invokeMock).toHaveBeenCalledWith(
      'fno_resolve_option_contract',
      expect.objectContaining({ underlying: 'BANKNIFTY', strike: 57000, optionType: 'CE' }),
    );
    expect(useTradeStore.getState().fnoUnderlying).toBe('BANKNIFTY');
  });

  it('replaces an expired contract', async () => {
    // `fno_symbol_is_listed` filters to live expiries, so a contract that was
    // valid last month answers false and gets rolled forward.
    enterFno('BANKNIFTY26AUG57000CE');
    bridge({
      fno_symbol_is_listed: false,
      fno_resolve_option_contract: { tradingsymbol: 'BANKNIFTY26SEP57000CE' },
    });

    renderHook(() => useFnoAutoContract());

    await vi.waitFor(() =>
      expect(useTradeStore.getState().selectedSymbol).toBe('BANKNIFTY26SEP57000CE'),
    );
  });

  it('leaves a listed contract alone, including a deliberate far expiry', async () => {
    // The reason the check exists rather than always re-resolving: a user who
    // picked October must not be dragged back to the nearest expiry.
    enterFno('BANKNIFTY26OCT57000CE');
    bridge({
      fno_symbol_is_listed: true,
      fno_resolve_option_contract: { tradingsymbol: 'BANKNIFTY26SEP57000CE' },
    });

    renderHook(() => useFnoAutoContract());

    await vi.waitFor(() => expect(invokeMock).toHaveBeenCalledWith('fno_symbol_is_listed', expect.anything()));
    expect(invokeMock.mock.calls.map((c) => c[0])).not.toContain('fno_resolve_option_contract');
    expect(useTradeStore.getState().selectedSymbol).toBe('BANKNIFTY26OCT57000CE');
  });

  it('never questions a futures symbol', async () => {
    // The listing check reads the option chain, which has no futures rows, so
    // asking it about a FUT would always answer "unlisted" and swap in a CE/PE.
    enterFno('RELIANCE26AUGFUT');
    bridge({ fno_symbol_is_listed: false, fno_resolve_nearest_contract: { tradingsymbol: 'X' } });

    renderHook(() => useFnoAutoContract());

    await new Promise((r) => setTimeout(r, 0));
    expect(invokeMock).not.toHaveBeenCalled();
    expect(useTradeStore.getState().selectedSymbol).toBe('RELIANCE26AUGFUT');
  });

  it('leaves the chart alone when nothing listed can be resolved', async () => {
    // Inventing a symbol is what caused this bug in the first place.
    enterFno('BANKNIFTY57000CE');
    bridge({ fno_symbol_is_listed: false });

    renderHook(() => useFnoAutoContract());

    await vi.waitFor(() =>
      expect(invokeMock).toHaveBeenCalledWith('fno_resolve_option_contract', expect.anything()),
    );
    expect(useTradeStore.getState().selectedSymbol).toBe('BANKNIFTY57000CE');
  });

  it('still resolves a contract when a plain underlying is charted', async () => {
    enterFno('NIFTY 50');
    bridge({ fno_resolve_nearest_contract: { tradingsymbol: 'NIFTY2690124000CE' } });

    renderHook(() => useFnoAutoContract());

    await vi.waitFor(() =>
      expect(useTradeStore.getState().selectedSymbol).toBe('NIFTY2690124000CE'),
    );
    expect(invokeMock.mock.calls.map((c) => c[0])).not.toContain('fno_symbol_is_listed');
  });

  it('does nothing outside F&O', async () => {
    useTradeStore.setState({
      activeProfile: 'INTRADAY',
      selectedSymbol: 'BANKNIFTY57000CE',
    } as never);
    bridge({ fno_symbol_is_listed: false });

    renderHook(() => useFnoAutoContract());

    await new Promise((r) => setTimeout(r, 0));
    expect(invokeMock).not.toHaveBeenCalled();
  });
});
