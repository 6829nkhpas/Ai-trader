'use client';

import React, { useEffect, useState, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useTradeStore } from '../store/useTradeStore';
import { crossfade } from '../lib/motionVariants';

import {
  type OrderBookLevel,
  type OrderBookState,
  createEmptyBook,
  depthPercent,
  formatSize,
  buildBookFromDepth,
} from './orderbook/orderBookHelpers';

// ── Component ──────────────────────────────────────────────────────────
export default function OrderBook() {
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);

  const [book, setBook] = useState<OrderBookState>(() => createEmptyBook());
  const [isLive, setIsLive] = useState(false);
  const updateCountRef = useRef(0);

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

  // Depth-bar scaling and the bid/ask ratio use REAL levels only so synthetic
  // padding never distorts the liquidity picture.
  const realAsks = book.asks.filter((l) => !l.synthetic);
  const realBids = book.bids.filter((l) => !l.synthetic);
  const maxAskSize = realAsks.length > 0 ? Math.max(...realAsks.map((l) => l.size), 0.01) : 0.01;
  const maxBidSize = realBids.length > 0 ? Math.max(...realBids.map((l) => l.size), 0.01) : 0.01;
  const globalMaxSize = Math.max(maxAskSize, maxBidSize);

  const totalAskVol = realAsks.reduce((s, l) => s + l.size, 0);
  const totalBidVol = realBids.reduce((s, l) => s + l.size, 0);
  const totalVol = totalAskVol + totalBidVol || 1;
  const askVolPct = (totalAskVol / totalVol) * 100;
  const bidVolPct = (totalBidVol / totalVol) * 100;

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
                <span className="text-sm">📊</span>
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
        <div className="flex flex-col justify-end flex-1 min-h-0 overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none] font-sans">
          {book.asks.map((level, i) => (
            <div
              key={`ask-${i}`}
              className={`group relative grid grid-cols-3 gap-0 px-3.5 py-0.75 hover:bg-red-500/10 ${level.synthetic ? 'opacity-75' : ''}`}
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
        <div className="flex flex-col flex-1 min-h-0 overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none] font-sans">
          {book.bids.map((level, i) => (
            <div
              key={`bid-${i}`}
              className={`group relative grid grid-cols-3 gap-0 px-3.5 py-0.75 hover:bg-emerald-500/10 ${level.synthetic ? 'opacity-75' : ''}`}
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
        <div className="px-3.5 py-2 border-t border-border-default bg-elevated/20 font-sans">
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
