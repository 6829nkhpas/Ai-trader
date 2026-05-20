'use client';

import React, { useState, useCallback, useRef, useEffect } from 'react';
import {
  Search, Loader2, X, ArrowUpRight, ArrowDownRight,
  TrendingUp, TrendingDown, Minus, Activity, Gauge, Waves,
  BarChart3, Hexagon, Target, Newspaper, ChevronUp, ChevronDown,
  Plus, Trash2, GripVertical,
} from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { useTradeStore } from '../../store/useTradeStore';
import { useQuantStore } from '../../store/useQuantStore';
import type { ConsensusReport, SentimentPayload } from '../../store/useQuantStore';
import { hydrateWatchlist } from '../../store/useTradeStore';

// ── Static Watchlist (removed — now fully dynamic + persisted) ──────────

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

interface QuoteData {
  symbol: string;
  last_price: number;
  change: number;
  net_change: number;
  open: number; high: number; low: number; close: number; volume: number;
}

interface SearchInstrument {
  instrument_token: number;
  tradingsymbol: string;
  name: string;
  instrument_type: string;
  exchange: string;
}

// ── Helpers ─────────────────────────────────────────────────────────────

function trendColor(score: number) {
  if (score > 50) return 'text-emerald-400';
  if (score > 0) return 'text-emerald-400/70';
  if (score < -50) return 'text-rose-400';
  if (score < 0) return 'text-rose-400/70';
  return 'text-amber-400';
}

function trendBg(score: number) {
  if (score > 50) return 'bg-emerald-500';
  if (score > 0) return 'bg-emerald-500/60';
  if (score < -50) return 'bg-rose-500';
  if (score < 0) return 'bg-rose-500/60';
  return 'bg-amber-500/60';
}

function stateColor(state: string) {
  switch (state) {
    case 'OVERBOUGHT': return 'text-rose-400 bg-rose-500/10 border-rose-500/30';
    case 'OVERSOLD': return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
    case 'SQUEEZING': return 'text-amber-400 bg-amber-500/10 border-amber-500/30';
    case 'EXPANDING': return 'text-violet-400 bg-violet-500/10 border-violet-500/30';
    case 'ACCUMULATION': return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
    case 'DISTRIBUTION': return 'text-rose-400 bg-rose-500/10 border-rose-500/30';
    default: return 'text-slate-400 bg-slate-500/10 border-slate-500/30';
  }
}

function sentimentImpactColor(impact: string) {
  switch (impact) {
    case 'positive': return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
    case 'negative': return 'text-rose-400 bg-rose-500/10 border-rose-500/30';
    default: return 'text-slate-400 bg-slate-500/10 border-slate-500/30';
  }
}

// ── Main Component ──────────────────────────────────────────────────────

export default function LeftPanel() {
  const [query, setQuery] = useState('');
  const [quotes, setQuotes] = useState<Record<string, QuoteData>>({});
  const [quotesLoading, setQuotesLoading] = useState(true);
  const [searchResults, setSearchResults] = useState<SearchInstrument[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [watchlistCollapsed, setWatchlistCollapsed] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);
  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const quoteIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const setSelectedSymbol = useTradeStore((s) => s.setSelectedSymbol);
  const watchlist = useTradeStore((s) => s.watchlist);
  const addToWatchlist = useTradeStore((s) => s.addToWatchlist);
  const removeFromWatchlist = useTradeStore((s) => s.removeFromWatchlist);
  const reorderWatchlist = useTradeStore((s) => s.reorderWatchlist);
  const consensusData = useQuantStore((s) => s.consensusData);
  const loadConsensusForSymbol = useQuantStore((s) => s.loadConsensusForSymbol);

  // ── Hydrate persisted watchlist on mount ───────────────────────────
  useEffect(() => {
    hydrateWatchlist();
  }, []);

  // ── Decoupled Sentiment (independent of tick data) ────────────────
  const activeSentiment = useQuantStore((s) => s.activeSentiment);
  const isFetchingSentiment = useQuantStore((s) => s.isFetchingSentiment);
  const sentimentError = useQuantStore((s) => s.sentimentError);
  const loadSentimentForSymbol = useQuantStore((s) => s.loadSentimentForSymbol);

  // Trigger sentiment fetch on symbol change — fully independent of market hours
  useEffect(() => {
    if (selectedSymbol) {
      loadSentimentForSymbol(selectedSymbol);
    }
  }, [selectedSymbol, loadSentimentForSymbol]);

  // Load cached consensus for the selected symbol (or clear if no cache exists)
  useEffect(() => {
    if (selectedSymbol) {
      loadConsensusForSymbol(selectedSymbol);
    }
  }, [selectedSymbol, loadConsensusForSymbol]);

  // ── Fetch quotes for all watchlist symbols ─────────────────────
  const fetchQuotes = useCallback(async () => {
    try {
      const allSymbols = useTradeStore.getState().watchlist.map((w) => w.symbol);
      if (allSymbols.length === 0) { setQuotesLoading(false); return; }

      const params = allSymbols.map((s) => `i=NSE:${s}`).join('&');
      const res = await fetch(`/kite/quote?${params}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.quotes) {
        const map: Record<string, QuoteData> = {};
        for (const q of data.quotes) {
          map[q.symbol] = q;
          useTradeStore.getState().updateWatchlistQuote(q.symbol, q.last_price, q.change);
        }
        setQuotes(map);
      }
    } catch (err) {
      console.error('[LeftPanel] Quote fetch failed:', err);
    } finally {
      setQuotesLoading(false);
    }
  }, []);

  const fetchQuotesRef = useRef(fetchQuotes);
  useEffect(() => { fetchQuotesRef.current = fetchQuotes; }, [fetchQuotes]);
  useEffect(() => {
    fetchQuotesRef.current();
    quoteIntervalRef.current = setInterval(() => fetchQuotesRef.current(), 30_000);
    return () => { if (quoteIntervalRef.current) clearInterval(quoteIntervalRef.current); };
  }, []);

  // Re-fetch quotes immediately when a new symbol is added to the dynamic watchlist
  const watchlistLength = watchlist.length;
  useEffect(() => {
    if (watchlistLength > 0) {
      fetchQuotesRef.current();
    }
  }, [watchlistLength]);

  // ── Search via Tauri IPC (local SQLite) ─────────────────────────
  const handleSearch = useCallback(async (searchQuery: string) => {
    const normalized = searchQuery.trim();
    if (normalized.length < 2) { setSearchResults([]); setShowDropdown(false); setIsSearching(false); return; }
    setIsSearching(true); setShowDropdown(true);
    try {
      const results = await invoke<SearchInstrument[]>('search_instruments', { query: normalized });
      setSearchResults(results || []);
    } catch (err) {
      console.error('[LeftPanel] search_instruments failed:', err);
      setSearchResults([]);
    }
    finally { setIsSearching(false); }
  }, []);

  const handleInputChange = (value: string) => {
    setQuery(value);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    if (!value.trim() || value.trim().length < 2) { setSearchResults([]); setShowDropdown(false); return; }
    searchTimeoutRef.current = setTimeout(() => handleSearch(value), 400);
  };

  const clearSearch = () => { setQuery(''); setSearchResults([]); setShowDropdown(false); };

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) setShowDropdown(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => { return () => { if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current); }; }, []);

  const formatPrice = (price: number) => price ? '₹' + price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
  const formatChange = (change: number) => `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;

  // Active symbol quote for header
  const activeQuote = quotes[selectedSymbol] || null;

  return (
    <div className="flex h-full flex-col select-none">

      {/* ══════════════════════════════════════════════════════════════
          TOP SECTION — Search + Watchlist (collapsible)
         ══════════════════════════════════════════════════════════════ */}
      <div className="shrink-0 border-b border-border-default">
        {/* Search */}
        <div className="px-3 pt-2 pb-1.5">
          <div className="relative" ref={dropdownRef}>
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
            <input
              value={query}
              onChange={(e) => handleInputChange(e.target.value)}
              onFocus={() => { if (searchResults.length > 0) setShowDropdown(true); }}
              placeholder="Search NSE symbol..."
              aria-label="Search symbols"
              className="h-8 w-full rounded-md border border-border-default bg-surface pl-8 pr-8 text-[11px] text-text-primary placeholder:text-text-muted transition-colors focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
            {query && (
              <button onClick={clearSearch} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary transition-colors" aria-label="Clear search">
                <X size={13} />
              </button>
            )}
            {showDropdown && (
              <div className="absolute left-0 right-0 top-full z-50 mt-1 max-h-52 overflow-y-auto rounded-lg border border-border-default bg-surface shadow-lg panel-shadow">
                {isSearching ? (
                  <div className="flex items-center justify-center gap-2 px-3 py-4">
                    <Loader2 size={13} className="animate-spin text-primary" />
                    <span className="text-[11px] text-text-secondary">Searching...</span>
                  </div>
                ) : searchResults.length === 0 ? (
                  <div className="px-3 py-4 text-center text-[11px] text-text-muted">No instruments found</div>
                ) : (
                  searchResults.map((inst) => (
                    <button
                      key={inst.instrument_token}
                      type="button"
                      onClick={() => {
                        // Add to dynamic watchlist + select
                        addToWatchlist({
                          symbol: inst.tradingsymbol,
                          token: inst.instrument_token,
                          name: inst.name || inst.tradingsymbol,
                          sector: inst.instrument_type || 'EQ',
                          lastPrice: 0,
                          change: 0,
                        });
                        setSelectedSymbol(inst.tradingsymbol);
                        setShowDropdown(false);
                        setQuery('');
                        setSearchResults([]);
                      }}
                      className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left transition-colors hover:bg-elevated/70"
                    >
                      <div className="flex flex-col min-w-0">
                        <span className="text-[11px] font-semibold text-text-primary truncate">{inst.tradingsymbol}</span>
                        <span className="text-[9px] text-text-muted truncate">{inst.name}</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <Plus size={10} className="text-primary" />
                        <span className="rounded px-1 py-px text-[7px] font-semibold uppercase tracking-wider bg-elevated text-text-muted">{inst.instrument_type || 'EQ'}</span>
                      </div>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
        </div>

        {/* Watchlist toggle */}
        <button
          type="button"
          onClick={() => setWatchlistCollapsed(!watchlistCollapsed)}
          className="flex w-full items-center justify-between px-3 py-1 text-[9px] font-bold uppercase tracking-widest text-text-muted/60 hover:text-text-muted transition-colors"
        >
          <span>Watchlist</span>
          {watchlistCollapsed ? <ChevronDown size={10} /> : <ChevronUp size={10} />}
        </button>
      </div>

      {/* Unified watchlist — all items are draggable, removable, persisted */}
      {!watchlistCollapsed && (
        <div className="shrink-0 max-h-[240px] overflow-y-auto scrollbar-thin border-b border-border-default">
          {quotesLoading ? (
            <div className="flex items-center justify-center gap-2 py-4">
              <Loader2 size={14} className="animate-spin text-primary" />
              <span className="text-[10px] text-text-secondary">Loading...</span>
            </div>
          ) : watchlist.length === 0 ? (
            <div className="flex items-center justify-center py-6">
              <p className="text-[10px] text-text-muted/60 italic">Search and add symbols to your watchlist</p>
            </div>
          ) : (
            watchlist.map((item, idx) => {
              const isActive = selectedSymbol === item.symbol;
              const quote = quotes[item.symbol];
              const isPositive = quote ? quote.change >= 0 : item.change >= 0;
              const sectorColor = SECTOR_COLORS[item.sector] ?? SECTOR_COLORS['EQ'] ?? 'bg-slate-500/10 text-slate-400';
              const isDragging = dragIndex === idx;
              const isDragOver = dragOverIndex === idx;

              return (
                <div
                  key={item.symbol}
                  draggable
                  onDragStart={() => setDragIndex(idx)}
                  onDragOver={(e) => { e.preventDefault(); setDragOverIndex(idx); }}
                  onDragLeave={() => setDragOverIndex(null)}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (dragIndex !== null && dragIndex !== idx) {
                      reorderWatchlist(dragIndex, idx);
                    }
                    setDragIndex(null);
                    setDragOverIndex(null);
                  }}
                  onDragEnd={() => { setDragIndex(null); setDragOverIndex(null); }}
                  className={`group flex w-full items-center gap-1 px-1.5 py-1.5 text-[11px] text-left transition-all border-l-2 ${
                    isDragging ? 'opacity-40 scale-95' : ''
                  } ${isDragOver ? 'bg-primary/5 border-t-2 border-t-primary/40' : ''} ${
                    isActive
                      ? 'bg-primary/10 border-primary text-text-primary'
                      : 'hover:bg-elevated/70 border-transparent hover:border-primary/50'
                  }`}
                >
                  {/* Drag handle */}
                  <div className="shrink-0 cursor-grab opacity-0 group-hover:opacity-60 transition-opacity active:cursor-grabbing">
                    <GripVertical size={10} className="text-text-muted" />
                  </div>

                  {/* Symbol + sector */}
                  <button
                    type="button"
                    onClick={() => setSelectedSymbol(item.symbol)}
                    className="flex items-center gap-1.5 min-w-0 flex-1 cursor-pointer"
                  >
                    <span className="font-semibold text-text-primary truncate">{item.symbol}</span>
                    <span className={`rounded px-1 py-px text-[6px] font-semibold uppercase tracking-wider ${sectorColor}`}>
                      {item.sector}
                    </span>
                  </button>

                  {/* Price + change */}
                  <div className="flex items-center gap-1 shrink-0">
                    {quote ? (
                      <>
                        <span className="font-semibold text-text-primary tabular-nums text-[10px]">{formatPrice(quote.last_price)}</span>
                        <span className={`flex items-center gap-px text-[9px] font-medium tabular-nums ${isPositive ? 'text-bull' : 'text-bear'}`}>
                          {isPositive ? <ArrowUpRight size={8} /> : <ArrowDownRight size={8} />}
                          {formatChange(quote.change)}
                        </span>
                      </>
                    ) : item.lastPrice > 0 ? (
                      <>
                        <span className="font-semibold text-text-primary tabular-nums text-[10px]">{formatPrice(item.lastPrice)}</span>
                        <span className={`flex items-center gap-px text-[9px] font-medium tabular-nums ${isPositive ? 'text-bull' : 'text-bear'}`}>
                          {isPositive ? <ArrowUpRight size={8} /> : <ArrowDownRight size={8} />}
                          {formatChange(item.change)}
                        </span>
                      </>
                    ) : (
                      <span className="text-[9px] text-text-muted/50">—</span>
                    )}

                    {/* Remove button — visible on hover */}
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); removeFromWatchlist(item.symbol); }}
                      className="opacity-0 group-hover:opacity-100 ml-0.5 p-0.5 rounded text-text-muted hover:text-rose-400 hover:bg-rose-500/10 transition-all"
                      aria-label={`Remove ${item.symbol} from watchlist`}
                    >
                      <Trash2 size={9} />
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}

      {/* ══════════════════════════════════════════════════════════════
          BOTTOM SECTION — Live Asset HUD (Consensus + Sentiment)
         ══════════════════════════════════════════════════════════════ */}
      <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin">
        

        {/* Sentiment section — always renders, independent of tick data */}
        <SentimentBlock sentiment={activeSentiment} isLoading={isFetchingSentiment} error={sentimentError} />

        {/* Technical consensus — requires OHLC candle data + symbol match */}
        {(() => {
          // Guard: don't show stale consensus from a different symbol
          const symbolMatch = consensusData && selectedSymbol
            ? consensusData.symbol?.toUpperCase() === selectedSymbol.toUpperCase()
            : !!consensusData; // no symbol selected = show whatever we have

          if (!consensusData || !symbolMatch) {
            return (
              <div className="flex flex-col items-center justify-center gap-3 p-4 py-6">
                <div className="relative">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-elevated border border-border-subtle">
                    <Activity size={16} className="text-text-muted animate-pulse" />
                  </div>
                  <div className="absolute -top-1 -right-1 h-2 w-2 rounded-full bg-amber-500/30 border border-amber-500/50 animate-ping" />
                </div>
                <div className="text-center">
                  <p className="text-[9px] font-semibold text-text-muted">No Technical Data for {selectedSymbol || 'symbol'}</p>
                  <p className="text-[8px] text-text-muted/50 mt-0.5">Run Deep Quant Analysis to<br />compute technical consensus</p>
                </div>
              </div>
            );
          }

          return <LiveAssetHUD data={consensusData} />;
        })()}
      </div>
    </div>
  );
}

// ── Live Asset HUD Sub-component ────────────────────────────────────────

function LiveAssetHUD({ data }: { data: ConsensusReport }) {
  const { symbol, trend_score, momentum_state, volatility_state, volume_flow_state, active_patterns, active_strategies } = data;
  const gaugePercent = Math.round(((trend_score + 100) / 200) * 100);

  const stateEntries = [
    { label: 'Momentum', value: momentum_state, icon: <Gauge size={10} /> },
    { label: 'Volatility', value: volatility_state, icon: <Waves size={10} /> },
    { label: 'Vol Flow', value: volume_flow_state, icon: <BarChart3 size={10} /> },
  ];

  return (
    <div className="flex flex-col text-sm">

      {/* ── Section 1: Technical Consensus ──────────────────── */}
      <div className="border-b border-border-default px-3 py-2.5">
        <div className="flex items-center gap-1.5 mb-2">
          <TrendingUp size={10} className="text-text-muted" />
          <h3 className="text-[9px] font-bold text-text-secondary uppercase tracking-wider">Technical Consensus</h3>
          {symbol && (
            <span className="ml-auto rounded px-1.5 py-px text-[7px] font-bold uppercase tracking-wider bg-blue-500/10 text-blue-400 border border-blue-500/20">
              {symbol}
            </span>
          )}
        </div>

        {/* Trend Score */}
        <div className="flex items-center gap-2.5 mb-2">
          <div className={`text-2xl font-black tabular-nums tracking-tight ${trendColor(trend_score)}`}>
            {trend_score > 0 ? '+' : ''}{trend_score}
          </div>
          <div className="flex-1 flex flex-col gap-0.5">
            <div className="flex items-center justify-between">
              <span className={`text-[9px] font-bold uppercase tracking-wider ${trendColor(trend_score)}`}>
                {trend_score > 50 ? 'STRONG BULL' : trend_score > 0 ? 'BULLISH' : trend_score < -50 ? 'STRONG BEAR' : trend_score < 0 ? 'BEARISH' : 'NEUTRAL'}
              </span>
              <span className="text-[8px] text-text-muted tabular-nums">{gaugePercent}%</span>
            </div>
            <div className="relative h-1.5 w-full rounded-full bg-elevated overflow-hidden">
              <div className={`h-1.5 rounded-full transition-all duration-700 ease-out ${trendBg(trend_score)}`} style={{ width: `${gaugePercent}%` }} />
              <div className="absolute top-0 left-1/2 -translate-x-px w-0.5 h-1.5 bg-text-muted/30" />
            </div>
          </div>
        </div>

        {/* State Badges */}
        <div className="flex flex-col gap-1">
          {stateEntries.map(({ label, value, icon }) => (
            <div key={label} className="flex items-center justify-between">
              <div className="flex items-center gap-1 text-[10px] text-text-secondary">
                {icon}
                <span className="font-medium">{label}</span>
              </div>
              <span className={`inline-flex items-center rounded px-1.5 py-px text-[8px] font-bold border ${stateColor(value)}`}>{value}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Section 2: Active Patterns ──────────────────────── */}
      <div className="border-b border-border-default px-3 py-2">
        <div className="flex items-center gap-1.5 mb-1.5">
          <Hexagon size={10} className="text-text-muted" />
          <h3 className="text-[9px] font-bold text-text-secondary uppercase tracking-wider">Patterns</h3>
          {active_patterns.length > 0 && (
            <span className="ml-auto flex h-3.5 w-3.5 items-center justify-center rounded-full bg-slate-500/20 text-[8px] font-bold text-slate-400">
              {active_patterns.length}
            </span>
          )}
        </div>
        {active_patterns.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {active_patterns.map((p) => (
              <span key={p} className="inline-flex items-center gap-0.5 rounded px-1.5 py-px text-[8px] font-semibold bg-slate-500/8 text-slate-400 border border-slate-500/20">
                {p.includes('Bullish') || p === 'Hammer' ? <TrendingUp size={7} /> : p.includes('Bearish') || p === 'Shooting Star' ? <TrendingDown size={7} /> : <Minus size={7} />}
                {p}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[9px] text-text-muted/50 italic">No patterns detected</p>
        )}
      </div>

      {/* ── Section 2b: Active Strategies ───────────────────── */}
      <div className="border-b border-border-default px-3 py-2">
        <div className="flex items-center gap-1.5 mb-1.5">
          <Target size={10} className="text-blue-400" />
          <h3 className="text-[9px] font-bold text-blue-400 uppercase tracking-wider">Strategies</h3>
          {active_strategies.length > 0 && (
            <span className="ml-auto flex h-3.5 w-3.5 items-center justify-center rounded-full bg-blue-500/20 text-[8px] font-bold text-blue-400 animate-pulse">
              {active_strategies.length}
            </span>
          )}
        </div>
        {active_strategies.length > 0 ? (
          <div className="flex flex-col gap-1">
            {active_strategies.map((s) => (
              <div key={s} className="flex items-center gap-1.5 rounded-md px-2 py-1 border border-blue-500/30 bg-blue-500/5 transition-colors hover:bg-blue-500/10">
                <div className="flex h-4 w-4 shrink-0 items-center justify-center rounded bg-blue-500/15">
                  {s.includes('Bullish') || s.includes('Golden') ? <TrendingUp size={8} className="text-blue-400" /> : <TrendingDown size={8} className="text-blue-400" />}
                </div>
                <span className="text-[10px] font-semibold text-blue-300">{s}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[9px] text-text-muted/50 italic">No strategies active</p>
        )}
      </div>
    </div>
  );
}

// ── Decoupled Sentiment Block (renders independently of tick data) ───────

function SentimentBlock({ sentiment, isLoading, error }: {
  sentiment: SentimentPayload | null;
  isLoading: boolean;
  error: string | null;
}) {
  const [headlinesExpanded, setHeadlinesExpanded] = useState(false);

  return (
    <div className="border-b border-border-default px-3 py-2.5">
      <div className="flex items-center gap-1.5 mb-1.5">
        <Newspaper size={10} className="text-text-muted" />
        <h3 className="text-[9px] font-bold text-text-secondary uppercase tracking-wider">AI News Sentiment</h3>
        {isLoading && (
          <Loader2 size={9} className="ml-auto animate-spin text-blue-400" />
        )}
        {sentiment && !isLoading && (
          <span className="ml-auto text-[8px] text-text-muted tabular-nums">
            {sentiment.headlines.length} headlines
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 rounded-md px-2 py-2 bg-blue-500/5 border border-blue-500/20">
          <div className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
          <p className="text-[9px] text-blue-300/80 font-medium">Analyzing latest news...</p>
        </div>
      ) : error ? (
        <div className="flex items-center gap-2 rounded-md px-2 py-2 bg-rose-500/5 border border-rose-500/20">
          <div className="h-1.5 w-1.5 rounded-full bg-rose-400" />
          <p className="text-[9px] text-rose-300/80 font-medium truncate">{error}</p>
        </div>
      ) : sentiment ? (
        <div className="flex flex-col gap-2">
          {/* ── Summary Score ─────────────────────────────────── */}
          <div className={`rounded-lg px-2.5 py-2 border ${
            sentiment.impact === 'positive'
              ? 'border-emerald-500/25 bg-emerald-500/5'
              : sentiment.impact === 'negative'
                ? 'border-rose-500/25 bg-rose-500/5'
                : 'border-slate-500/25 bg-slate-500/5'
          }`}>
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-1.5">
                <span className={`text-xl font-black tabular-nums ${
                  sentiment.impact === 'positive' ? 'text-emerald-400' :
                  sentiment.impact === 'negative' ? 'text-rose-400' : 'text-slate-400'
                }`}>
                  {sentiment.score > 0 ? '+' : ''}{sentiment.score}
                </span>
                <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[8px] font-bold border ${sentimentImpactColor(sentiment.impact)}`}>
                  {sentiment.label}
                </span>
              </div>
              <span className={`h-2 w-2 rounded-full ${
                sentiment.impact === 'positive' ? 'bg-emerald-400 animate-pulse' :
                sentiment.impact === 'negative' ? 'bg-rose-400 animate-pulse' : 'bg-slate-500'
              }`} />
            </div>
            <p className={`text-[9px] leading-relaxed font-medium ${
              sentiment.impact === 'positive' ? 'text-emerald-300/90' :
              sentiment.impact === 'negative' ? 'text-rose-300/90' : 'text-slate-300/90'
            }`}>
              {sentiment.top_headline}
            </p>
          </div>

          {/* ── Headlines Toggle + Scrollable List ────────────── */}
          {sentiment.headlines.length > 0 && (
            <div className="flex flex-col">
              <button
                type="button"
                onClick={() => setHeadlinesExpanded(!headlinesExpanded)}
                className="flex w-full items-center justify-between py-1 text-[8px] font-bold uppercase tracking-wider text-text-muted/60 hover:text-text-muted transition-colors"
              >
                <span>Headlines ({sentiment.headlines.length})</span>
                {headlinesExpanded ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
              </button>

              {headlinesExpanded && (
                <div className="flex flex-col gap-0.5 max-h-[240px] overflow-y-auto scrollbar-thin pr-0.5 mt-0.5">
                  {sentiment.headlines.map((headline, i) => (
                    <div
                      key={i}
                      className="group flex items-start gap-1.5 rounded-md px-2 py-1.5 border border-border-default/50 bg-elevated/30 hover:bg-elevated/60 transition-colors"
                    >
                      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded bg-slate-500/15 text-[7px] font-bold text-slate-500 mt-px">
                        {i + 1}
                      </span>
                      <p className="text-[9px] leading-snug text-text-secondary group-hover:text-text-primary transition-colors">
                        {headline}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2 rounded-md px-2 py-2 bg-elevated/50 border border-border-default">
          <div className="h-1.5 w-1.5 rounded-full bg-slate-500/40 animate-pulse" />
          <p className="text-[9px] text-text-muted/60 italic">Select a symbol to load sentiment</p>
        </div>
      )}
    </div>
  );
}

