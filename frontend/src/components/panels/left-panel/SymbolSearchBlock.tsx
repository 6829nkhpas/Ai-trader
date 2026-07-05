'use client';

import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { Search, Loader2, X, Plus } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { useTradeStore } from '../../../store/useTradeStore';
import { useChartUIStore } from '../../../store/useChartUIStore';

const SECTOR_COLORS: Record<string, string> = {
  Energy: 'bg-amber-500/10 text-amber-400',
  IT: 'bg-cyan-500/10 text-cyan-400',
  Banking: 'bg-emerald-500/10 text-emerald-400',
  FMCG: 'bg-purple-500/10 text-purple-400',
  Telecom: 'bg-rose-500/10 text-rose-400',
  Infra: 'bg-orange-500/10 text-orange-400',
  Auto: 'bg-sky-500/10 text-sky-400',
  Pharma: 'bg-teal-500/10 text-teal-400',
  Metal: 'bg-zinc-500/10 text-zinc-400',
  Realty: 'bg-lime-500/10 text-lime-400',
  Media: 'bg-pink-500/10 text-pink-400',
  EQ: 'bg-slate-500/10 text-slate-400',
  FUT: 'bg-indigo-500/10 text-indigo-400',
  CE: 'bg-emerald-500/10 text-emerald-400',
  PE: 'bg-rose-500/10 text-rose-400',
};

type SearchResult =
  | { kind: 'EQ'; symbol: string; name: string; exchange: string }
  | {
      kind: 'FNO';
      tradingsymbol: string;
      underlying: string;
      expiry: string;
      strike: number | null;
      optionType: 'CE' | 'PE' | 'FUT';
    };

const resultSymbol = (r: SearchResult): string =>
  r.kind === 'EQ' ? r.symbol : r.tradingsymbol;

const resultKey = (r: SearchResult): string =>
  r.kind === 'EQ' ? `EQ:${r.symbol}` : `FNO:${r.tradingsymbol}`;

const DEFAULT_FNO_UNDERLYINGS = ['NIFTY 50', 'BANKNIFTY'];

const INDEX_NFO_ALIASES: Record<string, string> = {
  'NIFTY 50': 'NIFTY',
  'NIFTY BANK': 'BANKNIFTY',
  'BANKNIFTY': 'BANKNIFTY',
  'NIFTY FIN SERVICE': 'FINNIFTY',
  'FINNIFTY': 'FINNIFTY',
  'NIFTY MIDCAP SELECT': 'MIDCPNIFTY',
  'MIDCPNIFTY': 'MIDCPNIFTY',
  'NIFTY NEXT 50': 'NIFTYNXT50',
  'NIFTYNXT50': 'NIFTYNXT50',
};

const nfoNameOf = (configured: string): string =>
  INDEX_NFO_ALIASES[configured.trim().toUpperCase()] ?? configured.trim();

export default function SymbolSearchBlock() {
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);

  const [fnoUnderlyingFilter, setFnoUnderlyingFilter] = useState<string | null>(null);
  const [fnoExpiryFilter, setFnoExpiryFilter] = useState<string | null>(null);
  const [fnoTypeFilter, setFnoTypeFilter] = useState<'CE' | 'PE' | 'FUT' | null>(null);

  const [configuredUnderlyings, setConfiguredUnderlyings] = useState<string[]>(
    DEFAULT_FNO_UNDERLYINGS,
  );

  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const setSelectedSymbol = useTradeStore((s) => s.setSelectedSymbol);
  const setActiveProfile = useTradeStore((s) => s.setActiveProfile);
  const setFnoUnderlying = useTradeStore((s) => s.setFnoUnderlying);
  const addToWatchlist = useTradeStore((s) => s.addToWatchlist);

  const splitView = useChartUIStore((s) => s.splitView);
  const activePaneId = useChartUIStore((s) => s.activePaneId);
  const setPaneSymbol = useChartUIStore((s) => s.setPaneSymbol);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const chains = await invoke<{ underlyings?: string[] }>('fno_list_chains');
        if (!cancelled && Array.isArray(chains?.underlyings) && chains.underlyings.length > 0) {
          setConfiguredUnderlyings(chains.underlyings);
        }
      } catch (err) {
        console.warn('[SymbolSearchBlock] fno_list_chains failed; using default underlyings:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSearch = useCallback(async (searchQuery: string) => {
    const normalized = searchQuery.trim();
    if (normalized.length < 2) {
      setSearchResults([]); setShowDropdown(false); setIsSearching(false); setSearchError(null);
      return;
    }
    setIsSearching(true); setShowDropdown(true); setSearchError(null);
    try {
      const results = await invoke<SearchResult[]>('search_instruments', { query: normalized });
      setSearchResults(results || []);
    } catch (err) {
      console.error('[SymbolSearchBlock] search_instruments failed:', err);
      setSearchResults([]);
      setSearchError('Search failed — please try again');
    } finally {
      setIsSearching(false);
    }
  }, []);

  const handleInputChange = (value: string) => {
    setQuery(value);
    setFnoUnderlyingFilter(null);
    setFnoExpiryFilter(null);
    setFnoTypeFilter(null);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    if (!value.trim() || value.trim().length < 2) {
      setSearchResults([]); setShowDropdown(false); setSearchError(null);
      return;
    }
    searchTimeoutRef.current = setTimeout(() => handleSearch(value), 400);
  };

  const clearSearch = () => {
    setQuery(''); setSearchResults([]); setShowDropdown(false); setSearchError(null);
    setFnoUnderlyingFilter(null); setFnoExpiryFilter(null); setFnoTypeFilter(null);
  };

  const routeSymbolToChart = useCallback((symbol: string) => {
    if (splitView) {
      setPaneSymbol(activePaneId, symbol);
    } else {
      setSelectedSymbol(symbol);
    }
  }, [splitView, setPaneSymbol, activePaneId, setSelectedSymbol]);

  const handleSelectResult = useCallback(async (r: SearchResult) => {
    const symbol = resultSymbol(r);
    const sector = r.kind === 'EQ' ? 'EQ' : r.optionType;

    let displayName = symbol;
    if (r.kind === 'EQ') {
      displayName = (r.name || r.symbol).replace(/"/g, '');
    } else {
      let expiryFormatted = r.expiry;
      if (r.expiry) {
        try {
          const date = new Date(r.expiry);
          if (!isNaN(date.getTime())) {
            const day = date.getDate();
            const month = date.toLocaleString('en-US', { month: 'short' });
            expiryFormatted = `${day} ${month}`;
          }
        } catch (e) {}
      }
      if (r.optionType === 'FUT') {
        displayName = `${r.underlying} FUT (${expiryFormatted})`;
      } else {
        displayName = `${r.underlying} ${r.strike} ${r.optionType} (${expiryFormatted})`;
      }
    }

    addToWatchlist({
      symbol,
      token: 0,
      name: displayName,
      sector,
      lastPrice: 0,
      change: 0,
    });

    const closeDropdown = () => {
      setShowDropdown(false);
      setQuery('');
      setSearchResults([]);
      setSearchError(null);
      setFnoUnderlyingFilter(null); setFnoExpiryFilter(null); setFnoTypeFilter(null);
    };

    if (r.kind === 'FNO' && typeof r.underlying === 'string') {
      setActiveProfile('FNO');
      const matchedConfig = configuredUnderlyings.find((u) => {
        const ru = r.underlying.toUpperCase();
        return u.toUpperCase() === ru || nfoNameOf(u).toUpperCase() === ru;
      });
      setFnoUnderlying(matchedConfig ?? r.underlying);
      routeSymbolToChart(symbol);
      closeDropdown();

      if (!matchedConfig) {
        invoke<boolean>('fno_request_underlying', { underlying: r.underlying }).catch(
          (err) => console.warn('[SymbolSearchBlock] fno_request_underlying failed:', err),
        );
      }
      return;
    }

    routeSymbolToChart(symbol);
    closeDropdown();
  }, [addToWatchlist, routeSymbolToChart, configuredUnderlyings, setActiveProfile, setFnoUnderlying]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) setShowDropdown(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => { return () => { if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current); }; }, []);

  const fnoResults = useMemo(
    () => searchResults.filter((r): r is Extract<SearchResult, { kind: 'FNO' }> => r.kind === 'FNO'),
    [searchResults],
  );
  const hasFno = fnoResults.length > 0;
  const underlyingOptions = useMemo(
    () => Array.from(new Set(fnoResults.map((r) => r.underlying))).sort(),
    [fnoResults],
  );
  const expiryOptions = useMemo(
    () => Array.from(new Set(fnoResults.map((r) => r.expiry))).sort(),
    [fnoResults],
  );
  const typeOptions = useMemo(
    () => Array.from(new Set(fnoResults.map((r) => r.optionType))) as ('CE' | 'PE' | 'FUT')[],
    [fnoResults],
  );

  const filteredResults = useMemo(() => {
    return searchResults.filter((r) => {
      if (r.kind === 'EQ') return true;
      if (fnoUnderlyingFilter && r.underlying !== fnoUnderlyingFilter) return false;
      if (fnoExpiryFilter && r.expiry !== fnoExpiryFilter) return false;
      if (fnoTypeFilter && r.optionType !== fnoTypeFilter) return false;
      return true;
    });
  }, [searchResults, fnoUnderlyingFilter, fnoExpiryFilter, fnoTypeFilter]);

  const renderResultRow = (inst: SearchResult) => {
    if (inst.kind === 'EQ') {
      const eqColor = SECTOR_COLORS['EQ'] ?? 'bg-slate-500/10 text-slate-400';
      return (
        <button
          key={resultKey(inst)}
          type="button"
          onClick={() => handleSelectResult(inst)}
          className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left transition-colors hover:bg-elevated/70"
        >
          <div className="flex flex-col min-w-0">
            <span className="text-[11px] font-semibold text-text-primary truncate">{inst.symbol}</span>
            <span className="text-[9px] text-text-muted truncate">{inst.name}</span>
          </div>
          <div className="flex items-center gap-1">
            <Plus size={10} className="text-text-secondary" />
            <span className={`rounded-none px-1 py-px text-[7px] font-semibold uppercase tracking-wider ${eqColor}`}>
              {inst.exchange || 'EQ'}
            </span>
          </div>
        </button>
      );
    }
    const typeColor = SECTOR_COLORS[inst.optionType] ?? 'bg-indigo-500/10 text-indigo-400';
    const meta = [
      inst.underlying,
      inst.expiry,
      inst.strike != null ? inst.strike.toString() : null,
    ].filter(Boolean).join(' · ');
    return (
      <button
        key={resultKey(inst)}
        type="button"
        onClick={() => handleSelectResult(inst)}
        className="flex w-full items-center justify-between gap-2 border-l-2 border-l-primary/30 px-3 py-1.5 text-left transition-colors hover:bg-elevated/70"
      >
        <div className="flex flex-col min-w-0">
          <span className="text-[11px] font-semibold text-text-primary truncate">{inst.tradingsymbol}</span>
          <span className="text-[9px] text-text-muted truncate">{meta}</span>
        </div>
        <div className="flex items-center gap-1">
          <Plus size={10} className="text-text-secondary" />
          <span className={`rounded-none px-1 py-px text-[7px] font-semibold uppercase tracking-wider ${typeColor}`}>
            {inst.optionType}
          </span>
        </div>
      </button>
    );
  };

  const eqRows = filteredResults.filter((r): r is Extract<SearchResult, { kind: 'EQ' }> => r.kind === 'EQ');
  const fnoRows = filteredResults.filter((r): r is Extract<SearchResult, { kind: 'FNO' }> => r.kind === 'FNO');

  return (
    <div className="hidden px-3 pt-2 pb-1.5">
      <div className="relative" ref={dropdownRef}>
        <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
        <input
          value={query}
          onChange={(e) => handleInputChange(e.target.value)}
          onFocus={() => { if (searchResults.length > 0) setShowDropdown(true); }}
          placeholder="Search NSE symbol..."
          aria-label="Search symbols"
          className="h-8 w-full rounded-none border border-border-default bg-surface pl-8 pr-8 text-[11px] text-text-primary placeholder:text-text-muted transition-colors focus:border-text-primary focus:outline-none focus:ring-1 focus:ring-text-primary"
        />
        {query && (
          <button onClick={clearSearch} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary transition-colors" aria-label="Clear search">
            <X size={13} />
          </button>
        )}
        {showDropdown && (
          <div className="absolute left-0 right-0 top-full z-50 mt-1 max-h-72 overflow-y-auto scrollbar-none rounded-none border border-border-default bg-surface shadow-lg panel-shadow">
            {!isSearching && !searchError && hasFno && (
              <div className="sticky top-0 z-10 flex flex-wrap gap-1 border-b border-border-default bg-surface px-2 py-1.5">
                {underlyingOptions.map((u) => (
                  <button
                    key={`u:${u}`}
                    type="button"
                    onClick={() => setFnoUnderlyingFilter(fnoUnderlyingFilter === u ? null : u)}
                    className={`rounded-none px-1.5 py-px text-[8px] font-semibold uppercase tracking-wider transition-colors ${
                      fnoUnderlyingFilter === u
                        ? 'bg-primary/20 text-text-primary'
                        : 'bg-elevated text-text-muted hover:text-text-secondary'
                    }`}
                  >
                    {u}
                  </button>
                ))}
                {expiryOptions.map((ex) => (
                  <button
                    key={`e:${ex}`}
                    type="button"
                    onClick={() => setFnoExpiryFilter(fnoExpiryFilter === ex ? null : ex)}
                    className={`rounded-none px-1.5 py-px text-[8px] font-semibold uppercase tracking-wider transition-colors ${
                      fnoExpiryFilter === ex
                        ? 'bg-primary/20 text-text-primary'
                        : 'bg-elevated text-text-muted hover:text-text-secondary'
                    }`}
                  >
                    {ex}
                  </button>
                ))}
                {typeOptions.map((t) => (
                  <button
                    key={`t:${t}`}
                    type="button"
                    onClick={() => setFnoTypeFilter(fnoTypeFilter === t ? null : t)}
                    className={`rounded-none px-1.5 py-px text-[8px] font-semibold uppercase tracking-wider transition-colors ${
                      fnoTypeFilter === t
                        ? (SECTOR_COLORS[t] ?? 'bg-primary/20 text-text-primary')
                        : 'bg-elevated text-text-muted hover:text-text-secondary'
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>
            )}

            {isSearching ? (
              <div className="flex items-center justify-center gap-2 px-3 py-4">
                <Loader2 size={13} className="animate-spin text-text-muted" />
                <span className="text-[11px] text-text-secondary">Searching...</span>
              </div>
            ) : searchError ? (
              <div className="px-3 py-4 text-center text-[11px] text-bear">{searchError}</div>
            ) : filteredResults.length === 0 ? (
              <div className="px-3 py-4 text-center text-[11px] text-text-muted">No instruments found</div>
            ) : (
              <>
                {eqRows.length > 0 && (
                  <>
                    <div className="sticky top-0 z-[5] bg-surface px-3 py-1 text-[8px] font-bold uppercase tracking-widest text-text-muted/70 border-b border-border-default/50">
                      Stocks
                    </div>
                    {eqRows.map(renderResultRow)}
                  </>
                )}
                {fnoRows.length > 0 && (
                  <>
                    <div className="bg-surface px-3 py-1 text-[8px] font-bold uppercase tracking-widest text-primary/80 border-y border-border-default/50">
                      F&amp;O · Futures &amp; Options
                    </div>
                    {fnoRows.map(renderResultRow)}
                  </>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
