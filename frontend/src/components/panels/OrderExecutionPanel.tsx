'use client';

import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import type { OhlcCandle } from '../../store/useTradeStore';
import { Briefcase } from 'lucide-react';

// ── ATR Calculation (Average True Range — 14 period) ─────────────────────────
// Used to compute dynamic Target and Stop levels based on recent volatility.

function computeATR(candles: OhlcCandle[], period: number = 14): number | null {
  if (candles.length < 2) return null;

  const trueRanges: number[] = [];

  for (let i = 1; i < candles.length; i++) {
    const high = candles[i].high;
    const low = candles[i].low;
    const prevClose = candles[i - 1].close;

    // True Range = max(H-L, |H-prevC|, |L-prevC|)
    const tr = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
    trueRanges.push(tr);
  }

  if (trueRanges.length === 0) return null;

  // Use the last `period` TRs, or all available if fewer
  const usable = trueRanges.slice(-period);
  const atr = usable.reduce((sum, v) => sum + v, 0) / usable.length;
  return atr;
}

// ── Real-time quote type (same as page.tsx) ──────────────────────────────────

interface SymbolQuote {
  symbol: string;
  last_price: number;
  open: number;
  high: number;
  low: number;
  close: number;
  change: number;
  net_change: number;
  volume: number;
}

// ── Format helpers ───────────────────────────────────────────────────────────

function formatINR(value: number): string {
  return '₹' + value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export default function OrderExecutionPanel() {
  const { activeDecision, portfolioBalance, positions, executeTrade, rejectTrade } = useTradeStore();
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);
  const [quantity, setQuantity] = useState<number>(100);
  const [liveQuote, setLiveQuote] = useState<SymbolQuote | null>(null);

  // ── Derive symbol from active decision ──────────────────────────────
  const symbol = activeDecision?.symbol ?? null;

  // ── Fetch live quote for entry price (same pattern as page.tsx) ─────
  const fetchQuote = useCallback(async () => {
    if (!symbol) return;
    try {
      const res = await fetch(`/kite/quote?i=NSE:${symbol}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.quotes && data.quotes.length > 0) {
        setLiveQuote(data.quotes[0]);
      }
    } catch (err) {
      console.error('[TradeStrip] Quote fetch failed:', err);
    }
  }, [symbol]);

  useEffect(() => {
    if (symbol) {
      fetchQuote();
      const interval = setInterval(fetchQuote, 15_000); // 15s for active trade
      return () => clearInterval(interval);
    } else {
      setLiveQuote(null);
    }
  }, [symbol, fetchQuote]);

  // ── Compute ATR-based Target & Stop from live OHLC candles ─────────
  const { entryPrice, targetPrice, stopPrice, atrValue } = useMemo(() => {
    // Entry: prefer live quote, fallback to decision price
    const entry = liveQuote?.last_price ?? activeDecision?.price ?? null;
    if (!entry || !symbol) {
      return { entryPrice: activeDecision?.price ?? null, targetPrice: null, stopPrice: null, atrValue: null };
    }

    // Filter candles for this symbol
    const symbolCandles = ohlcCandles
      .filter((c) => c.symbol.toUpperCase() === symbol.toUpperCase())
      .sort((a, b) => a.start_timestamp_ms - b.start_timestamp_ms);

    const atr = computeATR(symbolCandles);

    if (!atr || atr === 0) {
      return { entryPrice: entry, targetPrice: null, stopPrice: null, atrValue: null };
    }

    const isBuy = activeDecision?.action_type === 'BUY';
    const isSell = activeDecision?.action_type === 'SELL';

    let target: number | null = null;
    let stop: number | null = null;

    if (isBuy) {
      // BUY: Target 2× ATR above entry, Stop 1× ATR below (2:1 R:R)
      target = entry + atr * 2;
      stop = entry - atr;
    } else if (isSell) {
      // SELL: Target 2× ATR below entry, Stop 1× ATR above (2:1 R:R)
      target = entry - atr * 2;
      stop = entry + atr;
    }

    return { entryPrice: entry, targetPrice: target, stopPrice: stop, atrValue: atr };
  }, [liveQuote, activeDecision, ohlcCandles, symbol]);

  // ── No active decision — show portfolio state ──────────────────────
  if (!activeDecision) {
    return (
      <div className="flex flex-col gap-2 px-3 py-2">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
            <Briefcase size={14} /> Portfolio State
          </h2>
          <span className="text-xs text-text-muted">No active signal</span>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-text-secondary">
            <span>Available Balance:</span>
            <span className="flex items-center text-lg font-bold text-text-primary">
              <span className="mr-1 text-bull font-semibold">₹</span>
              {portfolioBalance.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>

          {Object.keys(positions).length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-xs font-semibold uppercase text-text-secondary">Positions</span>
              {Object.entries(positions).map(([sym, qty]) => (
                <div key={sym} className="rounded-full border border-border-default bg-surface px-2 py-1 text-xs text-text-secondary">
                  <span className="font-bold text-text-primary">{sym}</span>: {qty}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Active decision — show Trade Strip ─────────────────────────────
  const isBuy = activeDecision.action_type === 'BUY';
  const isHold = activeDecision.action_type === 'HOLD';
  const actionColor = isBuy ? 'text-bull' : isHold ? 'text-neutral' : 'text-bear';

  const entryDisplay = entryPrice ? formatINR(entryPrice) : '--';
  const targetDisplay = targetPrice ? formatINR(targetPrice) : '--';
  const stopDisplay = stopPrice ? formatINR(stopPrice) : '--';

  // Risk:Reward ratio
  const rrRatio = (entryPrice && targetPrice && stopPrice)
    ? Math.abs(targetPrice - entryPrice) / Math.max(Math.abs(stopPrice - entryPrice), 0.01)
    : null;

  return (
    <div className="flex flex-col gap-2 px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-45">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-text-secondary">Trade Strip</h2>
          <div className="mt-1 text-sm font-semibold text-text-primary">
            {activeDecision.symbol}{' '}
            <span className={`font-bold ${actionColor}`}>{activeDecision.action_type}</span>
          </div>
          <div className="flex items-center gap-2 text-xs text-text-secondary">
            <span>Conviction {activeDecision.final_conviction_score}%</span>
            {atrValue && (
              <span className="text-[10px] text-text-muted tabular-nums">
                ATR: {atrValue.toFixed(2)}
              </span>
            )}
            {rrRatio && (
              <span className="rounded bg-cyan-500/10 px-1 py-px text-[9px] font-bold text-cyan-400 tabular-nums">
                {rrRatio.toFixed(1)}:1 R:R
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-4 text-xs">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Entry</div>
            <div className="text-sm font-semibold text-text-primary tabular-nums">{entryDisplay}</div>
            {liveQuote && (
              <div className={`text-[9px] tabular-nums ${liveQuote.change >= 0 ? 'text-bull' : 'text-bear'}`}>
                {liveQuote.change >= 0 ? '+' : ''}{liveQuote.change.toFixed(2)}%
              </div>
            )}
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Target</div>
            <div className={`text-sm font-semibold tabular-nums ${targetPrice ? 'text-bull' : 'text-text-muted'}`}>{targetDisplay}</div>
            {targetPrice && entryPrice && (
              <div className="text-[9px] text-bull tabular-nums">
                +{(((targetPrice - entryPrice) / entryPrice) * 100).toFixed(1)}%
              </div>
            )}
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Stop</div>
            <div className={`text-sm font-semibold tabular-nums ${stopPrice ? 'text-bear' : 'text-text-muted'}`}>{stopDisplay}</div>
            {stopPrice && entryPrice && (
              <div className="text-[9px] text-bear tabular-nums">
                {(((stopPrice - entryPrice) / entryPrice) * 100).toFixed(1)}%
              </div>
            )}
          </div>
        </div>

        <div className="flex min-w-48 flex-1 items-start gap-2 text-xs text-text-secondary">
          <span className="font-semibold text-text-secondary">Reasoning:</span>
          <span>{activeDecision.reasoning || 'Live backend decision received without a reasoning string.'}</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-1 items-center gap-3">
          <div className="min-w-35 flex-1">
            <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-text-secondary">Quantity</label>
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(Number(e.target.value))}
              className="w-full rounded-lg border border-border-default bg-surface px-2 py-1.5 font-mono text-sm text-text-primary transition-all focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              min="1"
              disabled={isHold}
            />
          </div>
          <div className="min-w-40 flex-1">
            <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-text-secondary">
              Est. Value (Price: {entryPrice ? formatINR(entryPrice) : '---'})
            </label>
            <div className="flex h-8 w-full items-center rounded-lg border border-border-default bg-surface px-2 font-mono text-sm text-text-secondary">
              {entryPrice
                ? formatINR(entryPrice * quantity)
                : 'N/A'}
            </div>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-3">
          <button
            onClick={() => rejectTrade(activeDecision)}
            className="rounded-xl border border-border-default bg-card px-4 py-2 text-xs font-bold text-text-secondary transition-colors hover:bg-elevated"
          >
            REJECT
          </button>
          <button
            onClick={() => executeTrade(activeDecision, quantity)}
            className={`rounded-lg px-4 py-2 text-xs font-bold uppercase transition-colors text-white ${isBuy ? 'bg-[#16A34A] hover:bg-[#047857]' : isHold ? 'bg-primary hover:bg-primary-hover' : 'bg-[#DC2626] hover:bg-red-800'}`}
          >
            {isHold ? 'ACKNOWLEDGE HOLD' : `${activeDecision.action_type}`}
          </button>
        </div>
      </div>
    </div>
  );
}