'use client';

/**
 * useFnoExpiryChange — returns a handler for the expiry dropdowns that both
 * updates `fnoExpiry` AND re-opens the chart for the newly selected expiry.
 *
 * When a CE/PE contract is already charted, it re-resolves the SAME strike + side
 * on the chosen expiry (`fno_resolve_option_contract`). When nothing tradable is
 * charted yet, it falls back to the ATM contract for that expiry
 * (`fno_resolve_nearest_contract`). If resolution fails it leaves the chart as-is
 * and only updates the expiry, so the dropdown never dead-ends.
 */

import { useCallback } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { getStrikeFromSymbol, getOptionTypeFromSymbol } from './symbolParser';
import { bridgeInvoke } from '../../lib/bridge';

interface ResolvedContract {
  tradingsymbol: string;
  underlying: string;
  expiry: string;
  strike: number;
  option_type: string;
}

export function useFnoExpiryChange(): (expiry: string) => void {
  const fnoUnderlying = useTradeStore((s) => s.fnoUnderlying);
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const setFnoExpiry = useTradeStore((s) => s.setFnoExpiry);
  const setSelectedSymbol = useTradeStore((s) => s.setSelectedSymbol);

  return useCallback(
    (expiry: string) => {
      setFnoExpiry(expiry);
      if (!fnoUnderlying) return;
      // No `__TAURI_INTERNALS__` gate.
      //
      // This used to return here in any browser, so picking an expiry set the
      // store field and nothing else: the chart kept showing the old contract,
      // and — because `FnoSidebarPanel` re-derives the expiry FROM the charted
      // symbol — the dropdown then snapped straight back to the old date. That is
      // the reported "cannot change the expiry". `fno_resolve_option_contract`
      // and `fno_resolve_nearest_contract` are both implemented for the web in
      // `webAdapters.ts`, so the resolve below works in either runtime.

      const strike = getStrikeFromSymbol(selectedSymbol);
      const side = getOptionTypeFromSymbol(selectedSymbol);

      const resolve =
        strike != null && side
          ? bridgeInvoke<ResolvedContract | null>('fno_resolve_option_contract', {
              underlying: fnoUnderlying,
              strike,
              optionType: side,
              expiry: expiry || null,
            })
          : bridgeInvoke<ResolvedContract | null>('fno_resolve_nearest_contract', {
              underlying: fnoUnderlying,
              expiry: expiry || null,
            });

      resolve
        .then((resolved) => {
          if (resolved?.tradingsymbol) setSelectedSymbol(resolved.tradingsymbol);
        })
        .catch((err) => console.warn('[useFnoExpiryChange] resolve failed:', err));
    },
    [fnoUnderlying, selectedSymbol, setFnoExpiry, setSelectedSymbol],
  );
}
