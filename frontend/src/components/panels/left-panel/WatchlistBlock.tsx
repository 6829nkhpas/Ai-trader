'use client';

import React, { useState, useCallback, useRef, useEffect } from 'react';
import { ChevronUp, ChevronDown, GripVertical, Trash2, ArrowUpRight, ArrowDownRight, Loader2, RefreshCw, AlertTriangle } from 'lucide-react';
import { useTradeStore, hydrateWatchlist } from '../../../store/useTradeStore';
import { useChartUIStore } from '../../../store/useChartUIStore';
import { isFnoSymbol } from '../../../charting/symbolUtils';
import WatchlistSkeleton from './WatchlistSkeleton';
import { kiteFetch } from '../../../lib/kiteFetch';
import { bridgeInvoke } from '../../../lib/bridge';

interface ResolvedContract {
  tradingsymbol: string;
  underlying: string;
  expiry: string;
  strike: number;
  option_type: string;
}

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
  change: number | null;
  net_change: number | null;
  open: number | null; high: number | null; low: number | null; close: number | null; volume: number | null;
}

let globalDragIndex: number | null = null;

/** Ceiling for a quote batch, so a hung gateway can't wedge the 30s poll. */
const QUOTE_REQUEST_TIMEOUT_MS = 8000;

export default function WatchlistBlock() {
  const [quotes, setQuotes] = useState<Record<string, QuoteData>>({});
  const [quotesLoading, setQuotesLoading] = useState(true);
  /** Why the prices on screen are not current, when they are not. */
  const [quotesError, setQuotesError] = useState<string | null>(null);
  const [watchlistCollapsed, setWatchlistCollapsed] = useState(false);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null);

  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const setSelectedSymbol = useTradeStore((s) => s.setSelectedSymbol);
  const activeProfile = useTradeStore((s) => s.activeProfile);
  const setFnoUnderlying = useTradeStore((s) => s.setFnoUnderlying);
  const watchlist = useTradeStore((s) => s.watchlist);
  const removeFromWatchlist = useTradeStore((s) => s.removeFromWatchlist);
  const reorderWatchlist = useTradeStore((s) => s.reorderWatchlist);

  const splitView = useChartUIStore((s) => s.splitView);
  const activePaneId = useChartUIStore((s) => s.activePaneId);
  const setPaneSymbol = useChartUIStore((s) => s.setPaneSymbol);
  const panes = useChartUIStore((s) => s.panes);

  const routeSymbolToChart = useCallback(
    async (symbol: string) => {
      // Read the LIVE active pane id at call time. The hook closure can
      // capture a stale `activePaneId` (e.g. the user clicks the other pane
      // after this callback was created); routing with the stale id would
      // update the wrong chart. Always read the current value from the store.
      const currentActivePaneId = useChartUIStore.getState().activePaneId;
      // In F&O mode, when the user clicks an underlying/equity that is NOT
      // already a tradable option contract, resolve the nearest CE/PE contract
      // (nearest expiry, ATM strike) and chart THAT. If resolution fails we
      // fall back to charting the symbol verbatim — better a real chart than
      // an empty placeholder.
      if (activeProfile === 'FNO' && !isFnoSymbol(symbol)) {
        try {
          const resolved = await bridgeInvoke<ResolvedContract | null>(
            'fno_resolve_nearest_contract',
            { underlying: symbol },
          );
          if (resolved?.tradingsymbol) {
            setFnoUnderlying(symbol);
            if (splitView) {
              setPaneSymbol(currentActivePaneId, resolved.tradingsymbol);
            } else {
              setSelectedSymbol(resolved.tradingsymbol);
            }
            return;
          }
        } catch (err) {
          console.warn('[WatchlistBlock] fno_resolve_nearest_contract failed:', err);
        }
      }

      if (splitView) {
        setPaneSymbol(currentActivePaneId, symbol);
      } else {
        setSelectedSymbol(symbol);
      }
    },
    [
      activeProfile,
      splitView,
      setPaneSymbol,
      setSelectedSymbol,
      setFnoUnderlying,
    ],
  );

  // ── Fetch quotes for all watchlist symbols ─────────────────────
  //
  // Both failure paths used to be silent: `!res.ok` returned with no state at
  // all, and a transport error only reached the console. The prices already on
  // screen then stayed there indistinguishable from fresh ones, so a refresh
  // that failed looked like a refresh that did nothing. `quotesError` makes the
  // failure visible and gives the user a retry.
  const fetchQuotes = useCallback(async () => {
    try {
      const watchlistItems = useTradeStore.getState().watchlist;
      if (watchlistItems.length === 0) {
        setQuotesLoading(false);
        setQuotesError(null);
        return;
      }

      const params = watchlistItems
        .map((item) => {
          const sym = item.symbol.toUpperCase();
          const isFno = sym.endsWith('FUT') || ((sym.endsWith('CE') || sym.endsWith('PE')) && /\d/.test(sym));
          const exchange = isFno ? 'NFO' : 'NSE';
          return `i=${exchange}:${item.symbol}`;
        })
        .join('&');

      // Bounded, so a hung gateway cannot leave the panel spinning forever.
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), QUOTE_REQUEST_TIMEOUT_MS);
      let res: Response;
      try {
        res = await kiteFetch(`/quote?${params}`, { signal: controller.signal });
      } finally {
        clearTimeout(timeoutId);
      }

      if (!res.ok) {
        setQuotesError(
          res.status === 401 || res.status === 403
            ? 'Broker session expired — reconnect your broker to resume live prices.'
            : `Quotes unavailable (HTTP ${res.status}).`,
        );
        return;
      }

      // Typed at the boundary: `res.json()` is `any`, so without this annotation
      // every field below is unchecked and a null `change` would flow silently
      // into a `number` slot.
      const data = (await res.json()) as { quotes?: QuoteData[] };
      if (data.quotes) {
        const map: Record<string, QuoteData> = {};
        for (const q of data.quotes) {
          map[q.symbol] = q;
          useTradeStore.getState().updateWatchlistQuote(q.symbol, q.last_price, q.change);
        }
        setQuotes(map);
        setQuotesError(null);
      }
    } catch (err) {
      const aborted = err instanceof DOMException && err.name === 'AbortError';
      const message = aborted
        ? 'Quote request timed out.'
        : err instanceof Error
          ? err.message
          : String(err);
      console.error('[WatchlistBlock] Quote fetch failed:', err);
      setQuotesError(message);
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

  // `null` means the upstream did not report it — render an em-dash rather than
// standing in a zero, which would read as a real reading of 0.
  const formatPrice = (price: number | null) => price ? '₹' + price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—';
  const formatChange = (change: number | null) =>
    change === null ? '—' : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;

  return (
    <div className="shrink-0 flex flex-col gap-0 border-b border-border-default">
      {/* Watchlist toggle header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-surface/50 border-b border-border-subtle">
        <button
          type="button"
          onClick={() => setWatchlistCollapsed(!watchlistCollapsed)}
          className="flex w-full items-center justify-between px-1 py-0.5 text-[10px] font-bold uppercase tracking-widest text-text-muted/70 hover:text-text-primary transition-colors"
        >
          <span>Watchlist</span>
          <ChevronDown size={12} className={`transition-transform duration-300 ${watchlistCollapsed ? '' : 'rotate-180'}`} />
        </button>
        {/* Manual refresh. There was no way to re-request quotes other than
            waiting out the 30s poll, which is why a failed fetch read as
            "refresh is broken" — there was nothing to press. */}
        <button
          type="button"
          onClick={() => { setQuotesLoading(true); void fetchQuotes(); }}
          disabled={quotesLoading}
          aria-label="Refresh watchlist quotes"
          title={quotesError ?? 'Refresh quotes'}
          className={`ml-1 shrink-0 rounded p-0.5 transition-colors disabled:opacity-40 ${
            quotesError
              ? 'text-amber-500 hover:bg-amber-500/10 dark:text-amber-400'
              : 'text-text-muted hover:bg-elevated hover:text-text-primary'
          }`}
        >
          <RefreshCw size={11} className={quotesLoading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Quote failure banner — the prices below are the last good ones. */}
      {quotesError && !watchlistCollapsed && (
        <div
          role="status"
          className="flex items-start gap-1.5 border-b border-amber-500/25 bg-amber-500/5 px-3 py-1.5"
        >
          <AlertTriangle size={9} className="mt-px shrink-0 text-amber-500 dark:text-amber-400" />
          <div className="min-w-0">
            <p className="text-[9px] font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400">
              Prices not live
            </p>
            <p className="text-[9px] leading-normal text-amber-700/90 dark:text-amber-300/80 break-words">
              {quotesError}
            </p>
          </div>
        </div>
      )}

      {/* Watchlist content with smooth CSS Grid expand/collapse animation */}
      <div
        className={`grid transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] border-b border-border-default ${
          watchlistCollapsed ? 'grid-rows-[0fr] opacity-0 pointer-events-none' : 'grid-rows-[1fr] opacity-100'
        }`}
      >
        <div className="overflow-hidden min-h-0">
          <div className="shrink-0 max-h-[300px] overflow-y-auto scrollbar-thin">
            {quotesLoading ? (
              <WatchlistSkeleton rows={5} />
            ) : watchlist.length === 0 ? (
              <div className="flex items-center justify-center py-6">
                <p className="text-xs text-text-muted/60 italic">Search and add symbols to your watchlist</p>
              </div>
            ) : (
            watchlist.map((item, idx) => {
              const chartedSymbol = splitView
                ? panes.find((p) => p.id === activePaneId)?.symbol
                : selectedSymbol;
              const isActive = chartedSymbol === item.symbol;
              const quote = quotes[item.symbol];
              // `null` when the upstream reported no previous close, in which case
              // there is no direction to show — no arrow, no bull/bear colour.
              const changeVal: number | null = quote ? quote.change : item.change;
              const isPositive = changeVal !== null && changeVal >= 0;
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
                  onClick={() => routeSymbolToChart(item.symbol)}
                  className={`group flex w-full items-center justify-between gap-2 px-2.5 py-2 text-left cursor-pointer transition-all border-l-2 ${
                    isDragging ? 'opacity-40 scale-95' : ''
                  } ${isDragOver ? 'bg-primary/5 border-t-2 border-t-primary/40' : ''} ${
                    isActive
                      ? 'bg-primary/10 border-primary text-text-primary'
                      : 'hover:bg-elevated/70 border-transparent hover:border-primary/50'
                  }`}
                >
                  {/* Reorder Grip Handle — hidden by default, expands on hover without overlapping text */}
                  <div className="w-0 group-hover:w-4 opacity-0 group-hover:opacity-75 transition-all overflow-hidden shrink-0 flex items-center justify-center cursor-grab active:cursor-grabbing -ml-1 group-hover:mr-1">
                    <GripVertical size={13} className="text-text-muted" />
                  </div>

                  {(() => {
                    const isFnoItem = item.sector === 'CE' || item.sector === 'PE' || item.sector === 'FUT';
                    const displayName = (isFnoItem ? (item.name || item.symbol) : item.symbol).replace(/"/g, '');
                    const subtitle = isFnoItem ? null : (item.name !== item.symbol ? item.name.replace(/"/g, '') : null);

                    return (
                      <div className="flex flex-col items-start text-left min-w-0 flex-1 w-full select-none">
                        <div className="flex items-center gap-1.5 w-full min-w-0">
                          <span className="font-extrabold text-[13px] text-text-primary truncate">{displayName}</span>
                          <span className={`rounded-sm px-1.5 py-0.5 text-[8px] font-extrabold uppercase tracking-wider ${sectorColor} shrink-0`}>
                            {item.sector}
                          </span>
                        </div>
                        {subtitle && (
                          <span className="text-[10px] font-medium text-text-muted/80 truncate mt-0.5 w-full">
                            {subtitle}
                          </span>
                        )}
                      </div>
                    );
                  })()}

                  {/* Price & Change % — visible by default, hidden on hover */}
                  <div className="flex flex-col items-end justify-center gap-0.5 shrink-0 min-w-[75px] group-hover:hidden transition-all">
                    {quote ? (
                      <>
                        <span className="font-extrabold text-text-primary tabular-nums text-[13px]">{formatPrice(quote.last_price)}</span>
                        <span className={`flex items-center gap-0.5 text-[10px] font-bold tabular-nums ${changeVal === null ? 'text-text-muted' : isPositive ? 'text-bull' : 'text-bear'}`}>
                          {changeVal !== null && (isPositive ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />)}
                          {formatChange(quote.change)}
                        </span>
                      </>
                    ) : item.lastPrice > 0 ? (
                      <>
                        <span className="font-extrabold text-text-primary tabular-nums text-[13px]">{formatPrice(item.lastPrice)}</span>
                        <span className={`flex items-center gap-0.5 text-[10px] font-bold tabular-nums ${isPositive ? 'text-bull' : 'text-bear'}`}>
                          {isPositive ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}
                          {formatChange(item.change)}
                        </span>
                      </>
                    ) : (
                      <span className="text-xs text-text-muted/50 font-medium">—</span>
                    )}
                  </div>

                  {/* Trash Delete Button — hidden by default, replaces price on hover */}
                  <div className="hidden group-hover:flex items-center justify-end shrink-0 min-w-[75px] transition-all">
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); removeFromWatchlist(item.symbol); }}
                      className="p-1.5 rounded-md text-text-muted hover:text-rose-500 hover:bg-rose-500/10 transition-colors flex items-center justify-center"
                      title={`Remove ${item.symbol} from watchlist`}
                      draggable={false}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              );
            })
          )}
          </div>
        </div>
      </div>
    </div>
  );
}
