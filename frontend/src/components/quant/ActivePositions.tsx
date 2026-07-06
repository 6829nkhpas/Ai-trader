'use client';

import React, { useEffect, useRef, useMemo, useCallback } from 'react';
import {
  TrendingUp,
  TrendingDown,
  X,
  AlertTriangle,
  Target,
  Shield,
  ChevronUp,
  ChevronDown,
} from 'lucide-react';
import { useQuantStore } from '../../store/useQuantStore';
import { useTradeStore } from '../../store/useTradeStore';
import type { Position } from '../../store/useQuantStore';

// ── PNL Calculator ──────────────────────────────────────────────────────

function calcPnl(pos: Position, currentPrice: number): number {
  if (pos.type === 'LONG') return (currentPrice - pos.entry_price) * pos.size;
  return (pos.entry_price - currentPrice) * pos.size;
}

function calcPnlPercent(pos: Position, currentPrice: number): number {
  if (pos.entry_price === 0) return 0;
  if (pos.type === 'LONG') return ((currentPrice - pos.entry_price) / pos.entry_price) * 100;
  return ((pos.entry_price - currentPrice) / pos.entry_price) * 100;
}

function formatPnl(value: number): string {
  const abs = Math.abs(value);
  return `${value >= 0 ? '+' : '-'}₹${abs.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatPercent(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function timeAgo(ms: number): string {
  const secs = Math.floor((Date.now() - ms) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m`;
}

// ── Single Position Row ─────────────────────────────────────────────────

interface PositionRowProps {
  position: Position;
  currentPrice: number;
  onClose: (id: string, exitPrice: number) => void;
}

function PositionRow({ position, currentPrice, onClose }: PositionRowProps) {
  const pnl = useMemo(() => calcPnl(position, currentPrice), [position, currentPrice]);
  const pnlPct = useMemo(() => calcPnlPercent(position, currentPrice), [position, currentPrice]);
  const isProfit = pnl >= 0;

  // Distance to SL/TP as percentage
  const slDistance = position.type === 'LONG'
    ? ((currentPrice - position.stop_loss) / currentPrice) * 100
    : ((position.stop_loss - currentPrice) / currentPrice) * 100;

  const tpDistance = position.type === 'LONG'
    ? ((position.take_profit - currentPrice) / currentPrice) * 100
    : ((currentPrice - position.take_profit) / currentPrice) * 100;

  const isNearSl = slDistance < 0.5 && slDistance > 0;

  return (
    <div className={`flex items-center gap-3 rounded-lg border px-3 py-2 transition-all duration-300 ${
      isNearSl
        ? 'border-rose-500/40 bg-rose-500/5 animate-pulse'
        : isProfit
          ? 'border-emerald-500/20 bg-emerald-500/5'
          : 'border-rose-500/20 bg-rose-500/5'
    }`}>
      {/* Type badge */}
      <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${
        position.type === 'LONG' ? 'bg-emerald-500/15' : 'bg-rose-500/15'
      }`}>
        {position.type === 'LONG'
          ? <TrendingUp size={14} className="text-emerald-400" />
          : <TrendingDown size={14} className="text-rose-400" />
        }
      </div>

      {/* Position info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-[11px] font-bold text-text-primary">{position.symbol}</span>
          <span className={`text-[9px] font-bold px-1 py-px rounded ${
            position.type === 'LONG' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-rose-500/15 text-rose-400'
          }`}>
            {position.type}
          </span>
          <span className="text-[9px] text-text-muted tabular-nums ml-auto">{timeAgo(position.timestamp)}</span>
        </div>

        <div className="flex items-center gap-2 mt-0.5 text-[9px] text-text-muted tabular-nums">
          <span>Entry: <span className="text-text-secondary">{position.entry_price.toFixed(2)}</span></span>
          <span className="text-rose-400/60">SL: {position.stop_loss.toFixed(2)}</span>
          <span className="text-emerald-400/60">TP: {position.take_profit.toFixed(2)}</span>
        </div>
      </div>

      {/* Live PNL */}
      <div className="flex flex-col items-end shrink-0">
        <span className={`text-sm font-black tabular-nums tracking-tight ${
          isProfit ? 'text-emerald-400' : 'text-rose-400'
        } ${Math.abs(pnl) > 0 ? 'animate-pulse' : ''}`}>
          {formatPnl(pnl)}
        </span>
        <span className={`text-[9px] font-semibold tabular-nums ${
          isProfit ? 'text-emerald-400/70' : 'text-rose-400/70'
        }`}>
          {formatPercent(pnlPct)}
        </span>
      </div>

      {/* Close button */}
      <button
        type="button"
        onClick={() => onClose(position.id, currentPrice)}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-text-muted hover:bg-rose-500/15 hover:text-rose-400 transition-colors"
        title="Close position at market"
      >
        <X size={12} />
      </button>
    </div>
  );
}

// ── Main Component ──────────────────────────────────────────────────────

export default function ActivePositions() {
  const { activePositions, closePosition, completedTrades } = useQuantStore();
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);
  const addSystemLog = useTradeStore((s) => s.addSystemLog);

  // Get latest price per symbol from live candle stream
  const latestPrices = useMemo(() => {
    const prices: Record<string, number> = {};
    for (const candle of ohlcCandles) {
      prices[candle.symbol] = candle.close;
    }
    return prices;
  }, [ohlcCandles]);

  // Ref to track already-fired auto-exits to prevent double-triggers
  const exitedRef = useRef<Set<string>>(new Set());

  // ── Auto-Exit Monitor ───────────────────────────────────────────────
  const handleClose = useCallback((id: string, exitPrice: number) => {
    closePosition(id, exitPrice);
  }, [closePosition]);

  useEffect(() => {
    for (const pos of activePositions) {
      if (exitedRef.current.has(pos.id)) continue;

      const price = latestPrices[pos.symbol];
      if (!price || price <= 0) continue;

      let triggered = false;
      let reason = '';

      if (pos.type === 'LONG') {
        if (price <= pos.stop_loss) {
          triggered = true;
          reason = `Stop Loss hit at ₹${price.toFixed(2)}`;
        } else if (price >= pos.take_profit) {
          triggered = true;
          reason = `Take Profit reached at ₹${price.toFixed(2)}`;
        }
      } else {
        // SHORT
        if (price >= pos.stop_loss) {
          triggered = true;
          reason = `Stop Loss hit at ₹${price.toFixed(2)}`;
        } else if (price <= pos.take_profit) {
          triggered = true;
          reason = `Take Profit reached at ₹${price.toFixed(2)}`;
        }
      }

      if (triggered) {
        exitedRef.current.add(pos.id);
        handleClose(pos.id, price);
        addSystemLog('INFO', `🎯 AI Strategy Target Reached. Position ${pos.symbol} ${pos.type} closed. ${reason}`);
      }
    }
  }, [activePositions, latestPrices, handleClose, addSystemLog]);

  // Clean up exited refs when positions change
  useEffect(() => {
    const activeIds = new Set(activePositions.map((p) => p.id));
    exitedRef.current.forEach((id) => {
      if (!activeIds.has(id)) exitedRef.current.delete(id);
    });
  }, [activePositions]);

  if (activePositions.length === 0 && completedTrades.length === 0) return null;

  // Calculate total PNL
  const totalPnl = activePositions.reduce((sum, pos) => {
    const price = latestPrices[pos.symbol] || pos.entry_price;
    return sum + calcPnl(pos, price);
  }, 0);

  return (
    <div className="shrink-0 border-t border-border-default bg-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5">
        <div className="flex items-center gap-2">
          <Shield size={12} className="text-blue-400" />
          <span className="text-[10px] font-bold text-text-secondary uppercase tracking-wider">
            Simulated Positions
          </span>
          {activePositions.length > 0 && (
            <span className="flex h-4 min-w-4 items-center justify-center rounded-full bg-blue-500/20 px-1 text-[9px] font-bold text-blue-400">
              {activePositions.length}
            </span>
          )}
        </div>

        {activePositions.length > 0 && (
          <div className="flex items-center gap-1.5">
            <span className="text-[9px] text-text-muted">Total PNL:</span>
            <span className={`text-xs font-black tabular-nums ${totalPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {formatPnl(totalPnl)}
            </span>
          </div>
        )}
      </div>

      {/* Active positions */}
      {activePositions.length > 0 && (
        <div className="flex flex-col gap-1 px-2 pb-2">
          {activePositions.map((pos) => (
            <PositionRow
              key={pos.id}
              position={pos}
              currentPrice={latestPrices[pos.symbol] || pos.entry_price}
              onClose={handleClose}
            />
          ))}
        </div>
      )}

      {/* Recent completed trades (compact) */}
      {completedTrades.length > 0 && (
        <div className="border-t border-border-default px-3 py-1.5">
          <div className="flex items-center gap-1.5 mb-1">
            <Target size={10} className="text-text-muted/60" />
            <span className="text-[9px] text-text-muted/60 font-semibold uppercase tracking-wider">Recent Exits</span>
          </div>
          <div className="flex flex-wrap gap-1">
            {completedTrades.slice(0, 5).map((t) => (
              <span
                key={t.id}
                className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[9px] font-semibold border ${
                  t.pnl >= 0
                    ? 'text-emerald-400 bg-emerald-500/5 border-emerald-500/20'
                    : 'text-rose-400 bg-rose-500/5 border-rose-500/20'
                }`}
              >
                {t.symbol}
                {t.pnl >= 0 ? <ChevronUp size={9} /> : <ChevronDown size={9} />}
                {formatPnl(t.pnl)}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
