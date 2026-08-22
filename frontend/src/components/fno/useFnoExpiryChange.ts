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
      if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return;

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
