'use client';

import React, { useState } from 'react';
import { Layers, ChevronUp, ChevronDown, HelpCircle } from 'lucide-react';

interface PositionsSectionProps {
  broker: any;
  positionsData: any;
  formatCurrency: (val: number | undefined) => string;
  getPnlClass: (val: number) => string;
}

export default function PositionsSection({
  broker,
  positionsData,
  formatCurrency,
  getPnlClass,
}: PositionsSectionProps) {
  const [positionsSubTab, setPositionsSubTab] = useState<'net' | 'day'>('net');
  const [expandedPositionSymbol, setExpandedPositionSymbol] = useState<string | null>(null);

  if (!broker || !positionsData) return null;

  const activePositions = (positionsSubTab === 'net' ? positionsData.net : positionsData.day) || [];
  const positionsCount = activePositions.length;
  
  // Compute floating PnL across active Net positions
  const netPositionsList = positionsData.net || [];
  const totalFloatingPnl = netPositionsList.reduce((sum: number, p: any) => sum + (p.pnl || 0), 0);

  const toggleExpandPosition = (symbol: string) => {
    setExpandedPositionSymbol(expandedPositionSymbol === symbol ? null : symbol);
  };

  return (
    <div className="border border-border-default/40 bg-surface/30 p-4 space-y-3 rounded-none">
      <div className="flex items-center justify-between border-b border-border-default/20 pb-2">
        <div className="flex items-center gap-2">
          <Layers size={14} className="text-emerald-400" />
          <h3 className="text-xs font-black uppercase tracking-wider text-text-primary">Active Positions Ledger ({positionsCount})</h3>
        </div>
        
        <div className="flex items-center gap-4">
          {/* Positions SubTab switcher */}
          <div className="flex items-center gap-0 bg-muted/50 border border-border-default/20 rounded-none p-0">
            <button
              type="button"
              onClick={() => {
                setPositionsSubTab('net');
                setExpandedPositionSymbol(null);
              }}
              className={`rounded-none px-2.5 py-0.5 text-[9px] font-black uppercase tracking-wider transition-all border ${
                positionsSubTab === 'net'
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'text-text-muted hover:text-text-secondary border-transparent'
              }`}
            >
              Net ({positionsData.net?.length || 0})
            </button>
            <button
              type="button"
              onClick={() => {
                setPositionsSubTab('day');
                setExpandedPositionSymbol(null);
              }}
              className={`rounded-none px-2.5 py-0.5 text-[9px] font-black uppercase tracking-wider transition-all border-y border-r border-l-0 ${
                positionsSubTab === 'day'
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'text-text-muted hover:text-text-secondary border-transparent'
              }`}
            >
              Day ({positionsData.day?.length || 0})
            </button>
          </div>

          {/* Floating PnL Summary */}
          {positionsSubTab === 'net' && (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-[10px] text-text-secondary">Floating P&L:</span>
              <span className={`font-mono font-bold ${getPnlClass(totalFloatingPnl)}`}>
                {totalFloatingPnl >= 0 ? '+' : ''}{formatCurrency(totalFloatingPnl)}
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="overflow-x-auto border border-border-default/20 rounded-none bg-muted/30 max-h-[260px] scrollbar-thin">
        <table className="w-full text-left text-xs border-collapse">
          <thead className="bg-elevated/80 border-b border-border-default/30 sticky top-0 z-10">
            <tr>
              <th className="w-8 pl-3"></th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary">Symbol</th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary">Product</th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary text-right">Quantity</th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary text-right">Avg Price</th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary text-right">LTP</th>
              <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary text-right">Total PnL</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border-default/10 font-mono text-[11px]">
            {activePositions.map((pos: any, idx: number) => {
              const isShort = pos.quantity < 0;
              const isExpanded = expandedPositionSymbol === pos.tradingsymbol;
              return (
                <React.Fragment key={`${pos.tradingsymbol}-${idx}`}>
                  <tr 
                    onClick={() => toggleExpandPosition(pos.tradingsymbol)}
                    className={`hover:bg-elevated/5 cursor-pointer transition-colors ${isExpanded ? 'bg-elevated/5' : ''}`}
                  >
                    <td className="pl-3 py-2 text-center text-text-muted hover:text-text-primary">
                      {isExpanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                    </td>
                    <td className="px-3 py-2 font-sans font-bold text-text-primary flex items-center gap-1.5">
                      <span>{pos.tradingsymbol}</span>
                      <span className="text-[7px] bg-elevated border border-border-default text-text-secondary px-1 py-0.2 rounded-none font-mono">
                        {pos.exchange}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-text-secondary font-sans">{pos.product}</td>
                    <td className={`px-3 py-2 text-right font-semibold ${isShort ? 'text-rose-400' : pos.quantity > 0 ? 'text-emerald-400' : 'text-text-muted'}`}>
                      {isShort ? '' : pos.quantity > 0 ? '+' : ''}{pos.quantity}
                    </td>
                    <td className="px-3 py-2 text-right text-text-secondary">{(pos.average_price ?? 0).toFixed(2)}</td>
                    <td className="px-3 py-2 text-right text-text-primary">{(pos.last_price ?? 0).toFixed(2)}</td>
                    <td className={`px-3 py-2 text-right font-bold ${getPnlClass(pos.pnl ?? 0)}`}>
                      {pos.pnl >= 0 ? '+' : ''}{(pos.pnl ?? 0).toFixed(2)}
                    </td>
                  </tr>

                  {/* Deep mathematical grids accordion drawer */}
                  {isExpanded && (
                    <tr className="bg-surface/40 border-b border-border-default/20">
                      <td colSpan={7} className="p-4">
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-[10px] font-sans text-left animate-in fade-in slide-in-from-top-1 duration-150">
                          
                          {/* Card Column 1: Net Position Geometry */}
                          <div className="border border-border-default/30 rounded-none bg-muted/50 p-3 space-y-1.5">
                            <span className="text-[8px] font-black uppercase tracking-wider text-text-secondary block border-b border-border-default/30 pb-1 mb-1">
                              NET POSITION GEOMETRY
                            </span>
                            <div className="flex justify-between">
                              <span className="text-text-muted">Net Position Value</span>
                              <span className={`font-mono font-semibold ${(pos.value ?? 0) >= 0 ? 'text-bull' : 'text-[#ef4444]'}`}>
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
                            <div className="flex justify-between border-t border-border-default/20 pt-1.5 mt-1">
                              <span className="text-text-secondary font-bold">Mark to Market (M2M)</span>
                              <span className={`font-mono font-extrabold ${getPnlClass(pos.m2m ?? 0)}`}>
                                {formatCurrency(pos.m2m)}
                              </span>
                            </div>
                          </div>

                          {/* Card Column 2: Buy & Sell Accumulation */}
                          <div className="border border-border-default/30 rounded-none bg-muted/50 p-3 space-y-1.5">
                            <span className="text-[8px] font-black uppercase tracking-wider text-emerald-400 block border-b border-border-default/30 pb-1 mb-1">
                              BUY & SELL ACCUMULATION
                            </span>
                            <div className="grid grid-cols-2 gap-x-2 gap-y-1.5">
                              <div>
                                <span className="text-[7px] font-bold text-text-muted uppercase block">Buy Qty</span>
                                <span className="font-mono text-emerald-400 font-semibold">{pos.buy_quantity ?? 0}</span>
                              </div>
                              <div>
                                <span className="text-[7px] font-bold text-text-muted uppercase block">Sell Qty</span>
                                <span className="font-mono text-rose-400 font-semibold">{pos.sell_quantity ?? 0}</span>
                              </div>
                              <div>
                                <span className="text-[7px] font-bold text-text-muted uppercase block">Buy Price</span>
                                <span className="font-mono text-text-secondary">{(pos.buy_price ?? 0).toFixed(2)}</span>
                              </div>
                              <div>
                                <span className="text-[7px] font-bold text-text-muted uppercase block">Sell Price</span>
                                <span className="font-mono text-text-secondary">{(pos.sell_price ?? 0).toFixed(2)}</span>
                              </div>
                            </div>
                            <div className="border-t border-border-default/20 pt-1.5 mt-1 flex justify-between items-center text-[9px]">
                              <span className="text-[7px] font-bold text-text-muted uppercase">Buy / Sell Value</span>
                              <span className="font-mono text-text-primary font-semibold">
                                {formatCurrency(pos.buy_value)} / {formatCurrency(pos.sell_value)}
                              </span>
                            </div>
                          </div>

                          {/* Card Column 3: Intraday Returns Logic */}
                          <div className="border border-border-default/30 rounded-none bg-muted/50 p-3 space-y-1.5">
                            <span className="text-[8px] font-black uppercase tracking-wider text-rose-400 block border-b border-border-default/30 pb-1 mb-1">
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
                            <div className="flex justify-between border-t border-border-default/20 pt-1.5 mt-1">
                              <span className="text-text-muted">Day Buy/Sell</span>
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
            {activePositions.length === 0 && (
              <tr>
                <td colSpan={7} className="py-6 text-center text-text-muted italic font-sans">
                  No active {positionsSubTab} positions linked to this live account.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
