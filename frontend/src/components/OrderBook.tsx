'use client';

import React, { useEffect, useRef, useState, useCallback } from 'react';

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

// ── Seed Data Generator ────────────────────────────────────────────────
const BASE_PRICE = 67_432.50;
const TICK_SIZE = 0.50;
const LEVEL_COUNT = 10;

function generateInitialBook(): OrderBookState {
  const asks: OrderBookLevel[] = [];
  const bids: OrderBookLevel[] = [];

  // Build ask levels (ascending from mid price)
  let askRunningTotal = 0;
  for (let i = 0; i < LEVEL_COUNT; i++) {
    const price = BASE_PRICE + (i + 1) * TICK_SIZE;
    const size = parseFloat((Math.random() * 8 + 0.1).toFixed(4));
    askRunningTotal += size;
    asks.push({ price, size, total: parseFloat(askRunningTotal.toFixed(4)) });
  }

  // Build bid levels (descending from mid price)
  let bidRunningTotal = 0;
  for (let i = 0; i < LEVEL_COUNT; i++) {
    const price = BASE_PRICE - (i + 1) * TICK_SIZE;
    const size = parseFloat((Math.random() * 8 + 0.1).toFixed(4));
    bidRunningTotal += size;
    bids.push({ price, size, total: parseFloat(bidRunningTotal.toFixed(4)) });
  }

  // Asks displayed in descending order (highest at top, lowest near spread)
  asks.reverse();

  const bestAsk = asks[asks.length - 1].price;
  const bestBid = bids[0].price;
  const spread = parseFloat((bestAsk - bestBid).toFixed(2));
  const spreadPct = ((spread / bestAsk) * 100).toFixed(3);
  const midPrice = parseFloat(((bestAsk + bestBid) / 2).toFixed(2));

  return { asks, bids, spread, spreadPct, midPrice };
}

// ── High-Frequency Mock Engine ─────────────────────────────────────────
function perturbBook(prev: OrderBookState): OrderBookState {
  // Slightly shift base price with a micro random walk
  const priceShift = (Math.random() - 0.5) * 0.20;

  const asks: OrderBookLevel[] = [];
  const bids: OrderBookLevel[] = [];

  // Perturb ask sizes (asks are stored descending: highest first)
  let askRunningTotal = 0;
  // Process from lowest ask (end of array) to highest (start)
  for (let i = prev.asks.length - 1; i >= 0; i--) {
    const orig = prev.asks[i];
    const price = parseFloat((orig.price + priceShift).toFixed(2));
    // Jitter size by ±15% with floor at 0.01
    const jitter = 1 + (Math.random() - 0.5) * 0.30;
    const size = parseFloat(Math.max(0.01, orig.size * jitter).toFixed(4));
    askRunningTotal += size;
    asks.unshift({ price, size, total: parseFloat(askRunningTotal.toFixed(4)) });
  }

  // Perturb bid sizes (bids are stored descending: highest first)
  let bidRunningTotal = 0;
  for (let i = 0; i < prev.bids.length; i++) {
    const orig = prev.bids[i];
    const price = parseFloat((orig.price + priceShift).toFixed(2));
    const jitter = 1 + (Math.random() - 0.5) * 0.30;
    const size = parseFloat(Math.max(0.01, orig.size * jitter).toFixed(4));
    bidRunningTotal += size;
    bids.push({ price, size, total: parseFloat(bidRunningTotal.toFixed(4)) });
  }

  const bestAsk = asks[asks.length - 1].price;
  const bestBid = bids[0].price;
  const spread = parseFloat((bestAsk - bestBid).toFixed(2));
  const spreadPct = ((spread / bestAsk) * 100).toFixed(3);
  const midPrice = parseFloat(((bestAsk + bestBid) / 2).toFixed(2));

  return { asks, bids, spread, spreadPct, midPrice };
}

// ── Depth Bar (visual liquidity gauge) ─────────────────────────────────
function depthPercent(size: number, maxSize: number): number {
  return Math.min((size / maxSize) * 100, 100);
}

// ── Component ──────────────────────────────────────────────────────────
export default function OrderBook() {
  const [book, setBook] = useState<OrderBookState>(() => generateInitialBook());
  const bookRef = useRef(book);

  // Keep ref in sync for the interval callback (avoids stale closures)
  useEffect(() => {
    bookRef.current = book;
  }, [book]);

  // ── 100ms High-Frequency Simulation Loop ───────────────────────────
  useEffect(() => {
    const intervalId = setInterval(() => {
      setBook((prev) => perturbBook(prev));
    }, 100);

    return () => {
      clearInterval(intervalId);
    };
  }, []);

  // Compute max size across all levels for depth bar scaling
  const maxAskSize = Math.max(...book.asks.map((l) => l.size), 0.01);
  const maxBidSize = Math.max(...book.bids.map((l) => l.size), 0.01);
  const globalMaxSize = Math.max(maxAskSize, maxBidSize);

  return (
    <div
      id="order-book-dom"
      className="flex h-full flex-col rounded-lg border border-border-default bg-surface font-mono text-[11px] select-none overflow-hidden"
    >
      {/* ── Header ──────────────────────────────────────────── */}
      <div className="flex shrink-0 items-center justify-between border-b border-border-default px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-text-primary tracking-wide">Order Book</span>
          <span className="rounded bg-purple-500/15 px-1.5 py-px text-[9px] font-bold text-purple-400 uppercase tracking-widest">
            L2 DOM
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-50" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
          </span>
          <span className="text-[9px] font-medium text-slate-500 uppercase tracking-widest">100ms</span>
        </div>
      </div>

      {/* ── Column Headers ──────────────────────────────────── */}
      <div className="grid shrink-0 grid-cols-3 gap-0 border-b border-border-default bg-slate-50 px-3 py-1.5 text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
        <span>Price</span>
        <span className="text-right">Size</span>
        <span className="text-right">Total</span>
      </div>

      {/* ── Ask Levels (Red) ────────────────────────────────── */}
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

      {/* ── Spread Bar ──────────────────────────────────────── */}
      <div className="flex shrink-0 items-center justify-between border-y border-border-default bg-slate-50 px-3 py-1.5">
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

      {/* ── Bid Levels (Green) ──────────────────────────────── */}
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

      {/* ── Footer Stats ────────────────────────────────────── */}
      <div className="flex shrink-0 items-center justify-between border-t border-border-default bg-slate-50 px-3 py-1.5 text-[9px] text-slate-500">
        <span>
          Ask Vol:{' '}
          <span className="text-red-400/70 tabular-nums font-medium">
            {book.asks.reduce((s, l) => s + l.size, 0).toFixed(2)}
          </span>
        </span>
        <span>
          Bid Vol:{' '}
          <span className="text-emerald-400/70 tabular-nums font-medium">
            {book.bids.reduce((s, l) => s + l.size, 0).toFixed(2)}
          </span>
        </span>
      </div>
    </div>
  );
}
