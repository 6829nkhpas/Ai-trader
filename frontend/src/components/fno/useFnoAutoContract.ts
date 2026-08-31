'use client';

/**
 * useFnoAutoContract — makes sure the charted F&O symbol is one the exchange
 * actually lists.
 *
 * Two jobs, both keyed off `selectedSymbol`:
 *
 *  1. RESOLVE — the user entered F&O with a non-contract symbol selected
 *     (`RELIANCE` equity, `NIFTY 50` index), so substitute the nearest CE/PE.
 *  2. REPAIR — the symbol IS contract-shaped but is not listed, so replace it
 *     with the closest listed contract. `isFnoSymbol` is only a shape test (ends
 *     in CE/PE, contains a digit), so it accepts both a fabricated short symbol
 *     (`BANKNIFTY57000CE` — no expiry segment, written by an older ladder) and an
 *     expired one (`BANKNIFTY26AUG57000CE` after 25 Aug). Either charts as "No
 *     data here" forever, and because `selectedSymbol` is persisted to
 *     preferences, a bad one survives every reload. Job 1 could not help: it
 *     bails on anything contract-shaped, so nothing repaired these.
 *
 * Both fire ONCE per symbol via refs, so writing the resolved tradingsymbol back
 * into `selectedSymbol` does not loop. A LISTED contract is never touched — the
 * repair only runs when `fno_symbol_is_listed` says no, which is what keeps a
 * deliberate far-expiry pick (say the October contract) from being dragged back
 * to the nearest expiry.
 */

import { useEffect, useRef } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { isFnoSymbol } from '../../charting/symbolUtils';
import { bridgeInvoke } from '../../lib/bridge';
import {
  getUnderlyingFromSymbol,
  getStrikeFromSymbol,
  getOptionTypeFromSymbol,
} from './symbolParser';

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

  // Guard refs: when we write a contract tradingsymbol into `selectedSymbol`,
  // the effect fires again — these short-circuit that re-run so neither the
  // resolver nor the listing check is called twice for the same symbol.
  const lastResolvedRef = useRef<string>('');
  const lastCheckedRef = useRef<string>('');

  useEffect(() => {
    if (activeProfile !== 'FNO') {
      lastResolvedRef.current = '';
      lastCheckedRef.current = '';
      return;
    }
    if (!selectedSymbol) return;

    let cancelled = false;

    if (isFnoSymbol(selectedSymbol)) {
      const side = getOptionTypeFromSymbol(selectedSymbol);
      // Only an OPTION contract can be checked. `fno_symbol_is_listed` reads the
      // option chain, which holds no futures rows, so a perfectly good
      // `RELIANCE26AUGFUT` would come back "not listed" and get swapped for a
      // CE/PE. Futures are left exactly as selected.
      if (!side) return;
      if (lastCheckedRef.current === selectedSymbol) return;
      lastCheckedRef.current = selectedSymbol;

      (async () => {
        try {
          const listed = await bridgeInvoke<boolean>('fno_symbol_is_listed', {
            symbol: selectedSymbol,
          });
          if (cancelled || listed) return;

          const underlying = getUnderlyingFromSymbol(selectedSymbol);
          if (!underlying) return;
          const strike = getStrikeFromSymbol(selectedSymbol);

          // Keep the strike and side the user picked when the strike can be read
          // off the symbol; drop to the ATM contract only when it cannot. No
          // expiry is passed, so both commands answer with the nearest LIVE one.
          const resolved =
            strike != null
              ? await bridgeInvoke<ResolvedContract | null>('fno_resolve_option_contract', {
                  underlying,
                  strike,
                  optionType: side,
                })
              : await bridgeInvoke<ResolvedContract | null>('fno_resolve_nearest_contract', {
                  underlying,
                });
          if (cancelled) return;
          // No listed contract to move to ⇒ leave the chart alone rather than
          // invent a symbol. The panel already shows its own empty state.
          if (!resolved?.tradingsymbol || resolved.tradingsymbol === selectedSymbol) return;

          lastCheckedRef.current = resolved.tradingsymbol;
          setFnoUnderlying(underlying);
          setSelectedSymbol(resolved.tradingsymbol);
        } catch (err) {
          console.warn('[useFnoAutoContract] listing check failed:', err);
        }
      })();

      return () => {
        cancelled = true;
      };
    }

    if (lastResolvedRef.current === selectedSymbol) return;

    (async () => {
      try {
        const resolved = await bridgeInvoke<ResolvedContract | null>(
          'fno_resolve_nearest_contract',
          { underlying: selectedSymbol },
        );
        if (cancelled) return;
        if (resolved?.tradingsymbol) {
          lastResolvedRef.current = selectedSymbol;
          // The resolver only ever answers with a listed contract, so mark it
          // checked and spare the effect a pointless listing query on re-run.
          lastCheckedRef.current = resolved.tradingsymbol;
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
