'use client';

import React, { useEffect, useState, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { BarChart3 } from 'lucide-react';
import { useTradeStore } from '../store/useTradeStore';
import { crossfade } from '../lib/motionVariants';
import { bridgeListen } from '../lib/bridge';
import { kiteFetch } from '../lib/kiteFetch';

import {
  type OrderBookState,
  createEmptyBook,
  depthPercent,
  formatSize,
  buildBookFromDepth,
  buildBookFromKiteDepth,
  parseCachedBook,
  BOOK_CACHE_VERSION,
} from './orderbook/orderBookHelpers';

/** Depth refresh cadence. Kite allows ~3 req/s and the watchlist shares it. */
const DEPTH_POLL_MS = 2000;

/**
 * Per-request ceiling for a depth fetch.
 *
 * Without this the poll had no timeout and no AbortController, so a request that
 * never settled meant the `finally` never ran, the next tick was never scheduled,
 * and the panel sat on "Awaiting Market Depth Data…" indefinitely with no error —
 * the reported "order book takes forever to load". Shorter than the poll interval
 * so a hung request is abandoned before the next one is due.
 */
const DEPTH_REQUEST_TIMEOUT_MS = 1800;

/** Write the cache at most this often; the poll itself runs every 2s. */
const CACHE_WRITE_MIN_INTERVAL_MS = 10_000;

const cacheKey = (symbol: string) =>
  `ai-trader-orderbook-${BOOK_CACHE_VERSION}-${symbol.toUpperCase()}`;

// ── Component ──────────────────────────────────────────────────────────
export default function OrderBook() {
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);

  const [book, setBook] = useState<OrderBookState>(() => createEmptyBook());
  const [isLive, setIsLive] = useState(false);
  const updateCountRef = useRef(0);

  // ── Ask-ladder scroll anchoring ──────────────────────────────────────
  // `book.asks` is ordered farthest-ask-first (the builder reverses it), so the
  // BEST ask — the one that matters, sitting right above the mid price — is the
  // LAST row. A scroll container starts at scrollTop 0, i.e. showing the
  // farthest levels, which would push the best ask out of view. Anchor to the
  // bottom once per symbol, then leave the scroll position alone so scrolling up
  // to inspect deeper levels isn't yanked back on the next 2s tick.
  const asksScrollRef = useRef<HTMLDivElement>(null);
  const asksAnchoredRef = useRef(false);

  useEffect(() => {
    asksAnchoredRef.current = false; // a new symbol is a new ladder
  }, [selectedSymbol]);

  useEffect(() => {
    if (asksAnchoredRef.current || book.asks.length === 0) return;
    const el = asksScrollRef.current;
    if (!el) return;
    // One frame later, so the row heights are settled before we measure.
    const id = requestAnimationFrame(() => {
      const node = asksScrollRef.current;
      if (!node) return;
      node.scrollTop = node.scrollHeight;
      asksAnchoredRef.current = true;
    });
    return () => cancelAnimationFrame(id);
  }, [book.asks.length]);

  // ── Load cached order book data when symbol changes ──────────────────
  useEffect(() => {
    if (typeof window !== 'undefined') {
      // `parseCachedBook` validates the SHAPE, not just that the JSON parsed —
      // see its doc comment for why an unvalidated cast here crashed the app.
      let cached: string | null = null;
      try {
        cached = localStorage.getItem(cacheKey(selectedSymbol));
      } catch {
        cached = null; // private mode / storage disabled
      }
      const restored = parseCachedBook(cached);
      if (restored) {
        setBook(restored);
        // A restored book is the LAST KNOWN depth, not the current market. Saying
        // "live" here would present a cached ladder as the live one; the poll
        // below sets the flag once real depth actually arrives.
        setIsLive(false);
        return;
      }
    }
    setBook(createEmptyBook());
    setIsLive(false);
  }, [selectedSymbol]);

  // ── Listen for real-time order book data from backend IPC ──────────
  // The backend pushes depth updates via the `orderbook-update` event.
  useEffect(() => {
    let cleanup: (() => void) | undefined;

    async function setupListener() {
      try {
        // Tauri IPC on desktop; the bridge event bus in a browser. There is no
        // browser producer for `orderbook-update` yet, so the book stays in cold
        // standby there — the same visible state as before this was bridged.
        const unlisten = await bridgeListen<{
          bid_prices: number[];
          bid_sizes: number[];
          ask_prices: number[];
          ask_sizes: number[];
        }>('orderbook-update', (event) => {
          const { bid_prices, bid_sizes, ask_prices, ask_sizes } = event.payload;
          const newBook = buildBookFromDepth(bid_prices, bid_sizes, ask_prices, ask_sizes);
          setBook(newBook);
          setIsLive(true);
          updateCountRef.current += 1;

          // Cache the latest book details for this symbol (throttle to every 5th update)
          if (updateCountRef.current % 5 === 0) {
            const currentSymbol = useTradeStore.getState().selectedSymbol;
            if (typeof window !== 'undefined') {
              localStorage.setItem(`ai-trader-orderbook-${currentSymbol.toUpperCase()}`, JSON.stringify(newBook));
            }
          }
        });
        cleanup = unlisten;
      } catch {
        console.info('[OrderBook] Tauri IPC unavailable — order book is in cold standby.');
      }
    }

    setupListener();
    return () => {
      cleanup?.();
    };
  }, []);

  // ── Poll Kite REST depth ─────────────────────────────────────────────
  //
  // The `orderbook-update` listener above has NO producer on the web — it was fed
  // by the desktop app's IPC bridge, which is gone — so without this the book sat
  // in cold standby forever showing "Order book populates when live depth feed
  // connects". Kite's `/quote` has always returned five-level depth; the
  // aggregator's handler simply dropped the field until now.
  //
  // Polling rather than streaming, deliberately: there is no depth WS in this
  // stack (`/ws/*` carries decisions, candles, predictions and insights, none of
  // them order book), and a REST poll is honest about its own granularity. 2s is
  // fast enough to read as live without hammering Kite's 3 req/s ceiling — the
  // watchlist and macro strip share that budget.
  //
  // If a depth stream is added later, delete this effect rather than layering it:
  // two writers to `book` would fight, and the slower one would keep winning.
  useEffect(() => {
    const symbol = selectedSymbol?.trim();
    if (!symbol) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let lastCacheWrite = 0;

    const tick = async () => {
      // Bound every request. `kiteFetch` forwards the signal to `fetch`.
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), DEPTH_REQUEST_TIMEOUT_MS);
      try {
        const res = await kiteFetch(`/quote?i=NSE:${encodeURIComponent(symbol)}`, {
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`quote HTTP ${res.status}`);
        const data = await res.json();
        const quote = (data?.quotes ?? []).find(
          (q: { symbol?: string }) => q?.symbol?.toUpperCase() === symbol.toUpperCase(),
        ) ?? (data?.quotes ?? [])[0];

        const next = buildBookFromKiteDepth(quote?.depth);
        if (cancelled) return;

        if (next) {
          setBook(next);
          setIsLive(true);
          // Throttled: this is a synchronous JSON.stringify + localStorage write
          // on the main thread, and it does not need to happen on all 30 ticks a
          // minute. The cache only exists so a remount has something to show.
          const now = Date.now();
          if (typeof window !== 'undefined' && now - lastCacheWrite >= CACHE_WRITE_MIN_INTERVAL_MS) {
            lastCacheWrite = now;
            try {
              localStorage.setItem(cacheKey(symbol), JSON.stringify(next));
            } catch {
              /* quota exceeded — the live book is unaffected */
            }
          }
        } else {
          // A quote with no depth (Kite omits it outside full mode, and for some
          // indices entirely). Leave the last book visible but stop claiming it is
          // live, so a stale ladder is never presented as the current market.
          setIsLive(false);
        }
      } catch {
        // Transport failure or an expired broker token. Keep whatever is displayed
        // and drop the live flag — blanking the panel would imply an empty book,
        // which is a different and much stronger claim than "we cannot see it".
        if (!cancelled) setIsLive(false);
      } finally {
        clearTimeout(timeoutId);
        if (!cancelled) timer = setTimeout(tick, DEPTH_POLL_MS);
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [selectedSymbol]);

  // Every level here is a real broker-quoted level — the synthetic padding that
  // used to be filtered out at this point is gone (see `buildBookFromDepth`), so
  // the whole ladder counts toward the scaling and the bid/ask ratio.
  //
  // Memoized on `book`: these six passes over the ladder used to re-run on EVERY
  // render, including every unrelated parent re-render, not just when new depth
  // arrived.
  const { globalMaxSize, askVolPct, bidVolPct } = React.useMemo(() => {
    const maxAskSize = book.asks.length > 0 ? Math.max(...book.asks.map((l) => l.size), 0.01) : 0.01;
    const maxBidSize = book.bids.length > 0 ? Math.max(...book.bids.map((l) => l.size), 0.01) : 0.01;

    const totalAskVol = book.asks.reduce((s, l) => s + l.size, 0);
    const totalBidVol = book.bids.reduce((s, l) => s + l.size, 0);
    const totalVol = totalAskVol + totalBidVol || 1;

    return {
      globalMaxSize: Math.max(maxAskSize, maxBidSize),
      askVolPct: (totalAskVol / totalVol) * 100,
      bidVolPct: (totalBidVol / totalVol) * 100,
    };
  }, [book]);

  return (
    <div
      id="order-book-dom"
      className="flex h-full flex-col rounded-none border-0 bg-surface font-sans text-[12.5px] select-none overflow-hidden"
    >

      {/* ── Column Headers ──────────────────────────────────── */}
      <div className="grid shrink-0 grid-cols-3 gap-0 border-b border-border-default bg-elevated/30 px-3.5 py-2 text-[11px] font-extrabold text-text-muted uppercase tracking-wider font-sans">
        <span>Price</span>
        <span className="text-right">Size</span>
        <span className="text-right">Total</span>
      </div>

      {/* ── Awaiting Data State ───────────────────────────── */}
      <AnimatePresence>
        {!isLive && book.asks.length === 0 && (
          <motion.div
            variants={crossfade}
            initial="hidden"
            animate="show"
            exit="exit"
            className="flex flex-1 items-center justify-center font-sans"
          >
            <div className="flex flex-col items-center gap-2 text-center px-4">
              <div className="flex h-8 w-8 items-center justify-center rounded-none bg-elevated">
                <BarChart3 size={14} className="text-text-muted" />
              </div>
              <p className="text-[12px] font-bold text-text-muted leading-snug">
                Awaiting Market Depth Data...
              </p>
              <p className="text-[10px] text-text-muted/70">
                Order book populates when live depth feed connects
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Ask Levels (Red) — Scrollable without scrollbar ─────────── */}
      {book.asks.length > 0 && (
        // NOTE: no `justify-end` here, deliberately.
        //
        // This container used to be `flex flex-col justify-end overflow-y-auto`,
        // and that combination is unscrollable in every major browser: with
        // `justify-content: flex-end`, content that overflows does so past the
        // BLOCK-START (top) edge, and `scrollTop` cannot go below 0 — so the
        // topmost ask rows were clipped with no way to reach them. The bid side
        // never had `justify-end`, which is exactly why green scrolled and red
        // did not.
        //
        // The fix is an auto margin on the content wrapper below instead. Auto
        // margins give the same bottom alignment when there is spare room, but
        // resolve to 0 once the content overflows, so the scroll container
        // behaves normally.
        // `flex-initial` (flex: 0 1 auto), NOT `flex-1`.
        //
        // `flex-1` is `flex: 1 1 0%` — it makes each ladder claim an equal share
        // of the pane regardless of how many rows it holds. That was invisible
        // while the ladder was padded out to 14 rows a side and nearly filled its
        // half; once the padding was removed (it was fabricated — see
        // `buildBookFromDepth`) five real rows were left rattling around in a box
        // built for fourteen, with `mt-auto` pushing the asks to the bottom of
        // theirs and the bids sitting at the top of theirs. Hence the empty band
        // above the asks and below the bids: the reported "no order book depth",
        // which was really the right depth in the wrong-sized container.
        //
        // `flex-initial` sizes each ladder to its content and still lets it SHRINK
        // (with `min-h-0`) when the pane is too short, so the scrollability that
        // 97ec4ae restored is untouched. Both sides carry it, deliberately: the
        // original ask-ladder bug was a divergence between the two.
        <div
          ref={asksScrollRef}
          className="flex flex-col flex-initial min-h-0 overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none] font-sans"
        >
          {/* `mt-auto` keeps the best ask pinned just above the mid-price row
              when the ladder is shorter than the pane, without breaking scroll. */}
          <div className="mt-auto">
            {book.asks.map((level, i) => (
              <div
                key={`ask-${i}`}
                  className="group relative grid grid-cols-3 gap-0 px-3.5 py-0.75 hover:bg-red-500/10"
              >
                {/* Depth bar background */}
                <div
                  className="pointer-events-none absolute inset-y-0 right-0 bg-red-500/12"
                  style={{ width: `${depthPercent(level.size, globalMaxSize)}%` }}
                />
                <span className="relative z-10 tabular-nums font-extrabold text-[#ef4444]">
                  {level.price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </span>
                <span className="relative z-10 tabular-nums text-right font-bold text-red-400/90">
                  {formatSize(level.size)}
                </span>
                <span className="relative z-10 tabular-nums text-right font-bold text-zinc-400">
                  {formatSize(level.total)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Mid Price / Spread Floating Pill Row (FNO Spot Style) ── */}
      {book.asks.length > 0 && book.bids.length > 0 && (
        <div className="relative shrink-0 w-full text-center py-2.5 z-20 pointer-events-none font-sans">
          {/* Horizontal dividing line spanning full width */}
          <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-px bg-border-default dark:bg-zinc-700/80 z-0" />
          
          {/* Centered Theme-Adaptive Pill Badge */}
          <div className="relative z-10 inline-flex items-center gap-1.5 rounded-full bg-card dark:bg-[#373e4d] text-text-primary dark:text-white px-3.5 py-0.5 shadow-xl border border-border-default dark:border-slate-500/60 pointer-events-auto">
            <span className="text-[12px] font-black font-sans tracking-tight text-text-primary dark:text-white">
              {book.midPrice.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            <span className="text-[9.5px] font-extrabold text-text-muted dark:text-zinc-400 uppercase">MID</span>
            <span className="text-[10px] text-text-muted dark:text-zinc-400 font-light">|</span>
            <span className="text-[11px] font-extrabold text-amber-500 dark:text-amber-400 font-sans tracking-tight">
              {book.spread.toFixed(2)} ({book.spreadPct}%)
            </span>
          </div>
        </div>
      )}

      {/* ── Bid Levels (Green) — Scrollable without scrollbar ────────── */}
      {book.bids.length > 0 && (
        // `flex-initial` to match the ask ladder — see the note there for why
        // `flex-1` left five real levels floating in a fourteen-row box.
        <div className="flex flex-col flex-initial min-h-0 overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none] font-sans">
          {book.bids.map((level, i) => (
            <div
              key={`bid-${i}`}
              className="group relative grid grid-cols-3 gap-0 px-3.5 py-0.75 hover:bg-emerald-500/10"
            >
              {/* Depth bar background */}
              <div
                className="pointer-events-none absolute inset-y-0 right-0 bg-emerald-500/12"
                style={{ width: `${depthPercent(level.size, globalMaxSize)}%` }}
              />
              <span className="relative z-10 tabular-nums font-extrabold text-bull">
                {level.price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
              <span className="relative z-10 tabular-nums text-right font-bold text-emerald-400/90">
                {formatSize(level.size)}
              </span>
              <span className="relative z-10 tabular-nums text-right font-bold text-zinc-400">
                {formatSize(level.total)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ── Ask/Bid Volume Ratio Bar ────────────────────────── */}
      {book.asks.length > 0 && book.bids.length > 0 && (
        // Sits directly under the bids, NOT pinned to the panel bottom with an
        // auto margin. It summarises the ladder immediately above it, and pushing
        // it to the bottom of a tall pane opens a void between the bar and the
        // book it describes — which measured 283px in a 620px sidebar. The widget
        // now shrink-wraps its content and any spare sidebar height simply stays
        // empty below it, which is how a depth widget is normally laid out.
        <div className="shrink-0 px-3.5 py-2 border-t border-border-default bg-elevated/20 font-sans">
          <div className="flex justify-between text-[10px] font-black mb-1.5 tracking-wider font-sans">
            <span className="text-emerald-400">{bidVolPct.toFixed(1)}% BIDS</span>
            <span className="text-red-400">{askVolPct.toFixed(1)}% ASKS</span>
          </div>
          <div className="relative h-2 w-full rounded-full bg-border-default/40 flex overflow-hidden">
            {/* Bid Volume (Green) */}
            <motion.div 
              className="h-full bg-emerald-500" 
              animate={{ width: `${bidVolPct}%` }}
              transition={{ type: 'spring', stiffness: 120, damping: 18 }}
            />
            {/* Ask Volume (Red) */}
            <motion.div 
              className="h-full bg-red-500" 
              animate={{ width: `${askVolPct}%` }}
              transition={{ type: 'spring', stiffness: 120, damping: 18 }}
            />
            {/* 50/50 Divider Mark */}
            <div className="absolute inset-y-0 left-1/2 w-px bg-white/60 z-10" />
          </div>
        </div>
      )}
    </div>
  );
}
