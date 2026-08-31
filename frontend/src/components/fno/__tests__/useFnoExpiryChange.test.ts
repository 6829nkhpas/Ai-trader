// @vitest-environment jsdom
//
// Why the expiry could not be changed.
//
// Two defects compounded:
//
//   1. `useFnoExpiryChange` returned early on `!('__TAURI_INTERNALS__' in window)`,
//      so in a browser picking an expiry set the store field and nothing else —
//      the chart kept showing the old contract.
//   2. Because the chart never moved, `FnoSidebarPanel`'s "sync the expiry from
//      the charted symbol" effect re-derived the OLD expiry and wrote it straight
//      back, so the dropdown visibly reverted and the pick "did not take".
//
// This file covers (1): the resolver must run in a browser and move the chart to
// the same strike/side on the newly chosen expiry. `fno_resolve_option_contract`
// and `fno_resolve_nearest_contract` are both implemented for the web.
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn() }));
vi.mock('@/lib/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/bridge')>()),
  bridgeInvoke: invokeMock,
}));

import { useFnoExpiryChange } from '../useFnoExpiryChange';
import { useTradeStore } from '@/store/useTradeStore';

const KEYS = ['fnoUnderlying', 'fnoExpiry', 'selectedSymbol'] as const;
let snapshot: Record<string, unknown>;

beforeEach(() => {
  const s = useTradeStore.getState() as unknown as Record<string, unknown>;
  snapshot = Object.fromEntries(KEYS.map((k) => [k, s[k]]));
  invokeMock.mockReset();
});

afterEach(() => {
  useTradeStore.setState(snapshot as never);
  vi.clearAllMocks();
});

describe('useFnoExpiryChange', () => {
  it('records the chosen expiry in the store', () => {
    useTradeStore.setState({ fnoUnderlying: 'NIFTY', selectedSymbol: 'NIFTY26SEP24100CE' } as never);
    invokeMock.mockResolvedValue(null);

    const { result } = renderHook(() => useFnoExpiryChange());
    result.current('2026-10-29');

    expect(useTradeStore.getState().fnoExpiry).toBe('2026-10-29');
  });

  it('re-resolves the SAME strike and side on the new expiry — in a browser', async () => {
    // jsdom has no __TAURI_INTERNALS__, which is precisely the case the old gate
    // bailed out of.
    useTradeStore.setState({ fnoUnderlying: 'NIFTY', selectedSymbol: 'NIFTY26SEP24100CE' } as never);
    invokeMock.mockResolvedValue({ tradingsymbol: 'NIFTY26OCT24100CE' });

    const { result } = renderHook(() => useFnoExpiryChange());
    result.current('2026-10-29');

    await vi.waitFor(() => expect(invokeMock).toHaveBeenCalled());
    expect(invokeMock).toHaveBeenCalledWith(
      'fno_resolve_option_contract',
      expect.objectContaining({
        underlying: 'NIFTY',
        strike: 24100,
        optionType: 'CE',
        expiry: '2026-10-29',
      }),
    );
    // The chart follows the expiry — without this the dropdown snapped back.
    await vi.waitFor(() =>
      expect(useTradeStore.getState().selectedSymbol).toBe('NIFTY26OCT24100CE'),
    );
  });

  it('falls back to the nearest contract when no strike/side can be parsed', async () => {
    // Charting a plain index rather than a contract: there is no strike to keep,
    // so the nearest contract of the chosen expiry is the honest target.
    useTradeStore.setState({ fnoUnderlying: 'NIFTY', selectedSymbol: 'NIFTY 50' } as never);
    invokeMock.mockResolvedValue({ tradingsymbol: 'NIFTY26OCT24000CE' });

    const { result } = renderHook(() => useFnoExpiryChange());
    result.current('2026-10-29');

    await vi.waitFor(() => expect(invokeMock).toHaveBeenCalled());
    expect(invokeMock.mock.calls[0][0]).toBe('fno_resolve_nearest_contract');
  });

  it('does not resolve when there is no underlying yet', () => {
    useTradeStore.setState({ fnoUnderlying: '', selectedSymbol: 'NIFTY 50' } as never);

    const { result } = renderHook(() => useFnoExpiryChange());
    result.current('2026-10-29');

    // Still records the choice, but has nothing to resolve against.
    expect(useTradeStore.getState().fnoExpiry).toBe('2026-10-29');
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it('leaves the chart alone when the resolver finds nothing', async () => {
    useTradeStore.setState({ fnoUnderlying: 'NIFTY', selectedSymbol: 'NIFTY26SEP24100CE' } as never);
    invokeMock.mockResolvedValue(null);

    const { result } = renderHook(() => useFnoExpiryChange());
    result.current('2026-10-29');

    await vi.waitFor(() => expect(invokeMock).toHaveBeenCalled());
    expect(useTradeStore.getState().selectedSymbol).toBe('NIFTY26SEP24100CE');
  });
});
