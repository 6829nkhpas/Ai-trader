'use client';

/**
 * useFnoAutoContract — auto-resolves a tradable F&O contract for the active
 * `selectedSymbol` when the user enters F&O mode with a non-contract symbol
 * selected.
 *
 * Fires ONCE per (underlying, profile) pair so it does NOT loop when it writes
 * the resolved tradingsymbol back into `selectedSymbol`. Skips cleanly when
 * `selectedSymbol` is already a CE/PE/FUT contract (so an explicit contract
 * click is never overridden).
 */

import { useEffect, useRef } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { isFnoSymbol } from '../../charting/symbolUtils';
import { bridgeInvoke } from '../../lib/bridge';

interface ResolvedContract {
  tradingsymbol: string;
  underlying: string;
  expiry: string;
  strike: number;
  option_type: string;
}

export function useFnoAutoContract(): void {
  const activeProfile = useTradeStore((s) => s.activeProfile);
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const setSelectedSymbol = useTradeStore((s) => s.setSelectedSymbol);
  const setFnoUnderlying = useTradeStore((s) => s.setFnoUnderlying);

  // Guard ref: when we write a contract tradingsymbol into `selectedSymbol`,
  // the effect fires again — the resolved-in flag short-circuits that re-run so
  // we never call the resolver twice for the same underlying.
  const lastResolvedRef = useRef<string>('');

  useEffect(() => {
    if (activeProfile !== 'FNO') {
      lastResolvedRef.current = '';
      return;
    }
    if (!selectedSymbol) return;
    if (isFnoSymbol(selectedSymbol)) return; // already a contract — leave it
    if (lastResolvedRef.current === selectedSymbol) return;

    let cancelled = false;
    (async () => {
      try {
        const resolved = await bridgeInvoke<ResolvedContract | null>(
          'fno_resolve_nearest_contract',
          { underlying: selectedSymbol },
        );
        if (cancelled) return;
        if (resolved?.tradingsymbol) {
          lastResolvedRef.current = selectedSymbol;
          setFnoUnderlying(selectedSymbol);
          setSelectedSymbol(resolved.tradingsymbol);
        }
      } catch (err) {
        console.warn('[useFnoAutoContract] resolve failed:', err);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [activeProfile, selectedSymbol, setSelectedSymbol, setFnoUnderlying]);
}
