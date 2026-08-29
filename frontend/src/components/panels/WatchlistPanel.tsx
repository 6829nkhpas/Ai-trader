'use client';

import React, { useState, useCallback, useRef, useEffect } from 'react';
import { Search, Loader2, X, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { useTradeStore } from '../../store/useTradeStore';
import { staggerContainer, fadeInUp, crossfade } from '../../lib/motionVariants';
import WatchlistSkeleton from './left-panel/WatchlistSkeleton';
import { kiteFetch } from '../../lib/kiteFetch';
import { bridgeInvoke } from '../../lib/bridge';

// ── Static Top-10 Watchlist Symbols (NIFTY 50 Blue Chips) ──────────────
const TOP_WATCHLIST = [
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

const SECTOR_COLORS: Record<string, string> = {
  Energy: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
  IT: 'bg-cyan-500/10 text-cyan-600 dark:text-cyan-400',
  Banking: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  FMCG: 'bg-purple-500/10 text-purple-600 dark:text-purple-400',
  Telecom: 'bg-rose-500/10 text-rose-600 dark:text-rose-400',
  Infra: 'bg-orange-500/10 text-orange-600 dark:text-orange-400',
};

// ── Types ───────────────────────────────────────────────────────────────
interface QuoteData {
  symbol: string;
  last_price: number;
  change: number | null;
  net_change: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
}

interface SearchInstrument {
  instrument_token?: number;
  tradingsymbol: string;
  name: string;
  instrument_type?: string;
  exchange: string;
}

// Tauri `search_instruments` returns a tagged union (EQ | FNO). We accept
// either that shape or the legacy flat `SearchInstrument` shape so the panel
// keeps working no matter which backend is reachable.
type TauriSearchResult =
  | { kind: 'EQ'; symbol: string; name: string; exchange: string }
  | {
      kind: 'FNO';
      tradingsymbol: string;
      underlying: string;
      expiry: string;
      strike: number | null;
      optionType: 'CE' | 'PE' | 'FUT';
    };

function toSearchInstrument(r: TauriSearchResult): SearchInstrument {
  if (r.kind === 'EQ') {
    return {
      tradingsymbol: r.symbol,
      name: r.name,
      instrument_type: 'EQ',
      exchange: r.exchange || 'NSE',
    };
  }
  const desc =
    r.optionType === 'FUT'
      ? `${r.underlying} FUT (${r.expiry})`
      : `${r.underlying} ${r.strike ?? ''} ${r.optionType} (${r.expiry})`;
  return {
    tradingsymbol: r.tradingsymbol,
    name: desc,
    instrument_type: r.optionType,
    exchange: 'NFO',
  };
}

export default function WatchlistPanel() {
  const [query, setQuery] = useState('');
  const [quotes, setQuotes] = useState<Record<string, QuoteData>>({});
  const [quotesLoading, setQuotesLoading] = useState(true);
  const [searchResults, setSearchResults] = useState<SearchInstrument[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const quoteIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // ── Symbol selection from store ────────────────────────────────────
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const setSelectedSymbol = useTradeStore((s) => s.setSelectedSymbol);

  // ── Fetch quotes for watchlist stocks ──────────────────────────────
  const fetchQuotes = useCallback(async () => {
    try {
      const params = TOP_WATCHLIST.map((s) => `i=NSE:${s.symbol}`).join('&');
      const res = await kiteFetch(`/quote?${params}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.quotes) {
        const map: Record<string, QuoteData> = {};
        for (const q of data.quotes) {
          map[q.symbol] = q;
        }
        setQuotes(map);
      }
    } catch (err) {
      console.error('[Watchlist] Quote fetch failed:', err);
    } finally {
      setQuotesLoading(false);
    }
  }, []);

  // Fetch quotes on mount + poll every 30s.
  // We store fetchQuotes in a ref so the interval callback always calls the
  // latest version without adding it to the effect dependency array — this
  // avoids the "setState in effect" lint rule while keeping the polling stable.
  const fetchQuotesRef = useRef(fetchQuotes);
  useEffect(() => {
    fetchQuotesRef.current = fetchQuotes;
  }, [fetchQuotes]);

  useEffect(() => {
    fetchQuotesRef.current();
    quoteIntervalRef.current = setInterval(() => fetchQuotesRef.current(), 30_000);
    return () => {
      if (quoteIntervalRef.current) clearInterval(quoteIntervalRef.current);
    };
  }, []);

  // ── Debounced search via Tauri IPC (local SQLite) ──────────────
  const handleSearch = useCallback(async (searchQuery: string) => {
    const normalized = searchQuery.trim();
    if (normalized.length < 2) {
      setSearchResults([]);
      setShowDropdown(false);
      setIsSearching(false);
      return;
    }

    setIsSearching(true);
    setShowDropdown(true);

    try {
      const results = await bridgeInvoke<TauriSearchResult[]>('search_instruments', { query: normalized });
      // The command returns EQ + Index + FNO rows in a single flat list — one
      // global search across NSE / BSE / NFO. Map each row to the flat
      // `SearchInstrument` shape this panel already renders.
      setSearchResults((results || []).map(toSearchInstrument));
    } catch (err) {
      console.error('[Watchlist] search_instruments failed:', err);
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  }, []);

  const handleInputChange = (value: string) => {
    setQuery(value);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);

    if (!value.trim() || value.trim().length < 2) {
      setSearchResults([]);
      setShowDropdown(false);
      return;
    }

    searchTimeoutRef.current = setTimeout(() => {
      handleSearch(value);
    }, 400); // 400ms debounce
  };

  const clearSearch = () => {
    setQuery('');
    setSearchResults([]);
    setShowDropdown(false);
  };

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    };
  }, []);

  // `null` means the upstream did not report it — render an em-dash rather than
  // standing in a zero, which would read as a real reading of 0.
  const formatPrice = (price: number | null) => {
    if (!price) return '—';
    return '₹' + price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  const formatChange = (change: number | null) => {
    if (change === null) return '—';
    const sign = change >= 0 ? '+' : '';
    return `${sign}${change.toFixed(2)}%`;
  };

  return (
    <div className="flex h-full flex-col">
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="shrink-0 border-b border-border-default px-3 py-2">

        {/* Search input with dropdown */}
        <div className="relative mt-1.5" ref={dropdownRef}>
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
          <input
            value={query}
            onChange={(e) => handleInputChange(e.target.value)}
            onFocus={() => { if (searchResults.length > 0) setShowDropdown(true); }}
            placeholder="Search any symbol (NSE / BSE / F&O)..."
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

          {/* ── Search Dropdown ──────────────────────────────── */}
          <AnimatePresence>
            {showDropdown && (
              <motion.div
                variants={crossfade}
                initial="hidden"
                animate="show"
                exit="exit"
                className="absolute left-0 right-0 top-full z-50 mt-1 max-h-64 overflow-y-auto scrollbar-none rounded-lg border border-border-default bg-surface shadow-lg panel-shadow"
              >
                {isSearching ? (
                  <div className="flex items-center justify-center gap-2 px-3 py-4">
                    <Loader2 size={14} className="animate-spin text-primary" />
                    <span className="text-xs text-text-secondary">Searching...</span>
                  </div>
                ) : searchResults.length === 0 ? (
                  <div className="px-3 py-4 text-center text-xs text-text-muted">
                    No instruments found
                  </div>
                ) : (
                  searchResults.map((inst, idx) => (
                    <motion.button
                      key={`${inst.exchange}:${inst.tradingsymbol}:${idx}`}
                      variants={fadeInUp}
                      initial="hidden"
                      animate="show"
                      type="button"
                      onClick={() => {
                        setSelectedSymbol(inst.tradingsymbol);
                        setShowDropdown(false);
                        setQuery('');
                        setSearchResults([]);
                      }}
                      className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition-colors hover:bg-elevated/70"
                    >
                      <div className="flex flex-col min-w-0">
                        <span className="text-xs font-semibold text-text-primary truncate">
                          {inst.tradingsymbol}
                        </span>
                        <span className="text-[10px] text-text-muted truncate">
                          {inst.name}
                        </span>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <span className="rounded px-1 py-px text-[8px] font-semibold uppercase tracking-wider bg-elevated text-text-muted">
                          {inst.instrument_type || 'EQ'}
                        </span>
                        <span className="text-[9px] text-text-muted">{inst.exchange}</span>
                      </div>
                    </motion.button>
                  ))
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* ── Watchlist Content ───────────────────────────────── */}
      <div className="flex flex-1 flex-col gap-0 overflow-y-auto">
        {quotesLoading ? (
          <WatchlistSkeleton rows={10} />
        ) : (
          <motion.div variants={staggerContainer} initial="hidden" animate="show">
          {TOP_WATCHLIST.map((stock) => {
            const quote = quotes[stock.symbol];
            const sectorColor = SECTOR_COLORS[stock.sector] ?? 'bg-elevated text-text-muted';
            const isPositive = quote ? quote.change !== null && quote.change >= 0 : false;
            const isActive = selectedSymbol === stock.symbol;

            return (
              <motion.button
                key={stock.symbol}
                variants={fadeInUp}
                type="button"
                onClick={() => setSelectedSymbol(stock.symbol)}
                className={`group flex w-full items-center justify-between gap-1 px-3 py-2 text-xs text-left transition-colors cursor-pointer border-l-2 ${
                  isActive
                    ? 'bg-primary/10 border-primary text-text-primary'
                    : 'hover:bg-elevated/70 border-transparent hover:border-primary/50'
                }`}
              >
                {/* Left: Symbol + Name */}
                <div className="flex flex-col min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[13px] font-semibold text-text-primary truncate">
                      {stock.symbol}
                    </span>
                    <span className={`rounded px-1 py-px text-[7px] font-semibold uppercase tracking-wider ${sectorColor}`}>
                      {stock.sector}
                    </span>
                  </div>
                  <span className="text-[10px] text-text-muted truncate mt-0.5">
                    {stock.name}
                  </span>
                </div>

                {/* Right: Price + Change */}
                <div className="flex flex-col items-end shrink-0">
                  {quote ? (
                    <>
                      <span className="text-[12px] font-semibold text-text-primary tabular-nums">
                        {formatPrice(quote.last_price)}
                      </span>
                      <div className={`flex items-center gap-0.5 ${quote.change === null ? 'text-text-muted' : isPositive ? 'text-bull' : 'text-bear'}`}>
                        {quote.change !== null && (isPositive ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />)}
                        <span className="text-[10px] font-medium tabular-nums">
                          {formatChange(quote.change)}
                        </span>
                      </div>
                    </>
                  ) : (
                    <span className="text-[10px] text-text-muted/50">—</span>
                  )}
                </div>
              </motion.button>
            );
          })}
          </motion.div>
        )}
      </div>
    </div>
  );
}
