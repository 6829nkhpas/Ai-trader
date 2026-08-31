'use client';

import React, { useState, useEffect, useMemo, useRef } from 'react';
import { Loader2, Activity, RefreshCw, ChevronDown } from 'lucide-react';
import FnoSkeleton from './FnoSkeleton';
import { useTradeStore } from '../../store/useTradeStore';
import {
  toFnoViewState,
  type FnoChains,
  type FnoPayload,
  type FnoUnavailableMarker,
  type FnoViewState,
} from './viewModel';
import { deriveUnderlyingOptions } from './selectors';
import { isFnoSymbol } from '../../charting/symbolUtils';
import {
  getUnderlyingFromSymbol,
  matchExpiryFromSymbol,
  getStrikeFromSymbol,
  getOptionTypeFromSymbol,
} from './symbolParser';
import FnoOptionChainTable from './FnoOptionChainTable';
import { useFnoExpiryChange } from './useFnoExpiryChange';
import { bridgeInvoke, bridgeListen, type UnlistenFn } from '../../lib/bridge';

type FnoSnapshot = FnoPayload | FnoUnavailableMarker;

export default function FnoSidebarPanel() {
  const fnoUnderlying = useTradeStore((s) => s.fnoUnderlying);
  const fnoExpiry = useTradeStore((s) => s.fnoExpiry);
  const setFnoUnderlying = useTradeStore((s) => s.setFnoUnderlying);
  const setFnoExpiry = useTradeStore((s) => s.setFnoExpiry);

  // Selecting an expiry re-charts the same strike+side contract of the new expiry.
  const handleExpiryChange = useFnoExpiryChange();

  const [chains, setChains] = useState<FnoChains | null>(null);
  const [viewState, setViewState] = useState<FnoViewState | null>(null);
  const [loading, setLoading] = useState(true);

  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const prevSymbolRef = useRef('');

  // Extract highlighting from currently active symbol
  const highlightedStrike = useMemo(() => getStrikeFromSymbol(selectedSymbol), [selectedSymbol]);
  const highlightedSide = useMemo(() => getOptionTypeFromSymbol(selectedSymbol), [selectedSymbol]);

  // Derive dropdown options
  const underlyings = useMemo(() => deriveUnderlyingOptions(chains), [chains]);

  // Expiries are fetched per-underlying on demand so a stock selected from
  // search shows its real expiry dates immediately (fno_list_chains only maps
  // the configured indexes + already-registered underlyings).
  const [expiries, setExpiries] = useState<string[]>([]);
  useEffect(() => {
    if (!fnoUnderlying) {
      setExpiries([]);
      return;
    }
    let cancelled = false;
    bridgeInvoke<string[]>('fno_list_expiries', { underlying: fnoUnderlying })
      .then((e) => { if (!cancelled) setExpiries(Array.isArray(e) ? e : []); })
      .catch(() => { if (!cancelled) setExpiries([]); });
    return () => { cancelled = true; };
  }, [fnoUnderlying]);

  // Auto-sync the underlying when the user charts a different contract/symbol.
  //
  // Only adopts an underlying that actually HAS a live chain. It used to adopt
  // whatever `getUnderlyingFromSymbol` returned, and that helper echoes any
  // unrecognised name straight back — so charting a cash equity pointed the whole
  // F&O panel at, say, `RELIANCE`, which usually has no rows in
  // `option_chain_snapshots`. The panel then reported "no chain snapshot" and the
  // expiry list came back empty, leaving the expiry dropdown with nothing to pick
  // but "Nearest". Where the chain list is not loaded yet, an F&O contract symbol
  // is still trusted (its underlying is a real chain by construction); anything
  // else waits, and `FnoSection`'s seeding effect resolves a real underlying.
  useEffect(() => {
    if (!selectedSymbol || selectedSymbol === prevSymbolRef.current) return;

    const extractedUnderlying = getUnderlyingFromSymbol(selectedSymbol);
    if (!extractedUnderlying) return;

    const available = chains?.underlyings ?? [];
    const known = available.some(
      (u) => u.toUpperCase() === extractedUnderlying.toUpperCase(),
    );
    if (available.length > 0 && !known && !isFnoSymbol(selectedSymbol)) return;

    prevSymbolRef.current = selectedSymbol;
    setFnoUnderlying(extractedUnderlying);
  }, [selectedSymbol, chains, setFnoUnderlying]);

  // Sync the expiry FROM the charted contract — but only when the contract
  // actually changes, never in response to the expiry itself changing.
  //
  // This effect used to list `fnoExpiry` in its deps and compare against it, which
  // made it fight the user: picking 2025-01-30 called `setFnoExpiry`, which
  // re-ran this effect, which re-derived the expiry from the UNCHANGED
  // `selectedSymbol` (still the old contract), found a mismatch, and wrote the old
  // date back. The dropdown visibly reverted and the choice "did not take" — the
  // reported bug. It only appeared on the web because `useFnoExpiryChange` bailed
  // early there and never moved the chart, so the symbol never caught up.
  //
  // Keyed off the symbol via a ref instead: one sync per contract change, and a
  // user's expiry pick is left alone until the chart follows it.
  const lastExpirySyncedSymbolRef = useRef('');
  useEffect(() => {
    if (!selectedSymbol || !expiries.length) return;
    if (selectedSymbol === lastExpirySyncedSymbolRef.current) return;
    lastExpirySyncedSymbolRef.current = selectedSymbol;
    const matched = matchExpiryFromSymbol(selectedSymbol, expiries);
    if (matched) {
      setFnoExpiry(matched);
    }
  }, [selectedSymbol, expiries, setFnoExpiry]);

  // Initial fetch of available chains
  useEffect(() => {
    bridgeInvoke<FnoChains>('fno_list_chains')
      .then((c) => setChains(c))
      .catch(() => setChains(null));
  }, []);

  // Poll / subscribe to snapshot updates
  useEffect(() => {
    let cancelled = false;
    let unlisten: UnlistenFn | undefined;

    (async () => {
      try {
        unlisten = await bridgeListen<FnoSnapshot>('fno-snapshot', (event) => {
          if (!cancelled && event.payload) {
            setViewState(toFnoViewState(event.payload));
          }
        });
      } catch { /* not in Tauri */ }

      // Same guard as `FnoSection`: a blank underlying fails
      // `get_fno_analytics`'s own argument check before any request is made, and
      // that rejection used to surface as an F&O service error on every cold load.
      // `FnoSection` seeds the underlying from the live chain list; until it does,
      // stay in the loading state. (The panel also renders its own
      // "Select a symbol…" placeholder while `fnoUnderlying` is empty.)
      if (!fnoUnderlying) {
        if (!cancelled) setLoading(false);
        return;
      }
      setLoading(true);
      try {
        const payload = await bridgeInvoke<FnoSnapshot>('get_fno_analytics', {
          underlying: fnoUnderlying,
          expiry: fnoExpiry,
        });
        if (!cancelled) setViewState(toFnoViewState(payload));
      } catch (err) {
        if (!cancelled) {
          setViewState({
            kind: 'service-error',
            detail: err instanceof Error ? err.message : String(err),
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
      try {
        await bridgeInvoke('fno_subscribe', { underlying: fnoUnderlying, expiry: fnoExpiry });
      } catch { /* not in Tauri */ }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
      bridgeInvoke('fno_unsubscribe').catch(() => {});
    };
  }, [fnoUnderlying, fnoExpiry]);

  const msg = viewState?.kind === 'unavailable'
    ? viewState.reason
    : viewState?.kind === 'service-error'
      ? viewState.detail
      : 'F&O data unavailable. Ensure the F&O service is running.';

  const [isUnderlyingOpen, setIsUnderlyingOpen] = useState(false);
  const [isExpiryOpen, setIsExpiryOpen] = useState(false);
  const underlyingRef = useRef<HTMLDivElement>(null);
  const expiryRef = useRef<HTMLDivElement>(null);

  // Close dropdowns on click outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (underlyingRef.current && !underlyingRef.current.contains(event.target as Node)) {
        setIsUnderlyingOpen(false);
      }
      if (expiryRef.current && !expiryRef.current.contains(event.target as Node)) {
        setIsExpiryOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div className="flex flex-col gap-0 bg-surface dark:bg-black text-text-primary h-full font-sans select-none">
      {/* ── Selectors + status bar: Custom Popover Theme Adaptive ── */}
      <div className="flex flex-col gap-3.5 px-4 py-3.5 bg-surface dark:bg-black border-b border-border-default/40 dark:border-zinc-800/80 font-sans">
        {/* Tracking indicator */}
        {/* <div className="flex items-center justify-between font-sans">
          <span className="text-[11px] font-black uppercase tracking-widest text-text-muted dark:text-zinc-300 font-sans">TRACKING</span>
          <span className="text-[13px] font-sans font-black text-cyan-600 dark:text-cyan-400 truncate max-w-56">
            {selectedSymbol || '—'}
          </span>
        </div> */}

        <div className="grid grid-cols-2 gap-3">
          {/* Custom Underlying Dropdown */}
          <div className="flex flex-col gap-1.5 relative" ref={underlyingRef}>
            {/* <span className="text-[11px] font-black uppercase tracking-widest text-text-muted dark:text-zinc-300 font-sans">UNDERLYING</span> */}
            <button
              type="button"
              onClick={() => setIsUnderlyingOpen(!isUnderlyingOpen)}
              className="w-full flex items-center justify-between rounded border border-border-default dark:border-zinc-700 bg-surface dark:bg-black pl-3 pr-2.5 py-2 text-[14px] font-black text-text-primary dark:text-white focus:outline-none focus:border-color-primary cursor-pointer transition-colors"
            >
              <span className="truncate">{fnoUnderlying || 'NIFTY'}</span>
              <ChevronDown className={`transition-transform duration-200 text-text-muted dark:text-zinc-300 ${isUnderlyingOpen ? 'rotate-180' : ''}`} size={14} />
            </button>

            {/* Custom Popover Menu */}
            {isUnderlyingOpen && (
              <div className="absolute top-full left-0 z-50 mt-1.5 w-64 rounded-xl bg-card dark:bg-[#12141a] border border-border-default/80 dark:border-zinc-800 shadow-2xl p-1.5 overflow-hidden font-sans">
                <div className="flex flex-col max-h-72 overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
                  {underlyings.map((u) => {
                    const isSelected = u === fnoUnderlying;
                    return (
                      <button
                        key={u}
                        type="button"
                        onClick={() => {
                          setFnoUnderlying(u);
                          setIsUnderlyingOpen(false);
                        }}
                        className={`flex items-center gap-3.5 w-full px-3.5 py-3 text-left transition-colors border-b border-border-default/20 dark:border-zinc-800/40 last:border-none hover:bg-elevated/60 dark:hover:bg-white/5 rounded-lg ${
                          isSelected ? 'bg-emerald-500/10 dark:bg-emerald-500/10' : ''
                        }`}
                      >
                        {/* Radio Button Icon */}
                        <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
                          isSelected ? 'border-emerald-500' : 'border-emerald-500/80'
                        }`}>
                          {isSelected && <div className="w-2 h-2 rounded-full bg-emerald-500" />}
                        </div>
                        <span className="text-[13.5px] font-black uppercase tracking-wide text-text-primary dark:text-white">
                          {u}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Custom Expiry Dropdown */}
          <div className="flex flex-col gap-1.5 relative" ref={expiryRef}>
            {/* <span className="text-[11px] font-black uppercase tracking-widest text-text-muted dark:text-zinc-300 font-sans">EXPIRY</span> */}
            <button
              type="button"
              onClick={() => setIsExpiryOpen(!isExpiryOpen)}
              className="w-full flex items-center justify-between rounded border border-border-default dark:border-zinc-700 bg-surface dark:bg-black pl-3 pr-2.5 py-2 text-[14px] font-black text-text-primary dark:text-white focus:outline-none focus:border-color-primary cursor-pointer transition-colors"
            >
              <span className="truncate">{fnoExpiry || 'Nearest'}</span>
              <ChevronDown className={`transition-transform duration-200 text-text-muted dark:text-zinc-300 ${isExpiryOpen ? 'rotate-180' : ''}`} size={14} />
            </button>

            {/* Custom Popover Menu */}
            {isExpiryOpen && (
              <div className="absolute top-full right-0 z-50 mt-1.5 w-56 rounded-xl bg-card dark:bg-[#12141a] border border-border-default/80 dark:border-zinc-800 shadow-2xl p-1.5 overflow-hidden font-sans">
                <div className="flex flex-col max-h-72 overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
                  {['Nearest', ...expiries].map((e) => {
                    const value = e === 'Nearest' ? '' : e;
                    const isSelected = fnoExpiry === value || (e === 'Nearest' && !fnoExpiry);
                    return (
                      <button
                        key={e}
                        type="button"
                        onClick={() => {
                          handleExpiryChange(value);
                          setIsExpiryOpen(false);
                        }}
                        className={`flex items-center gap-3.5 w-full px-3.5 py-3 text-left transition-colors border-b border-border-default/20 dark:border-zinc-800/40 last:border-none hover:bg-elevated/60 dark:hover:bg-white/5 rounded-lg ${
                          isSelected ? 'bg-emerald-500/10 dark:bg-emerald-500/10' : ''
                        }`}
                      >
                        {/* Radio Button Icon */}
                        <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
                          isSelected ? 'border-emerald-500' : 'border-emerald-500/80'
                        }`}>
                          {isSelected && <div className="w-2 h-2 rounded-full bg-emerald-500" />}
                        </div>
                        <span className="text-[13.5px] font-black uppercase tracking-wide text-text-primary dark:text-white">
                          {e}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Conditional content section ── */}
      {!fnoUnderlying ? (
        <div className="flex h-40 items-center justify-center gap-2 text-text-secondary bg-surface/20">
          <Activity size={14} />
          <span className="text-[11px] font-semibold uppercase tracking-wider">Select a symbol…</span>
        </div>
      ) : loading && viewState === null ? (
        <FnoSkeleton />
      ) : !viewState || viewState.kind === 'unavailable' || viewState.kind === 'service-error' ? (
        <div className="flex flex-col gap-3 p-4 bg-surface/20">
          <div className="flex items-center gap-2 rounded border border-amber-500/20 bg-amber-500/5 px-3 py-2">
            <Activity size={12} className="shrink-0 text-amber-400" />
            <p className="text-[11px] text-amber-300 leading-relaxed">{msg}</p>
          </div>
          <button
            type="button"
            onClick={() => {
              setLoading(true);
              setViewState(null);
              bridgeInvoke<FnoSnapshot>('get_fno_analytics', { underlying: fnoUnderlying, expiry: fnoExpiry })
                .then(p => setViewState(toFnoViewState(p)))
                .catch(() => setViewState({ kind: 'service-error', detail: 'Retry failed' }))
                .finally(() => setLoading(false));
            }}
            className="flex items-center justify-center gap-1.5 rounded border border-border-default bg-elevated px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider text-text-secondary hover:bg-elevated/80 transition-colors"
          >
            <RefreshCw size={10} /> Retry
          </button>
        </div>
      ) : (
        <div className="flex flex-col pl-px">
          <FnoOptionChainTable
            viewState={viewState as FnoViewState & { kind: 'ready' | 'partial' }}
            highlightedStrike={highlightedStrike}
            highlightedSide={highlightedSide}
            fnoExpiry={fnoExpiry}
            expiries={expiries}
            onExpiryChange={handleExpiryChange}
          />
        </div>
      )}
    </div>
  );
}
