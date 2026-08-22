'use client';

import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import type { OhlcCandle } from '../../store/useTradeStore';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';
import ReasoningBlock from './order-execution/ReasoningBlock';
import MetricsHUD from './order-execution/MetricsHUD';
import { kiteFetch } from '../../lib/kiteFetch';

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



export default function OrderExecutionPanel() {
  const { activeDecision, portfolioBalance, positions } = useTradeStore();
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const liveDecisions = useTradeStore((s) => s.liveDecisions);


  const [liveQuote, setLiveQuote] = useState<SymbolQuote | null>(null);

  // ── Derive symbol: selectedSymbol (watchlist) → active decision → fallback ──
  const latestDecision = activeDecision ?? liveDecisions[liveDecisions.length - 1] ?? null;
  const symbol = selectedSymbol || latestDecision?.symbol || 'RELIANCE';

  // ── Match active decision: only show trade controls when the decision
  //    matches the currently viewed symbol ──────────────────────────────
  const matchedDecision = useMemo(() => {
    // 1. If activeDecision matches the viewed symbol, use it first
    if (activeDecision && activeDecision.symbol.toUpperCase() === symbol.toUpperCase()) {
      return activeDecision;
    }
    // 2. Otherwise, find the latest decision in the liveDecisions array matching the symbol
    const reversedDecisions = [...liveDecisions].reverse();
    const matched = reversedDecisions.find((d) => d.symbol.toUpperCase() === symbol.toUpperCase());
    if (matched) return matched;

    // 3. Fallback: No mock synthetic decisions! Returns null.
    return null;
  }, [activeDecision, liveDecisions, symbol]);

  const fetchQuote = useCallback(async () => {
    if (!symbol) return;
    try {
      const sym = symbol.toUpperCase();
      const isFno = sym.endsWith('FUT') || ((sym.endsWith('CE') || sym.endsWith('PE')) && /\d/.test(sym));
      const exchange = isFno ? 'NFO' : 'NSE';
      const res = await kiteFetch(`/quote?i=${exchange}:${symbol}`);
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
    // Reset quote on symbol change for instant visual feedback
    setLiveQuote(null);
    if (symbol) {
      fetchQuote();
      const interval = setInterval(fetchQuote, 10_000); // 10s polling for selected symbol
      return () => clearInterval(interval);
    }
  }, [symbol, fetchQuote]);



  // ── Compute ATR-based Target & Stop from live OHLC candles ─────────
  const { entryPrice, targetPrice, stopPrice, atrValue } = useMemo(() => {
    // Entry: prefer live quote, fallback to decision price
    const entry = liveQuote?.last_price ?? matchedDecision?.price ?? null;
    if (!entry || !symbol) {
      return { entryPrice: liveQuote?.last_price ?? matchedDecision?.price ?? null, targetPrice: null, stopPrice: null, atrValue: null };
    }

    // Filter candles for this symbol
    const symbolCandles = ohlcCandles
      .filter((c) => c.symbol.toUpperCase() === symbol.toUpperCase())
      .sort((a, b) => a.start_timestamp_ms - b.start_timestamp_ms);

    // Dynamic synthetic ATR fallback = 1.8% of entry price
    const syntheticAtr = entry * 0.018;
    const atr = computeATR(symbolCandles) || syntheticAtr;

    const isBuy = matchedDecision?.action_type === 'BUY';
    const isSell = matchedDecision?.action_type === 'SELL';
    const isHold = matchedDecision?.action_type === 'HOLD';

    let target: number | null = null;
    let stop: number | null = null;

    if (isBuy || isHold) {
      // BUY/HOLD: Target 2× ATR above entry, Stop 1× ATR below (2:1 R:R)
      target = entry + atr * 2;
      stop = entry - atr;
    } else if (isSell) {
      // SELL: Target 2× ATR below entry, Stop 1× ATR above (2:1 R:R)
      target = entry - atr * 2;
      stop = entry + atr;
    }

    return { entryPrice: entry, targetPrice: target, stopPrice: stop, atrValue: atr };
  }, [liveQuote, matchedDecision, ohlcCandles, symbol]);

  // ── Always show the strip with real-time data for the selected symbol ──
  const isBuy = matchedDecision?.action_type === 'BUY';
  const isSell = matchedDecision?.action_type === 'SELL';
  const isHold = matchedDecision?.action_type === 'HOLD';
  const hasDecision = !!matchedDecision;

  // Risk:Reward ratio
  const rrRatio = (entryPrice && targetPrice && stopPrice)
    ? Math.abs(targetPrice - entryPrice) / Math.max(Math.abs(stopPrice - entryPrice), 0.01)
    : null;

  return (
    <div className="flex flex-col gap-2 px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* ── Left: Symbol + Live Quote ───────────────────────── */}
        <div className="min-w-45">
          <div className="flex items-center gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
              {hasDecision ? 'Trade Strip' : 'Live Strip'}
            </h2>
            {hasDecision && (
              <span className={`rounded px-1.5 py-px text-[9px] font-bold uppercase ${
                isBuy ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                  : isSell ? 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                  : 'bg-amber-500/10 text-amber-400 border border-amber-500/30'
              }`}>
                {matchedDecision!.action_type}
              </span>
            )}
          </div>
          <div className="mt-1 flex items-center gap-2">
            <span className="text-sm font-semibold text-text-primary">{symbol}</span>
            {liveQuote && (
              <div className={`flex items-center gap-0.5 text-[10px] font-medium tabular-nums ${liveQuote.change >= 0 ? 'text-bull' : 'text-bear'}`}>
                {liveQuote.change >= 0 ? <ArrowUpRight size={10} /> : <ArrowDownRight size={10} />}
                {liveQuote.change >= 0 ? '+' : ''}{liveQuote.change.toFixed(2)}%
              </div>
            )}
          </div>
          {hasDecision && (
            <div className="flex items-center gap-2 text-xs text-text-secondary">
              <span>Conviction {matchedDecision!.final_conviction_score}%</span>
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
          )}
        </div>

        {/* ── Center: Price Levels (Entry / Target / Stop / OHLC) ── */}
        <MetricsHUD
          hasDecision={hasDecision}
          liveQuote={liveQuote}
          entryPrice={entryPrice}
          targetPrice={targetPrice}
          stopPrice={stopPrice}
        />

        {/* ── Right: Reasoning + Portfolio State ──────────────── */}
        <ReasoningBlock
          hasDecision={hasDecision}
          matchedDecision={matchedDecision}
          symbol={symbol}
          liveQuote={liveQuote}
          portfolioBalance={portfolioBalance}
          positions={positions}
        />
      </div>


    </div>
  );
}