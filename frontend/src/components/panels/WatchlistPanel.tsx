'use client';

import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Search, Star, TrendingUp, TrendingDown, Minus, Loader2, X } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';

// ── Static Top-10 Watchlist (NIFTY 50 Blue Chips) ─────────────────────
interface WatchlistStock {
  symbol: string;
  name: string;
  sector: string;
}

const TOP_WATCHLIST: WatchlistStock[] = [
  { symbol: 'RELIANCE', name: 'Reliance Industries', sector: 'Energy' },
  { symbol: 'TCS', name: 'Tata Consultancy', sector: 'IT' },
  { symbol: 'HDFCBANK', name: 'HDFC Bank', sector: 'Banking' },
  { symbol: 'INFY', name: 'Infosys', sector: 'IT' },
  { symbol: 'ICICIBANK', name: 'ICICI Bank', sector: 'Banking' },
  { symbol: 'HINDUNILVR', name: 'Hindustan Unilever', sector: 'FMCG' },
  { symbol: 'SBIN', name: 'State Bank of India', sector: 'Banking' },
  { symbol: 'BHARTIARTL', name: 'Bharti Airtel', sector: 'Telecom' },
  { symbol: 'KOTAKBANK', name: 'Kotak Mahindra Bank', sector: 'Banking' },
  { symbol: 'LT', name: 'Larsen & Toubro', sector: 'Infra' },
];

// ── Sector badge color map ──────────────────────────────────────────────
const SECTOR_COLORS: Record<string, string> = {
  Energy: 'bg-amber-500/10 text-amber-400',
  IT: 'bg-cyan-500/10 text-cyan-400',
  Banking: 'bg-emerald-500/10 text-emerald-400',
  FMCG: 'bg-purple-500/10 text-purple-400',
  Telecom: 'bg-rose-500/10 text-rose-400',
  Infra: 'bg-orange-500/10 text-orange-400',
};

interface SearchResult {
  symbol: string;
  name: string;
  action_type?: 'BUY' | 'SELL' | 'HOLD';
  conviction?: number;
}

export default function WatchlistPanel() {
  const { liveDecisions, activeDecision } = useTradeStore();
  const [query, setQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Build a map of latest decisions by symbol for the watchlist badges
  const latestBySymbol = React.useMemo(() => {
    const map = new Map<string, { action_type: string; conviction: number }>();
    for (const d of liveDecisions) {
      map.set(d.symbol, { action_type: d.action_type, conviction: d.final_conviction_score });
    }
    return map;
  }, [liveDecisions]);

  // ── Search handler: only fires when user types and pauses ────────
  const handleSearch = useCallback(
    (searchQuery: string) => {
      const normalized = searchQuery.trim().toUpperCase();
      if (!normalized) {
        setSearchResult(null);
        setSearchError(null);
        return;
      }

      setIsSearching(true);
      setSearchError(null);

      // Check in liveDecisions first (local, zero-cost)
      const localMatch = liveDecisions.find(
        (d) => d.symbol.toUpperCase() === normalized
      );

      if (localMatch) {
        setSearchResult({
          symbol: localMatch.symbol,
          name: localMatch.symbol,
          action_type: localMatch.action_type,
          conviction: localMatch.final_conviction_score,
        });
        setIsSearching(false);
        return;
      }

      // Check if it's in the watchlist
      const watchlistMatch = TOP_WATCHLIST.find(
        (s) => s.symbol.toUpperCase() === normalized
      );

      if (watchlistMatch) {
        setSearchResult({
          symbol: watchlistMatch.symbol,
          name: watchlistMatch.name,
        });
        setIsSearching(false);
        return;
      }

      // If not found locally, show as custom symbol (no API call for now)
      setSearchResult({
        symbol: normalized,
        name: `${normalized} (Custom)`,
      });
      setIsSearching(false);
    },
    [liveDecisions]
  );

  // Debounced search — only trigger after 600ms of idle typing
  const handleInputChange = (value: string) => {
    setQuery(value);
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }
    if (!value.trim()) {
      setSearchResult(null);
      setSearchError(null);
      return;
    }
    searchTimeoutRef.current = setTimeout(() => {
      handleSearch(value);
    }, 600);
  };

  const clearSearch = () => {
    setQuery('');
    setSearchResult(null);
    setSearchError(null);
  };

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    };
  }, []);

  const actionColor = (action?: string) => {
    if (action === 'BUY') return 'text-bull';
    if (action === 'SELL') return 'text-bear';
    return 'text-neutral';
  };

  const ActionIcon = ({ action }: { action?: string }) => {
    if (action === 'BUY') return <TrendingUp size={12} className="text-bull" />;
    if (action === 'SELL') return <TrendingDown size={12} className="text-bear" />;
    return <Minus size={12} className="text-text-muted" />;
  };

  return (
    <div className="flex h-full flex-col">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="shrink-0 border-b border-border-default px-3 py-2">
        <div className="flex items-center justify-between">
          <h2 className="text-xs font-bold uppercase tracking-widest text-text-secondary">
            Watchlist
          </h2>
          <span className="rounded px-1.5 py-px text-[9px] font-bold uppercase tracking-widest bg-emerald-500/10 text-emerald-400">
            TOP 10
          </span>
        </div>
        <div className="relative mt-1.5">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            value={query}
            onChange={(e) => handleInputChange(e.target.value)}
            placeholder="Search any symbol..."
            aria-label="Search symbols"
            className="h-9 w-full rounded-md border border-border-default bg-surface pl-8 pr-8 text-xs text-text-primary placeholder:text-text-muted transition-colors focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          />
          {query && (
            <button
              onClick={clearSearch}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary transition-colors"
              aria-label="Clear search"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      {/* ── Content ─────────────────────────────────────────── */}
      <div className="flex flex-1 flex-col gap-0 overflow-y-auto">

        {/* Search Result (appears above watchlist when searching) */}
        {isSearching && (
          <div className="flex items-center justify-center gap-2 px-3 py-4 border-b border-border-default">
            <Loader2 size={14} className="animate-spin text-primary" />
            <span className="text-xs text-text-secondary">Searching...</span>
          </div>
        )}

        {searchError && (
          <div className="px-3 py-3 border-b border-border-default">
            <p className="text-xs text-red-400">{searchError}</p>
          </div>
        )}

        {searchResult && !isSearching && (
          <div className="border-b border-border-default bg-emerald-500/5">
            <div className="px-3 py-1">
              <span className="text-[9px] font-bold uppercase tracking-widest text-emerald-400">Search Result</span>
            </div>
            <div className="flex items-center justify-between px-3 py-2 hover:bg-elevated/50 cursor-pointer transition-colors">
              <div className="flex flex-col min-w-0">
                <span className="text-sm font-semibold text-text-primary truncate">{searchResult.symbol}</span>
                <span className="text-[10px] text-text-muted truncate">{searchResult.name}</span>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {searchResult.action_type ? (
                  <>
                    <ActionIcon action={searchResult.action_type} />
                    <span className={`text-xs font-bold ${actionColor(searchResult.action_type)}`}>
                      {searchResult.action_type}
                    </span>
                    <span className="text-[11px] text-text-muted tabular-nums">
                      {searchResult.conviction}%
                    </span>
                  </>
                ) : (
                  <span className="text-[10px] text-text-muted italic">No signal</span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── Top 10 Watchlist ──────────────────────────────── */}
        <div className="px-3 py-1.5">
          <span className="text-[9px] font-bold uppercase tracking-widest text-text-muted">
            NIFTY 50 — Blue Chips
          </span>
        </div>

        {TOP_WATCHLIST.map((stock) => {
          const decision = latestBySymbol.get(stock.symbol);
          const isActive = activeDecision?.symbol === stock.symbol;
          const sectorColor = SECTOR_COLORS[stock.sector] ?? 'bg-elevated text-text-muted';

          return (
            <div
              key={stock.symbol}
              className={`
                group flex items-center justify-between gap-2 px-3 py-2 text-xs transition-colors cursor-pointer
                ${isActive
                  ? 'bg-emerald-500/10 border-l-2 border-emerald-500'
                  : 'hover:bg-elevated/70 border-l-2 border-transparent'
                }
              `}
            >
              {/* Left: Symbol + Name */}
              <div className="flex flex-col min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-semibold text-text-primary truncate">
                    {stock.symbol}
                  </span>
                  <span className={`rounded px-1 py-px text-[8px] font-semibold uppercase tracking-wider ${sectorColor}`}>
                    {stock.sector}
                  </span>
                </div>
                <span className="text-[10px] text-text-muted truncate mt-0.5">
                  {stock.name}
                </span>
              </div>

              {/* Right: Signal (from live decisions if available) */}
              <div className="flex items-center gap-2 shrink-0">
                {decision ? (
                  <>
                    <ActionIcon action={decision.action_type} />
                    <span className={`text-xs font-bold ${actionColor(decision.action_type)}`}>
                      {decision.action_type}
                    </span>
                    <span className="text-[11px] text-text-muted tabular-nums">
                      {decision.conviction}%
                    </span>
                  </>
                ) : (
                  <span className="text-[10px] text-text-muted/50 group-hover:text-text-muted transition-colors">
                    —
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
