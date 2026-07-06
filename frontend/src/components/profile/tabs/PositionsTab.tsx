import React, { useState } from 'react';
import { Layers, RefreshCw, AlertCircle, ChevronDown, ChevronUp } from 'lucide-react';

interface PositionsTabProps {
  positionsData: any;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export default function PositionsTab({ positionsData, loading, error, refetch }: PositionsTabProps) {
  const [subTab, setSubTab] = useState<'net' | 'day'>('net');
  const [expandedSymbol, setExpandedSymbol] = useState<string | null>(null);

  const formatCurrency = (val: number | undefined) => {
    if (val === undefined) return '₹0.00';
    return `₹${val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  const getPnlClass = (val: number) => {
    if (val > 0) return 'text-bull font-bold';
    if (val < 0) return 'text-[#ef4444] font-bold';
    return 'text-text-secondary';
  };

  const toggleExpand = (symbol: string) => {
    setExpandedSymbol(expandedSymbol === symbol ? null : symbol);
  };

  return (
    <div className="space-y-5 flex flex-col h-full">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-extrabold text-text-primary tracking-tight">Active Positions</h2>
          <p className="text-xs text-text-secondary mt-1">Real-time positions linked directly to Kite broker</p>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-400">
          <AlertCircle size={14} />
          <span>{error}</span>
        </div>
      )}

      {loading && !positionsData && (
        <div className="flex h-24 items-center justify-center text-xs text-text-muted">
          <RefreshCw size={14} className="animate-spin mr-2 text-emerald-400" />
          Loading positions...
        </div>
      )}

      {positionsData && (
        <div className="flex flex-col h-full min-h-0 space-y-3">
          {/* Sub tabs */}
          <div className="flex items-center gap-0 border-b border-border-default/40 pb-2">
            <button
              type="button"
              onClick={() => setSubTab('net')}
              className={`rounded-none px-3 py-1 text-[10px] font-bold uppercase tracking-wider transition-all border ${
                subTab === 'net'
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'text-text-muted hover:text-text-secondary border-transparent'
              }`}
            >
              Net Positions ({positionsData.net?.length || 0})
            </button>
            <button
              type="button"
              onClick={() => setSubTab('day')}
              className={`rounded-none px-3 py-1 text-[10px] font-bold uppercase tracking-wider transition-all border-y border-r border-l-0 ${
                subTab === 'day'
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'text-text-muted hover:text-text-secondary border-transparent'
              }`}
            >
              Day Positions ({positionsData.day?.length || 0})
            </button>
          </div>

          {/* Positions table */}
          <div className="flex-1 min-h-0 overflow-auto border border-border-default/40 rounded-none bg-surface/30">
            <table className="w-full text-left text-xs border-collapse">
              <thead className="bg-elevated/80 border-b border-border-default/40 sticky top-0 z-10">
                <tr>
                  <th className="w-8"></th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary">Symbol</th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary">Product</th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary text-right">Qty</th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary text-right">Avg</th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary text-right">LTP</th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary text-right">PnL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-default/10">
                {(subTab === 'net' ? positionsData.net : positionsData.day)?.map((pos: any, idx: number) => {
                  const isShort = pos.quantity < 0;
                  const qtyText = isShort ? `${pos.quantity}` : pos.quantity > 0 ? `+${pos.quantity}` : '0';
                  const qtyClass = isShort ? 'text-rose-400 font-semibold' : pos.quantity > 0 ? 'text-emerald-400 font-semibold' : 'text-text-muted';
                  const isExpanded = expandedSymbol === pos.tradingsymbol;
                  
                  return (
                    <React.Fragment key={`${pos.tradingsymbol}-${idx}`}>
                      <tr 
                        onClick={() => toggleExpand(pos.tradingsymbol)}
                        className={`hover:bg-elevated/10 cursor-pointer transition-colors ${isExpanded ? 'bg-elevated/5' : ''}`}
                      >
                        <td className="pl-3 py-2.5 text-center text-text-muted hover:text-text-primary">
                          {isExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                        </td>
                        <td className="px-4 py-2.5 font-bold text-text-primary flex items-center gap-1.5">
                          <span>{pos.tradingsymbol}</span>
                          <span className="text-[8px] bg-elevated border border-border-default text-text-secondary px-1 py-0.5 rounded-none font-mono">
                            {pos.exchange}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-text-secondary font-mono">{pos.product}</td>
                        <td className={`px-4 py-2.5 text-right font-mono ${qtyClass}`}>{qtyText}</td>
                        <td className="px-4 py-2.5 text-right font-mono text-text-secondary">
                          {(pos.average_price ?? 0).toFixed(2)}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono text-text-primary">
                          {(pos.last_price ?? 0).toFixed(2)}
                        </td>
                        <td className={`px-4 py-2.5 text-right font-mono font-bold ${getPnlClass(pos.pnl)}`}>
                          {pos.pnl >= 0 ? '+' : ''}{(pos.pnl ?? 0).toFixed(2)}
                        </td>
                      </tr>

                      {/* Deep Analytics Card Expanded Grid */}
                      {isExpanded && (
                        <tr className="bg-surface/40 border-b border-border-default/20">
                          <td colSpan={7} className="p-4">
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-[11px] animate-in fade-in slide-in-from-top-1 duration-150">
                              
                              {/* Card Column 1: Net Position Geometry */}
                              <div className="border border-border-default/30 rounded-none bg-muted/50 p-3.5 space-y-2">
                                <span className="text-[9px] font-black uppercase tracking-wider text-text-secondary block border-b border-border-default/30 pb-1.5 mb-1.5">
                                  NET POSITION GEOMETRY
                                </span>
                                <div className="flex justify-between">
                                  <span className="text-text-muted">Net Position Value</span>
                                  <span className={`font-mono font-bold ${(pos.value ?? 0) >= 0 ? 'text-bull' : 'text-[#ef4444]'}`}>
                                    {formatCurrency(pos.value)}
                                  </span>
                                </div>
                                <div className="flex justify-between">
                                  <span className="text-text-muted">Overnight Quantity</span>
                                  <span className="font-mono text-text-primary font-semibold">{pos.overnight_quantity ?? 0}</span>
                                </div>
                                <div className="flex justify-between">
                                  <span className="text-text-muted">Lot Multiplier</span>
                                  <span className="font-mono text-text-primary font-semibold">x{pos.multiplier ?? 1}</span>
                                </div>
                                <div className="flex justify-between border-t border-border-default/20 pt-2 mt-1">
                                  <span className="text-text-secondary font-bold">Mark to Market (M2M)</span>
                                  <span className={`font-mono font-extrabold ${getPnlClass(pos.m2m ?? 0)}`}>
                                    {formatCurrency(pos.m2m)}
                                  </span>
                                </div>
                              </div>

                              {/* Card Column 2: Buy & Sell Accumulation */}
                              <div className="border border-border-default/30 rounded-none bg-muted/50 p-3.5 space-y-2">
                                <span className="text-[9px] font-black uppercase tracking-wider text-emerald-400 block border-b border-border-default/30 pb-1.5 mb-1.5">
                                  BUY & SELL ACCUMULATION
                                </span>
                                <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                                  <div>
                                    <span className="text-[8px] font-bold text-text-muted uppercase block">Buy Qty</span>
                                    <span className="font-mono text-emerald-400 font-semibold">{pos.buy_quantity ?? 0}</span>
                                  </div>
                                  <div>
                                    <span className="text-[8px] font-bold text-text-muted uppercase block">Sell Qty</span>
                                    <span className="font-mono text-rose-400 font-semibold">{pos.sell_quantity ?? 0}</span>
                                  </div>
                                  <div>
                                    <span className="text-[8px] font-bold text-text-muted uppercase block">Avg Buy Price</span>
                                    <span className="font-mono text-text-secondary">{(pos.buy_price ?? 0).toFixed(2)}</span>
                                  </div>
                                  <div>
                                    <span className="text-[8px] font-bold text-text-muted uppercase block">Avg Sell Price</span>
                                    <span className="font-mono text-text-secondary">{(pos.sell_price ?? 0).toFixed(2)}</span>
                                  </div>
                                </div>
                                <div className="border-t border-border-default/20 pt-2 mt-1 flex justify-between items-center text-[10px]">
                                  <span className="text-[8px] font-bold text-text-muted uppercase">Buy / Sell Value</span>
                                  <span className="font-mono text-text-primary font-semibold">
                                    {formatCurrency(pos.buy_value)} / {formatCurrency(pos.sell_value)}
                                  </span>
                                </div>
                              </div>

                              {/* Card Column 3: Intraday Returns Logic */}
                              <div className="border border-border-default/30 rounded-none bg-muted/50 p-3.5 space-y-2">
                                <span className="text-[9px] font-black uppercase tracking-wider text-rose-400 block border-b border-border-default/30 pb-1.5 mb-1.5">
                                  INTRADAY RETURNS LOGIC
                                </span>
                                <div className="flex justify-between">
                                  <span className="text-text-muted">Realised P&L</span>
                                  <span className={`font-mono font-bold ${getPnlClass(pos.realised ?? 0)}`}>
                                    {formatCurrency(pos.realised)}
                                  </span>
                                </div>
                                <div className="flex justify-between">
                                  <span className="text-text-muted">Unrealised P&L</span>
                                  <span className={`font-mono font-bold ${getPnlClass(pos.unrealised ?? 0)}`}>
                                    {formatCurrency(pos.unrealised)}
                                  </span>
                                </div>
                                <div className="flex justify-between border-t border-border-default/20 pt-2 mt-1">
                                  <span className="text-text-muted">Day Buy / Sell Qty</span>
                                  <span className="font-mono text-text-primary font-semibold">
                                    +{pos.day_buy_quantity ?? 0} / -{pos.day_sell_quantity ?? 0}
                                  </span>
                                </div>
                              </div>

                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}

                {(!positionsData || (subTab === 'net' ? positionsData.net.length : positionsData.day.length) === 0) && (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-text-muted italic">
                      No active {subTab} positions.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
