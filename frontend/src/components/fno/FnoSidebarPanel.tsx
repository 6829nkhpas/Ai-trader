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
import OiChainTable from './OiChainTable';
import FnoMetricsHud from './FnoMetricsHud';

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

  // Derive underlying from chart symbol & keep panel in sync
  useEffect(() => {
    if (!selectedSymbol) return;
    const underlying = getUnderlyingFromSymbol(selectedSymbol);
    if (!underlying) return;

    // Always update if the derived underlying differs from the current one
    if (underlying !== fnoUnderlying) {
      setFnoUnderlying(underlying);
    }

    // Register underlying for backend ingestion on symbol change
    if (selectedSymbol !== prevSymbolRef.current) {
      prevSymbolRef.current = selectedSymbol;
      invoke<boolean>('fno_request_underlying', { underlying }).catch((err) => {
        console.warn('[FnoSidebarPanel] fno_request_underlying failed:', err);
      });
    }
  }, [selectedSymbol, fnoUnderlying, setFnoUnderlying]);

  // Auto-sync expiry dropdown to match contract symbol's expiry
  useEffect(() => {
    if (!selectedSymbol || !chains) return;
    const underlying = getUnderlyingFromSymbol(selectedSymbol);
    if (!underlying) return;
    const expiriesList = chains.expiries_by_underlying[underlying] || [];
    if (expiriesList.length > 0) {
      const matched = matchExpiryFromSymbol(selectedSymbol, expiriesList);
      if (matched && matched !== fnoExpiry) {
        setFnoExpiry(matched);
      }
    }
  }, [selectedSymbol, chains, fnoExpiry, setFnoExpiry]);

  // Selector option lists
  const underlyings = useMemo(() => deriveUnderlyingOptions(chains, fnoUnderlying), [chains, fnoUnderlying]);
  const expiries = useMemo(() => deriveExpiryOptions(chains, fnoUnderlying), [chains, fnoUnderlying]);

  // Register fno-snapshot listener
  useEffect(() => {
    let cancelled = false;
    let unlisten: UnlistenFn | undefined;
    (async () => {
      try {
        const fn = await listen<FnoSnapshot>('fno-snapshot', (event) => {
          if (!cancelled) setViewState(toFnoViewState(event.payload));
        });
        if (cancelled) fn();
        else unlisten = fn;
      } catch { /* not in Tauri context */ }
    })();
    return () => {
      cancelled = true;
      unlisten?.();
      invoke('fno_unsubscribe').catch(() => {});
    };
  }, []);

  // Populate selectors from fno_list_chains
  useEffect(() => {
    if (!fnoUnderlying) return; // Skip until underlying is derived from chart
    let cancelled = false;
    (async () => {
      try {
        const result = await invoke<FnoChains>('fno_list_chains');
        if (!cancelled) setChains(result);
      } catch { /* not in Tauri */ }
    })();
    return () => { cancelled = true; };
  }, [fnoUnderlying]);

  // Fetch analytics + subscribe on selector change
  useEffect(() => {
    if (!fnoUnderlying) return; // Skip until underlying is derived from chart
    let cancelled = false;
    (async () => {
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
            detail: typeof err === 'string' ? err : 'F&O service unavailable',
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
      try {
        await invoke('fno_subscribe', { underlying: fnoUnderlying, expiry: fnoExpiry });
      } catch { /* not in Tauri */ }
    })();
    return () => { cancelled = true; };
  }, [fnoUnderlying, fnoExpiry]);

  // Status computation
  const isLive = viewState && viewState.kind !== 'service-error' && viewState.kind !== 'unavailable'
    ? viewState.marketStatus === 'open'
    : false;

  const msg = viewState?.kind === 'unavailable'
    ? viewState.reason
    : viewState?.kind === 'service-error'
      ? viewState.detail
      : 'F&O data unavailable. Ensure the F&O service is running.';

  return (
    <div className="flex flex-col gap-0 divide-y divide-border-default/40">
      {/* ── Selectors + status bar: Always visible ── */}
      <div className="flex flex-col gap-3 px-3 py-3 bg-surface border-b border-border-default/30">
        {/* Tracking indicator */}
        <div className="flex items-center justify-between">
          <span className="text-[8px] font-black uppercase tracking-widest text-text-muted">Tracking</span>
          <span className="text-[10px] font-mono font-bold text-cyan-400 truncate max-w-[180px]">
            {selectedSymbol || '—'}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2">
          <div className="flex flex-col gap-1">
            <span className="text-[8px] font-black uppercase tracking-widest text-text-muted">Underlying</span>
            <div className="relative">
              <select
                value={fnoUnderlying}
                onChange={(e) => setFnoUnderlying(e.target.value)}
                className="w-full appearance-none rounded-none border border-border-default bg-elevated pl-2 pr-6 py-1 text-[10px] font-bold text-text-primary focus:outline-none focus:border-emerald-500/40 cursor-pointer"
              >
                {fnoUnderlying && !underlyings.includes(fnoUnderlying) && (
                  <option value={fnoUnderlying}>{fnoUnderlying}</option>
                )}
                {underlyings.map(u => <option key={u} value={u}>{u}</option>)}
              </select>
              <ChevronDown className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 text-text-muted" size={10} />
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <span className="text-[8px] font-black uppercase tracking-widest text-text-muted">Expiry</span>
            <div className="relative">
              <select
                value={fnoExpiry}
                onChange={(e) => setFnoExpiry(e.target.value)}
                className="w-full appearance-none rounded-none border border-border-default bg-elevated pl-2 pr-6 py-1 text-[10px] font-bold text-text-primary focus:outline-none focus:border-emerald-500/40 cursor-pointer"
              >
                <option value="">Nearest</option>
                {expiries.map(e => <option key={e} value={e}>{e}</option>)}
              </select>
              <ChevronDown className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 text-text-muted" size={10} />
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between border-t border-border-default/10 pt-2 text-[9px] font-bold uppercase tracking-wider text-text-secondary">
          <span>Feed Status</span>
          <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[8px] font-black uppercase ${
            isLive
              ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-400'
              : 'border-amber-500/25 bg-amber-500/10 text-amber-400'
          }`}>
            <span className={`h-1.5 w-1.5 rounded-full ${isLive ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
            {isLive ? 'Live' : 'Closed'}
          </span>
        </div>
      </div>

      {/* ── Conditional content section ── */}
      {!fnoUnderlying ? (
        <div className="flex h-40 items-center justify-center gap-2 text-text-secondary bg-surface/20">
          <Activity size={14} />
          <span className="text-[10px] font-semibold uppercase tracking-wider">Select a symbol…</span>
        </div>
      ) : loading && viewState === null ? (
        <div className="flex h-40 items-center justify-center gap-2 text-text-secondary bg-surface/20">
          <Loader2 size={14} className="animate-spin" />
          <span className="text-[10px] font-semibold uppercase tracking-wider">Loading F&amp;O…</span>
        </div>
      ) : !viewState || viewState.kind === 'unavailable' || viewState.kind === 'service-error' ? (
        <div className="flex flex-col gap-3 p-4 bg-surface/20">
          <div className="flex items-center gap-2 rounded border border-amber-500/20 bg-amber-500/5 px-3 py-2">
            <Activity size={12} className="shrink-0 text-amber-400" />
            <p className="text-[10px] text-amber-300 leading-relaxed">{msg}</p>
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
            className="flex items-center justify-center gap-1.5 rounded border border-border-default bg-elevated px-3 py-1.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary hover:bg-elevated/80 transition-colors"
          >
            <RefreshCw size={10} /> Retry
          </button>
        </div>
      ) : (
        <>
          <FnoMetricsHud viewState={viewState as FnoViewState & { kind: 'ready' | 'partial' }} />
          <div className="flex flex-col">
            <div className="border-b border-border-default px-3 py-1 text-[9px] font-bold uppercase tracking-widest text-text-muted bg-elevated/30">
              OI Chain (Call vs Put)
            </div>
            <OiChainTable
              viewState={viewState as FnoViewState & { kind: 'ready' | 'partial' }}
              highlightedStrike={highlightedStrike}
              highlightedSide={highlightedSide}
            />
          </div>
        </>
      )}
    </div>
  );
}
