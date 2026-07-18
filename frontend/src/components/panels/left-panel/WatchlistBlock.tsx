'use client';

import React, { useState, useCallback, useRef, useEffect } from 'react';
import { ChevronUp, ChevronDown, GripVertical, Trash2, ArrowUpRight, ArrowDownRight, Loader2 } from 'lucide-react';
import { useTradeStore, hydrateWatchlist } from '../../../store/useTradeStore';
import { useChartUIStore } from '../../../store/useChartUIStore';
import WatchlistSkeleton from './WatchlistSkeleton';

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

let globalDragIndex: number | null = null;

export default function WatchlistBlock() {
  const [quotes, setQuotes] = useState<Record<string, QuoteData>>({});
  const [quotesLoading, setQuotesLoading] = useState(true);
  const [watchlistCollapsed, setWatchlistCollapsed] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const setSelectedSymbol = useTradeStore((s) => s.setSelectedSymbol);
  const watchlist = useTradeStore((s) => s.watchlist);
  const removeFromWatchlist = useTradeStore((s) => s.removeFromWatchlist);
  const reorderWatchlist = useTradeStore((s) => s.reorderWatchlist);

  const splitView = useChartUIStore((s) => s.splitView);
  const activePaneId = useChartUIStore((s) => s.activePaneId);
  const setPaneSymbol = useChartUIStore((s) => s.setPaneSymbol);
  const panes = useChartUIStore((s) => s.panes);

  const routeSymbolToChart = useCallback((symbol: string) => {
    if (splitView) {
      setPaneSymbol(activePaneId, symbol);
    } else {
      setSelectedSymbol(symbol);
    }
  }, [splitView, setPaneSymbol, activePaneId, setSelectedSymbol]);

  // ── Fetch quotes for all watchlist symbols ─────────────────────
  const fetchQuotes = useCallback(async () => {
    try {
      const watchlistItems = useTradeStore.getState().watchlist;
      if (watchlistItems.length === 0) { setQuotesLoading(false); return; }

      const params = watchlistItems
        .map((item) => {
          const sym = item.symbol.toUpperCase();
          const isFno = sym.endsWith('FUT') || ((sym.endsWith('CE') || sym.endsWith('PE')) && /\d/.test(sym));
          const exchange = isFno ? 'NFO' : 'NSE';
          return `i=${exchange}:${item.symbol}`;
        })
        .join('&');

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
      console.error('[WatchlistBlock] Quote fetch failed:', err);
    } finally {
      setQuotesLoading(false);
    }
  }, []);

  const fetchQuotesRef = useRef(fetchQuotes);
  useEffect(() => { fetchQuotesRef.current = fetchQuotes; }, [fetchQuotes]);

  useEffect(() => {
    const init = async () => {
      await hydrateWatchlist();
      fetchQuotesRef.current();
    };
    init();
    const quoteInterval = setInterval(() => fetchQuotesRef.current(), 30_000);
    return () => clearInterval(quoteInterval);
  }, []);

  // Re-fetch quotes immediately when a new symbol is added to the dynamic watchlist
  const watchlistLength = watchlist.length;
  useEffect(() => {
    if (watchlistLength > 0) {
      fetchQuotesRef.current();
    }
  }, [watchlistLength]);

  const formatPrice = (price: number) => price ? '₹' + price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
  const formatChange = (change: number) => `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;

  return (
    <div className="shrink-0 flex flex-col gap-0 border-b border-border-default">
      {/* Watchlist toggle header */}
      <div className="flex items-center justify-between px-3 py-1 bg-surface/50 border-b border-border-subtle">
        <button
          type="button"
          onClick={() => setWatchlistCollapsed(!watchlistCollapsed)}
          className="flex w-full items-center justify-between px-3 py-1 text-[9px] font-bold uppercase tracking-widest text-text-muted/60 hover:text-text-muted transition-colors"
        >
          <span>Watchlist</span>
          {watchlistCollapsed ? <ChevronDown size={10} /> : <ChevronUp size={10} />}
        </button>
      </div>

      {/* Watchlist content */}
      {!watchlistCollapsed && (
        <div className="shrink-0 max-h-[240px] overflow-y-auto scrollbar-thin border-b border-border-default">
          {quotesLoading ? (
            <WatchlistSkeleton rows={5} />
          ) : watchlist.length === 0 ? (
            <div className="flex items-center justify-center py-6">
              <p className="text-[10px] text-text-muted/60 italic">Search and add symbols to your watchlist</p>
            </div>
          ) : (
            watchlist.map((item, idx) => {
              const chartedSymbol = splitView
                ? panes.find((p) => p.id === activePaneId)?.symbol
                : selectedSymbol;
              const isActive = chartedSymbol === item.symbol;
              const quote = quotes[item.symbol];
              const isPositive = quote ? quote.change >= 0 : item.change >= 0;
              const sectorColor = SECTOR_COLORS[item.sector] ?? SECTOR_COLORS['EQ'] ?? 'bg-slate-500/10 text-slate-400';
              const isDragging = dragIndex === idx;
              const isDragOver = dragOverIndex === idx;

              return (
                <div
                  key={item.symbol}
                  draggable
                  onDragStart={(e) => {
                    const target = e.target as HTMLElement;
                    if (target.closest('button') || target.closest('input')) {
                      e.preventDefault();
                      return;
                    }
                    setDragIndex(idx);
                    globalDragIndex = idx;
                    e.dataTransfer.setData('text/plain', idx.toString());
                    e.dataTransfer.effectAllowed = 'move';
                  }}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragOverIndex(idx);
                    e.dataTransfer.dropEffect = 'move';
                  }}
                  onDragLeave={() => setDragOverIndex(null)}
                  onDrop={(e) => {
                    e.preventDefault();
                    const fromIndex = globalDragIndex ?? (() => {
                      const fromIndexStr = e.dataTransfer.getData('text/plain');
                      return fromIndexStr !== '' ? parseInt(fromIndexStr, 10) : null;
                    })();

                    if (fromIndex !== null && !isNaN(fromIndex) && fromIndex !== idx) {
                      reorderWatchlist(fromIndex, idx);
                    }
                    globalDragIndex = null;
                    setDragIndex(null);
                    setDragOverIndex(null);
                  }}
                  onDragEnd={() => {
                    globalDragIndex = null;
                    setDragIndex(null);
                    setDragOverIndex(null);
                  }}
                  className={`group flex w-full items-center gap-1 px-1.5 py-1 text-[11px] text-left transition-all border-l-2 ${
                    isDragging ? 'opacity-40 scale-95' : ''
                  } ${isDragOver ? 'bg-primary/5 border-t-2 border-t-primary/40' : ''} ${
                    isActive
                      ? 'bg-primary/10 border-primary text-text-primary'
                      : 'hover:bg-elevated/70 border-transparent hover:border-primary/50'
                  }`}
                >
                  <div className="shrink-0 cursor-grab opacity-30 group-hover:opacity-75 transition-opacity active:cursor-grabbing p-1">
                    <GripVertical size={10} className="text-text-muted" />
                  </div>

                  {(() => {
                    const isFnoItem = item.sector === 'CE' || item.sector === 'PE' || item.sector === 'FUT';
                    const displayName = (isFnoItem ? (item.name || item.symbol) : item.symbol).replace(/"/g, '');
                    const subtitle = isFnoItem ? null : (item.name !== item.symbol ? item.name.replace(/"/g, '') : null);

                    return (
                      <button
                        type="button"
                        onClick={() => routeSymbolToChart(item.symbol)}
                        className="flex flex-col items-start text-left min-w-0 flex-1 cursor-pointer w-full"
                        draggable={false}
                      >
                        <div className="flex items-center gap-1.5 w-full min-w-0">
                          <span className="font-semibold text-text-primary truncate">{displayName}</span>
                          <span className={`rounded px-1 py-px text-[6px] font-semibold uppercase tracking-wider ${sectorColor} shrink-0`}>
                            {item.sector}
                          </span>
                        </div>
                        {subtitle && (
                          <span className="text-[9px] text-text-muted truncate mt-0.5 w-full">
                            {subtitle}
                          </span>
                        )}
                      </button>
                    );
                  })()}

                  <div className="flex flex-col items-end justify-center gap-0.5 shrink-0 min-w-[75px]">
                    {quote ? (
                      <>
                        <span className="font-bold text-text-primary tabular-nums text-[11px]">{formatPrice(quote.last_price)}</span>
                        <span className={`flex items-center gap-px text-[9px] font-semibold tabular-nums ${isPositive ? 'text-bull' : 'text-bear'}`}>
                          {isPositive ? <ArrowUpRight size={8} /> : <ArrowDownRight size={8} />}
                          {formatChange(quote.change)}
                        </span>
                      </>
                    ) : item.lastPrice > 0 ? (
                      <>
                        <span className="font-bold text-text-primary tabular-nums text-[11px]">{formatPrice(item.lastPrice)}</span>
                        <span className={`flex items-center gap-px text-[9px] font-semibold tabular-nums ${isPositive ? 'text-bull' : 'text-bear'}`}>
                          {isPositive ? <ArrowUpRight size={8} /> : <ArrowDownRight size={8} />}
                          {formatChange(item.change)}
                        </span>
                      </>
                    ) : (
                      <span className="text-[10px] text-text-muted/50 font-medium">—</span>
                    )}
                  </div>

                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); removeFromWatchlist(item.symbol); }}
                    className="opacity-0 group-hover:opacity-100 ml-0.5 p-0.5 rounded text-text-muted hover:text-rose-400 hover:bg-rose-500/10 transition-all"
                    aria-label={`Remove ${item.symbol} from watchlist`}
                    draggable={false}
                  >
                    <Trash2 size={9} />
                  </button>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
