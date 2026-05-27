'use client';

import React, { useEffect, useState } from 'react';
import { useTradeStore } from '../store/useTradeStore';

// ── Types ──────────────────────────────────────────────────────────────
interface OrderBookLevel {
  price: number;
  size: number;
  total: number;
}

interface OrderBookState {
  asks: OrderBookLevel[];
  bids: OrderBookLevel[];
  spread: number;
  spreadPct: string;
  midPrice: number;
}

// ── Constants ──────────────────────────────────────────────────────────
const LEVEL_COUNT = 10;

// ── Empty initial book (no mock data) ──────────────────────────────────
function createEmptyBook(): OrderBookState {
  return {
    asks: [],
    bids: [],
    spread: 0,
    spreadPct: '0.000',
    midPrice: 0,
  };
}

// ── Depth Bar (visual liquidity gauge) ─────────────────────────────────
function depthPercent(size: number, maxSize: number): number {
  return Math.min((size / maxSize) * 100, 100);
}

// ── Build order book from market depth data ────────────────────────────
// This function constructs book state from real market depth arrays
// received via IPC/WebSocket from the backend.
function buildBookFromDepth(
  bidPrices: number[],
  bidSizes: number[],
  askPrices: number[],
  askSizes: number[],
): OrderBookState {
  const asks: OrderBookLevel[] = [];
  const bids: OrderBookLevel[] = [];

  // Build ask levels (ascending, then reversed for display: highest at top)
  let askRunningTotal = 0;
  const askCount = Math.min(askPrices.length, LEVEL_COUNT);
  for (let i = 0; i < askCount; i++) {
    const price = askPrices[i];
    const size = askSizes[i] || 0;
    askRunningTotal += size;
    asks.push({ price, size, total: parseFloat(askRunningTotal.toFixed(4)) });
  }
  asks.reverse(); // highest at top, lowest near spread

  // Build bid levels (descending: highest first, closest to spread at top)
  let bidRunningTotal = 0;
  const bidCount = Math.min(bidPrices.length, LEVEL_COUNT);
  for (let i = 0; i < bidCount; i++) {
    const price = bidPrices[i];
    const size = bidSizes[i] || 0;
    bidRunningTotal += size;
    bids.push({ price, size, total: parseFloat(bidRunningTotal.toFixed(4)) });
  }

  const bestAsk = asks.length > 0 ? asks[asks.length - 1].price : 0;
  const bestBid = bids.length > 0 ? bids[0].price : 0;
  const spread = bestAsk > 0 && bestBid > 0 ? parseFloat((bestAsk - bestBid).toFixed(2)) : 0;
  const spreadPct = bestAsk > 0 ? ((spread / bestAsk) * 100).toFixed(3) : '0.000';
  const midPrice = bestAsk > 0 && bestBid > 0 ? parseFloat(((bestAsk + bestBid) / 2).toFixed(2)) : 0;

  return { asks, bids, spread, spreadPct, midPrice };
}

// ── Component ──────────────────────────────────────────────────────────
export default function OrderBook() {
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);

  const [book, setBook] = useState<OrderBookState>(() => createEmptyBook());
  const [isLive, setIsLive] = useState(false);

  // ── Load cached order book data when symbol changes ──────────────────
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const cached = localStorage.getItem(`ai-trader-orderbook-${selectedSymbol.toUpperCase()}`);
      if (cached) {
        try {
          const parsed = JSON.parse(cached);
          setBook(parsed);
          setIsLive(true);
          return;
        } catch {
          // ignore
        }
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
        // Tauri IPC path — native desktop mode
        const { listen } = await import('@tauri-apps/api/event');
        const unlisten = await listen<{
          bid_prices: number[];
          bid_sizes: number[];
          ask_prices: number[];
          ask_sizes: number[];
        }>('orderbook-update', (event) => {
          const { bid_prices, bid_sizes, ask_prices, ask_sizes } = event.payload;
          const newBook = buildBookFromDepth(bid_prices, bid_sizes, ask_prices, ask_sizes);
          setBook(newBook);
          setIsLive(true);

          // Cache the latest book details for this symbol
          const currentSymbol = useTradeStore.getState().selectedSymbol;
          if (typeof window !== 'undefined') {
            localStorage.setItem(`ai-trader-orderbook-${currentSymbol.toUpperCase()}`, JSON.stringify(newBook));
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

  // Compute max size across all levels for depth bar scaling
  const maxAskSize = book.asks.length > 0 ? Math.max(...book.asks.map((l) => l.size), 0.01) : 0.01;
  const maxBidSize = book.bids.length > 0 ? Math.max(...book.bids.map((l) => l.size), 0.01) : 0.01;
  const globalMaxSize = Math.max(maxAskSize, maxBidSize);

  const totalAskVol = book.asks.reduce((s, l) => s + l.size, 0);
  const totalBidVol = book.bids.reduce((s, l) => s + l.size, 0);
  const totalVol = totalAskVol + totalBidVol || 1;
  const askVolPct = (totalAskVol / totalVol) * 100;
  const bidVolPct = (totalBidVol / totalVol) * 100;

  return (
    <div
      id="order-book-dom"
      className="flex h-full flex-col rounded-lg border border-border-default bg-surface font-mono text-[11px] select-none overflow-hidden"
    >

      {/* ── Column Headers ──────────────────────────────────── */}
      <div className="grid shrink-0 grid-cols-3 gap-0 border-b border-border-default bg-elevated/30 px-3 py-1.5 text-[10px] font-semibold text-text-muted uppercase tracking-wider">
        <span>Price</span>
        <span className="text-right">Size</span>
        <span className="text-right">Total</span>
      </div>

      {/* ── Awaiting Data State ───────────────────────────────── */}
      {!isLive && book.asks.length === 0 && (
        <div className="flex flex-1 items-center justify-center">
          <div className="flex flex-col items-center gap-2 text-center px-4">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-elevated">
              <span className="text-sm">📊</span>
            </div>
            <p className="text-[11px] text-text-muted leading-snug">
              Awaiting Market Depth Data...
            </p>
            <p className="text-[9px] text-text-muted/60">
              Order book populates when live depth feed connects
            </p>
          </div>
        </div>
      )}

      {/* ── Ask Levels (Red) ────────────────────────────────── */}
      {book.asks.length > 0 && (
        <div className="flex flex-col justify-end flex-1 min-h-0 overflow-hidden">
          {book.asks.map((level, i) => (
            <div
              key={`ask-${i}`}
              className="group relative grid grid-cols-3 gap-0 px-3 py-[3px] transition-colors duration-75 hover:bg-red-500/5"
            >
              {/* Depth bar background */}
              <div
                className="pointer-events-none absolute inset-y-0 right-0 bg-red-500/8 transition-[width] duration-100"
                style={{ width: `${depthPercent(level.size, globalMaxSize)}%` }}
              />
              <span className="relative z-10 tabular-nums text-[#ef4444]">
                {level.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
              <span className="relative z-10 tabular-nums text-right text-red-400/80">
                {level.size.toFixed(4)}
              </span>
              <span className="relative z-10 tabular-nums text-right text-slate-500">
                {level.total.toFixed(4)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ── Spread Bar ──────────────────────────────────────── */}
      {book.asks.length > 0 && book.bids.length > 0 && (
        <div className="flex shrink-0 items-center justify-between border-y border-border-default bg-elevated/20 px-3 py-1.5">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-bold tabular-nums text-text-primary">
              {book.midPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            <span className="text-[9px] text-slate-500 font-medium">MID</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] tabular-nums text-amber-400/90 font-semibold">
              {book.spread.toFixed(2)}
            </span>
            <span className="rounded bg-amber-500/10 px-1 py-px text-[9px] font-bold text-amber-500/70 tabular-nums">
              {book.spreadPct}%
            </span>
          </div>
        </div>
      )}

      {/* ── Bid Levels (Green) ──────────────────────────────── */}
      {book.bids.length > 0 && (
        <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
          {book.bids.map((level, i) => (
            <div
              key={`bid-${i}`}
              className="group relative grid grid-cols-3 gap-0 px-3 py-[3px] transition-colors duration-75 hover:bg-emerald-500/5"
            >
              {/* Depth bar background */}
              <div
                className="pointer-events-none absolute inset-y-0 right-0 bg-emerald-500/8 transition-[width] duration-100"
                style={{ width: `${depthPercent(level.size, globalMaxSize)}%` }}
              />
              <span className="relative z-10 tabular-nums text-[#22c55e]">
                {level.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
              <span className="relative z-10 tabular-nums text-right text-emerald-400/80">
                {level.size.toFixed(4)}
              </span>
              <span className="relative z-10 tabular-nums text-right text-slate-500">
                {level.total.toFixed(4)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ── Ask/Bid Volume Ratio Bar ────────────────────────── */}
      {book.asks.length > 0 && book.bids.length > 0 && (
        <div className="px-3 py-1.5 border-t border-border-default bg-elevated/10">
          <div className="flex justify-between text-[9px] font-bold mb-1 tracking-wider">
            <span className="text-emerald-400">{bidVolPct.toFixed(1)}% BIDS</span>
            <span className="text-red-400">{askVolPct.toFixed(1)}% ASKS</span>
          </div>
          <div className="relative h-1.5 w-full rounded-full bg-border-default/30 flex overflow-hidden">
            {/* Bid Volume (Green) */}
            <div 
              className="h-full bg-emerald-500 transition-all duration-300 ease-out" 
              style={{ width: `${bidVolPct}%` }}
            />
            {/* Ask Volume (Red) */}
            <div 
              className="h-full bg-red-500 transition-all duration-300 ease-out" 
              style={{ width: `${askVolPct}%` }}
            />
            {/* 50/50 Divider Mark */}
            <div className="absolute inset-y-0 left-1/2 w-[1px] bg-white/45 z-10" />
          </div>
        </div>
      )}
    </div>
  );
}
