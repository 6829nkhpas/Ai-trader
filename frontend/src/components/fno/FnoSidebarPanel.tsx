'use client';

import React, { useState, useEffect, useMemo, useRef } from 'react';
import { Loader2, Activity, RefreshCw, ChevronDown } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { useTradeStore } from '../../store/useTradeStore';
import {
  toFnoViewState,
  type FnoChains,
  type FnoPayload,
  type FnoUnavailableMarker,
  type FnoViewState,
} from './viewModel';
import { deriveUnderlyingOptions, deriveExpiryOptions } from './selectors';
import {
  getUnderlyingFromSymbol,
  matchExpiryFromSymbol,
  getStrikeFromSymbol,
  getOptionTypeFromSymbol,
} from './symbolParser';
import FnoOptionChainTable from './FnoOptionChainTable';

type FnoSnapshot = FnoPayload | FnoUnavailableMarker;

export default function FnoSidebarPanel() {
  const fnoUnderlying = useTradeStore((s) => s.fnoUnderlying);
  const fnoExpiry = useTradeStore((s) => s.fnoExpiry);
  const setFnoUnderlying = useTradeStore((s) => s.setFnoUnderlying);
  const setFnoExpiry = useTradeStore((s) => s.setFnoExpiry);

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
  const expiries = useMemo(() => deriveExpiryOptions(chains, fnoUnderlying), [chains, fnoUnderlying]);

  // Auto-sync underlying & expiry when user selects an option contract in the app
  useEffect(() => {
    if (!selectedSymbol || selectedSymbol === prevSymbolRef.current) return;
    prevSymbolRef.current = selectedSymbol;

    const extractedUnderlying = getUnderlyingFromSymbol(selectedSymbol);
    if (extractedUnderlying) {
      setFnoUnderlying(extractedUnderlying);
    }
  }, [selectedSymbol, setFnoUnderlying]);

  useEffect(() => {
    if (!selectedSymbol || !expiries.length) return;
    const matched = matchExpiryFromSymbol(selectedSymbol, expiries);
    if (matched && matched !== fnoExpiry) {
      setFnoExpiry(matched);
    }
  }, [selectedSymbol, expiries, fnoExpiry, setFnoExpiry]);

  // Initial fetch of available chains
  useEffect(() => {
    invoke<FnoChains>('fno_list_chains')
      .then((c) => setChains(c))
      .catch(() => setChains(null));
  }, []);

  // Poll / subscribe to snapshot updates
  useEffect(() => {
    let cancelled = false;
    let unlisten: UnlistenFn | undefined;

    (async () => {
      try {
        unlisten = await listen<FnoSnapshot>('fno-snapshot', (event) => {
          if (!cancelled && event.payload) {
            setViewState(toFnoViewState(event.payload));
          }
        });
      } catch { /* not in Tauri */ }

      setLoading(true);
      try {
        const payload = await invoke<FnoSnapshot>('get_fno_analytics', {
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
        await invoke('fno_subscribe', { underlying: fnoUnderlying, expiry: fnoExpiry });
      } catch { /* not in Tauri */ }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
      invoke('fno_unsubscribe').catch(() => {});
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
        <div className="flex items-center justify-between font-sans">
          <span className="text-[11px] font-black uppercase tracking-widest text-text-muted dark:text-zinc-300 font-sans">TRACKING</span>
          <span className="text-[13px] font-sans font-black text-cyan-600 dark:text-cyan-400 truncate max-w-56">
            {selectedSymbol || '—'}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-3">
          {/* Custom Underlying Dropdown */}
          <div className="flex flex-col gap-1.5 relative" ref={underlyingRef}>
            <span className="text-[11px] font-black uppercase tracking-widest text-text-muted dark:text-zinc-300 font-sans">UNDERLYING</span>
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
            <span className="text-[11px] font-black uppercase tracking-widest text-text-muted dark:text-zinc-300 font-sans">EXPIRY</span>
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
                          setFnoExpiry(value);
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
        <div className="flex h-40 items-center justify-center gap-2 text-text-secondary bg-surface/20">
          <Loader2 size={14} className="animate-spin" />
          <span className="text-[11px] font-semibold uppercase tracking-wider">Loading F&amp;O…</span>
        </div>
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
              invoke<FnoSnapshot>('get_fno_analytics', { underlying: fnoUnderlying, expiry: fnoExpiry })
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
        <div className="flex flex-col">
          <FnoOptionChainTable
            viewState={viewState as FnoViewState & { kind: 'ready' | 'partial' }}
            highlightedStrike={highlightedStrike}
            highlightedSide={highlightedSide}
            fnoExpiry={fnoExpiry}
            expiries={expiries}
            onExpiryChange={setFnoExpiry}
          />
        </div>
      )}
    </div>
  );
}
