'use client';

import React, { useState, useCallback, useEffect, useMemo } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import type { OhlcCandle } from '../../store/useTradeStore';
import { Briefcase, ArrowUpRight, ArrowDownRight } from 'lucide-react';

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

function formatVolume(vol: number): string {
  if (vol >= 10_000_000) return (vol / 10_000_000).toFixed(2) + ' Cr';
  if (vol >= 100_000) return (vol / 100_000).toFixed(2) + ' L';
  if (vol >= 1_000) return (vol / 1_000).toFixed(1) + ' K';
  return vol.toString();
}

export default function OrderExecutionPanel() {
  const { activeDecision, portfolioBalance, positions, executeTrade, rejectTrade } = useTradeStore();
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const liveDecisions = useTradeStore((s) => s.liveDecisions);

  const [quantity, setQuantity] = useState<number>(100);
  const [liveQuote, setLiveQuote] = useState<SymbolQuote | null>(null);

  // ── Derive symbol: selectedSymbol (watchlist) → active decision → fallback ──
  const latestDecision = activeDecision ?? liveDecisions[liveDecisions.length - 1] ?? null;
  const symbol = selectedSymbol || latestDecision?.symbol || 'RELIANCE';

  // ── Match active decision: only show trade controls when the decision
  //    matches the currently viewed symbol ──────────────────────────────
  const matchedDecision = useMemo(() => {
    if (!activeDecision) return null;
    if (activeDecision.symbol.toUpperCase() === symbol.toUpperCase()) return activeDecision;
    return null;
  }, [activeDecision, symbol]);

  // ── Fetch live quote for the selected symbol ───────────────────────
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

    const atr = computeATR(symbolCandles);

    if (!atr || atr === 0) {
      return { entryPrice: entry, targetPrice: null, stopPrice: null, atrValue: null };
    }

    const isBuy = matchedDecision?.action_type === 'BUY';
    const isSell = matchedDecision?.action_type === 'SELL';

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
  }, [liveQuote, matchedDecision, ohlcCandles, symbol]);

  // ── Always show the strip with real-time data for the selected symbol ──
  const isBuy = matchedDecision?.action_type === 'BUY';
  const isSell = matchedDecision?.action_type === 'SELL';
  const isHold = matchedDecision?.action_type === 'HOLD';
  const hasDecision = !!matchedDecision;

  const actionColor = isBuy ? 'text-bull' : isHold ? 'text-neutral' : isSell ? 'text-bear' : 'text-text-secondary';

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
        <div className="flex items-center gap-4 text-xs">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">
              {hasDecision ? 'Entry' : 'LTP'}
            </div>
            <div className="text-sm font-semibold text-text-primary tabular-nums">{entryDisplay}</div>
            {liveQuote && (
              <div className={`text-[9px] tabular-nums ${liveQuote.change >= 0 ? 'text-bull' : 'text-bear'}`}>
                {liveQuote.net_change >= 0 ? '+' : ''}{liveQuote.net_change.toFixed(2)}
              </div>
            )}
          </div>

          {/* OHLC Data — always visible for the selected symbol */}
          {liveQuote && (
            <>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-text-secondary">Open</div>
                <div className="text-sm font-semibold text-text-primary tabular-nums">{formatINR(liveQuote.open)}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-text-secondary">High</div>
                <div className="text-sm font-semibold text-bull tabular-nums">{formatINR(liveQuote.high)}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-text-secondary">Low</div>
                <div className="text-sm font-semibold text-bear tabular-nums">{formatINR(liveQuote.low)}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-text-secondary">Vol</div>
                <div className="text-sm font-semibold text-text-secondary tabular-nums">{formatVolume(liveQuote.volume)}</div>
              </div>
            </>
          )}

          {/* ATR Target/Stop — only when AI decision is active */}
          {hasDecision && (
            <>
              <div className="border-l border-border-default pl-4">
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
            </>
          )}
        </div>

        {/* ── Right: Reasoning + Portfolio State ──────────────── */}
        {hasDecision ? (
          <div className="flex min-w-48 flex-1 items-start gap-2 text-xs text-text-secondary">
            <span className="font-semibold text-text-secondary">Reasoning:</span>
            <span>{matchedDecision!.reasoning || 'Live backend decision received without a reasoning string.'}</span>
          </div>
        ) : (
          <div className="flex items-center gap-3 text-xs text-text-secondary">
            <div className="flex items-center gap-2">
              <Briefcase size={12} className="text-text-muted" />
              <span>Balance:</span>
              <span className="flex items-center font-bold text-text-primary">
                <span className="mr-0.5 text-bull font-semibold">₹</span>
                {portfolioBalance.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
            {Object.keys(positions).length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                {Object.entries(positions).map(([sym, qty]) => (
                  <span key={sym} className="rounded-full border border-border-default bg-surface px-2 py-0.5 text-[10px] text-text-secondary">
                    <span className="font-bold text-text-primary">{sym}</span>: {qty}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ── Bottom Row: Trade Controls (only when an AI decision matches selected symbol) ── */}
      {hasDecision && (
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
              onClick={() => rejectTrade(matchedDecision!)}
              className="rounded-xl border border-border-default bg-card px-4 py-2 text-xs font-bold text-text-secondary transition-colors hover:bg-elevated"
            >
              REJECT
            </button>
            <button
              onClick={() => executeTrade(matchedDecision!, quantity)}
              className={`rounded-lg px-4 py-2 text-xs font-bold uppercase transition-colors text-white ${isBuy ? 'bg-[#16A34A] hover:bg-[#047857]' : isHold ? 'bg-primary hover:bg-primary-hover' : 'bg-[#DC2626] hover:bg-red-800'}`}
            >
              {isHold ? 'ACKNOWLEDGE HOLD' : `${matchedDecision!.action_type}`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}