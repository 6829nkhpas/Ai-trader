'use client';

// PortfolioDashboard.tsx — Premium Virtual Paper Trading Account & Journal Dashboard
// 
// Displays virtual account equity, active simulated positions, and the completed trade journal 
// with realized PnL in dynamic, premium glassmorphism styling.

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { Shield, TrendingUp, TrendingDown, Clock, BookOpen, AlertCircle } from 'lucide-react';

export default function PortfolioDashboard() {
  const paperPortfolio = useTradeStore((s) => s.paperPortfolio);
  const fetchPaperPortfolio = useTradeStore((s) => s.fetchPaperPortfolio);

  React.useEffect(() => {
    // Initial sync
    fetchPaperPortfolio();
  }, [fetchPaperPortfolio]);

  if (!paperPortfolio) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-8 border border-border-default/40 bg-surface/50 rounded-xl">
        <Shield size={24} className="text-text-muted opacity-30 animate-pulse" />
        <span className="text-xs font-semibold text-text-muted">Loading paper engine state...</span>
      </div>
    );
  }

  const { balance, active_positions, trade_history } = paperPortfolio;

  // Realized PnL Calculator for individual completed trades
  const calculateRealizedPnL = (pos: any) => {
    const isWin = pos.status === 'CLOSED_WIN';
    const exitPrice = isWin ? pos.take_profit : pos.stop_loss;
    const diff = exitPrice - pos.entry_price;
    const pnl = pos.side === 'BUY' ? diff * pos.quantity : -diff * pos.quantity;
    return pnl;
  };

  // Cumulative PnL from Trade History
  const totalPnL = trade_history.reduce((sum, pos) => sum + calculateRealizedPnL(pos), 0);

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-border-default bg-surface/80 backdrop-blur-md p-4 shadow-lg">
      
      {/* ── Top Header and Balance Card ── */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-border-default/50 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
            </span>
            <h2 className="text-xs font-black uppercase tracking-wider text-text-primary">Simulated Paper Portfolio</h2>
          </div>
          <p className="text-[10px] text-text-muted mt-0.5">Real-time local match loop verification</p>
        </div>

        {/* Balance Display */}
        <div className="flex items-center gap-4">
          <div className="bg-black/30 border border-white/5 rounded-xl px-4 py-2 flex flex-col items-end">
            <span className="text-[9px] uppercase font-bold text-text-muted">Account Equity</span>
            <span className="text-xl font-black text-white tracking-tight tabular-nums">
              ₹{balance.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>

          <div className={`bg-black/30 border border-white/5 rounded-xl px-4 py-2 flex flex-col items-end`}>
            <span className="text-[9px] uppercase font-bold text-text-muted">Realized PnL</span>
            <span className={`text-base font-extrabold tracking-tight tabular-nums flex items-center gap-1 ${totalPnL >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
              {totalPnL >= 0 ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
              {totalPnL >= 0 ? '+' : ''}₹{totalPnL.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>
        </div>
      </div>

      {/* ── Grid Layout: Active Positions & Trade Journal ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        
        {/* Left Side: Active Positions */}
        <div className="flex flex-col gap-2 border border-border-default/40 bg-black/10 rounded-xl p-3">
          <div className="flex items-center justify-between pb-2 border-b border-border-default/30">
            <span className="text-[10px] font-black uppercase text-text-secondary tracking-wider flex items-center gap-1.5">
              <Shield size={12} className="text-emerald-400" /> Active Positions ({active_positions.length})
            </span>
          </div>

          {active_positions.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
              <AlertCircle size={20} className="text-text-muted opacity-40" />
              <p className="text-xs font-medium text-text-secondary">No active positions</p>
              <p className="text-[10px] text-text-muted max-w-[220px]">Deploy strategies from the AI Deep Quant panel or trigger Sentinel signals.</p>
            </div>
          ) : (
            <div className="overflow-x-auto max-h-[220px] scrollbar-thin">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-border-default/20 text-[9px] font-black uppercase text-text-muted">
                    <th className="py-2">Symbol</th>
                    <th className="py-2">Side</th>
                    <th className="py-2 text-right">Qty</th>
                    <th className="py-2 text-right">Entry Price</th>
                    <th className="py-2 text-right">SL / TP</th>
                    <th className="py-2 text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-default/10 text-xs">
                  {active_positions.map((pos) => (
                    <tr key={pos.id} className="hover:bg-white/5 transition-colors">
                      <td className="py-2 font-bold text-white uppercase">{pos.symbol}</td>
                      <td className="py-2">
                        <span className={`rounded-md px-1.5 py-0.5 text-[9px] font-black tracking-wide uppercase ${
                          pos.side === 'BUY' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'
                        }`}>
                          {pos.side}
                        </span>
                      </td>
                      <td className="py-2 text-right font-mono tabular-nums">{pos.quantity}</td>
                      <td className="py-2 text-right font-mono tabular-nums">₹{pos.entry_price.toFixed(2)}</td>
                      <td className="py-2 text-right font-mono tabular-nums text-[10px]">
                        <div className="text-rose-400">SL: ₹{pos.stop_loss.toFixed(2)}</div>
                        <div className="text-emerald-400">TP: ₹{pos.take_profit.toFixed(2)}</div>
                      </td>
                      <td className="py-2 text-center">
                        <span className="rounded bg-emerald-500/10 border border-emerald-500/20 px-1 py-0.5 text-[9px] font-bold text-emerald-400 uppercase tracking-widest animate-pulse">
                          {pos.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Right Side: Trade Journal */}
        <div className="flex flex-col gap-2 border border-border-default/40 bg-black/10 rounded-xl p-3">
          <div className="flex items-center justify-between pb-2 border-b border-border-default/30">
            <span className="text-[10px] font-black uppercase text-text-secondary tracking-wider flex items-center gap-1.5">
              <BookOpen size={12} className="text-violet-400" /> Realized Trade Journal ({trade_history.length})
            </span>
          </div>

          {trade_history.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
              <Clock size={20} className="text-text-muted opacity-40 animate-pulse" />
              <p className="text-xs font-medium text-text-secondary">Journal is currently empty</p>
              <p className="text-[10px] text-text-muted max-w-[220px]">Closed positions will be recorded here when stop losses or targets are hit.</p>
            </div>
          ) : (
            <div className="overflow-x-auto max-h-[220px] scrollbar-thin">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-border-default/20 text-[9px] font-black uppercase text-text-muted">
                    <th className="py-2">Symbol</th>
                    <th className="py-2">Side</th>
                    <th className="py-2 text-right">Entry</th>
                    <th className="py-2 text-right">SL / TP Boundary</th>
                    <th className="py-2 text-right">Realized PnL</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-default/10 text-xs">
                  {trade_history.map((pos) => {
                    const pnl = calculateRealizedPnL(pos);
                    const isWin = pos.status === 'CLOSED_WIN';
                    return (
                      <tr key={pos.id} className="hover:bg-white/5 transition-colors">
                        <td className="py-2 font-bold text-white uppercase">{pos.symbol}</td>
                        <td className="py-2">
                          <span className={`rounded-md px-1.5 py-0.5 text-[9px] font-black tracking-wide uppercase ${
                            pos.side === 'BUY' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'
                          }`}>
                            {pos.side}
                          </span>
                        </td>
                        <td className="py-2 text-right font-mono tabular-nums">₹{pos.entry_price.toFixed(2)}</td>
                        <td className="py-2 text-right font-mono tabular-nums text-text-secondary">
                          ₹{(isWin ? pos.take_profit : pos.stop_loss).toFixed(2)}
                        </td>
                        <td className={`py-2 text-right font-bold font-mono tabular-nums ${pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {pnl >= 0 ? '+' : ''}₹{pnl.toFixed(2)}
                          <div className="text-[8px] text-text-muted font-medium font-sans uppercase">
                            {isWin ? 'TAKE PROFIT HIT' : 'STOP LOSS HIT'}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

      </div>

    </div>
  );
}
