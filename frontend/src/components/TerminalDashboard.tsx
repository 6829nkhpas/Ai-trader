'use client';

import React, { useState, useEffect } from 'react';
import { useMargins, usePositions, useOrderBook } from '../hooks/useAlphaData';
import { 
  Shield, 
  Activity, 
  Layers, 
  ClipboardList, 
  RefreshCw, 
  TrendingUp, 
  TrendingDown, 
  AlertCircle, 
  HelpCircle,
  Clock
} from 'lucide-react';

export default function TerminalDashboard() {
  const [activeTab, setActiveTab] = useState<'risk' | 'positions' | 'orders'>('risk');
  const [positionsSubTab, setPositionsSubTab] = useState<'net' | 'day'>('net');

  // Load backend data hooks
  const { 
    data: marginsData, 
    loading: marginsLoading, 
    error: marginsError, 
    refetch: refetchMargins 
  } = useMargins();

  const { 
    data: positionsData, 
    loading: positionsLoading, 
    error: positionsError, 
    refetch: refetchPositions 
  } = usePositions();

  const { 
    orders: ordersData, 
    loading: ordersLoading, 
    error: ordersError, 
    refetch: refetchOrders 
  } = useOrderBook();

  // Helper to format currency
  const formatCurrency = (val: number | undefined) => {
    if (val === undefined) return '₹0.00';
    return `₹${val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  // Status tag badge styling
  const getOrderStatusClass = (status: string) => {
    switch (status.toUpperCase()) {
      case 'COMPLETE':
        return 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20';
      case 'REJECTED':
        return 'bg-rose-500/10 text-rose-400 border border-rose-500/20';
      case 'OPEN':
      case 'PENDING':
        return 'bg-amber-500/10 text-amber-400 border border-amber-500/20';
      default:
        return 'bg-slate-500/10 text-slate-400 border border-slate-500/20';
    }
  };

  // P&L class selection
  const getPnlClass = (val: number) => {
    if (val > 0) return 'text-[#22c55e]';
    if (val < 0) return 'text-[#ef4444]';
    return 'text-text-secondary';
  };

  // Auto-hydrate specific tabs on tab switch
  useEffect(() => {
    if (activeTab === 'risk') {
      refetchMargins();
    } else if (activeTab === 'positions') {
      refetchPositions();
    } else if (activeTab === 'orders') {
      refetchOrders();
    }
  }, [activeTab]);

  return (
    <div className="flex flex-col border border-border-default rounded-lg bg-surface/80 backdrop-blur-md panel-shadow-lg overflow-hidden mt-2">
      {/* Dashboard Top Header & Tabs */}
      <div className="flex items-center justify-between border-b border-border-default bg-surface/50 px-4 py-2 shrink-0">
        <div className="flex items-center gap-1.5">
          <Activity size={14} className="text-emerald-400" />
          <span className="text-[10px] font-black uppercase tracking-wider text-text-primary">
            Risk & Portfolio Engine
          </span>
        </div>

        {/* Tab Selection */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setActiveTab('risk')}
            className={`rounded-md px-3 py-1 text-xs font-semibold transition-all duration-200 ${
              activeTab === 'risk'
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                : 'text-text-secondary hover:bg-elevated hover:text-text-primary border border-transparent'
            }`}
          >
            <div className="flex items-center gap-1.5">
              <Shield size={12} />
              <span>Risk & Margins</span>
            </div>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('positions')}
            className={`rounded-md px-3 py-1 text-xs font-semibold transition-all duration-200 ${
              activeTab === 'positions'
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                : 'text-text-secondary hover:bg-elevated hover:text-text-primary border border-transparent'
            }`}
          >
            <div className="flex items-center gap-1.5">
              <Layers size={12} />
              <span>Active Positions</span>
            </div>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('orders')}
            className={`rounded-md px-3 py-1 text-xs font-semibold transition-all duration-200 ${
              activeTab === 'orders'
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                : 'text-text-secondary hover:bg-elevated hover:text-text-primary border border-transparent'
            }`}
          >
            <div className="flex items-center gap-1.5">
              <ClipboardList size={12} />
              <span>Order Book</span>
            </div>
          </button>
        </div>
      </div>

      {/* Main Tab Content Panels */}
      <div className="p-4 overflow-y-auto max-h-[350px] min-h-[160px] bg-surface/30">
        
        {/* TAB 1: RISK & MARGINS */}
        {activeTab === 'risk' && (
          <div className="space-y-4">
            {marginsError && (
              <div className="flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-400">
                <AlertCircle size={14} />
                <span>{marginsError}</span>
              </div>
            )}

            {marginsLoading && !marginsData && (
              <div className="flex h-24 items-center justify-center text-xs text-text-muted">
                <RefreshCw size={14} className="animate-spin mr-2 text-emerald-400" />
                Loading risk limits and margins...
              </div>
            )}

            {marginsData && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* Large Typography Available Margin */}
                <div className="md:col-span-1 border border-border-default rounded-xl bg-surface/40 p-4 flex flex-col justify-center">
                  <div className="text-[10px] font-black uppercase tracking-wider text-text-muted">
                    Available Margin
                  </div>
                  <div className="text-3xl font-black tracking-tight text-white mt-1 font-mono">
                    {formatCurrency(
                      marginsData.equity?.net !== undefined && marginsData.equity?.net !== 0
                        ? marginsData.equity.net
                        : ((marginsData.equity?.available as any)?.live_balance ?? marginsData.equity?.available?.cash)
                    )}
                  </div>
                  <div className="text-[10px] text-text-secondary mt-1 flex items-center gap-1">
                    <span>Net Leverage Power</span>
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                  </div>
                </div>

                {/* Utilised Metrics Tables */}
                <div className="md:col-span-2 border border-border-default rounded-xl bg-surface/40 p-4">
                  <div className="text-[10px] font-black uppercase tracking-wider text-text-muted mb-3">
                    MARGIN UTILISATION BREAKDOWN
                  </div>
                  <div className="grid grid-cols-2 gap-x-8 gap-y-3 text-xs">
                    <div className="flex items-center justify-between border-b border-border-default/30 pb-1.5">
                      <span className="text-text-secondary font-medium">Total Margin Net</span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(marginsData.equity?.net)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between border-b border-border-default/30 pb-1.5">
                      <span className="text-text-secondary font-medium flex items-center gap-1">
                        Utilised M2M
                        <span className="cursor-help" title="Real-time mark to market margin deduction">
                          <HelpCircle size={10} className="text-text-muted" />
                        </span>
                      </span>
                      <span className={`font-mono font-semibold ${getPnlClass(marginsData.equity?.utilised.m2m || 0)}`}>
                        {formatCurrency(marginsData.equity?.utilised.m2m)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between border-b border-border-default/30 pb-1.5">
                      <span className="text-text-secondary font-medium">Margin Utilised (Debits)</span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(marginsData.equity?.utilised.debits)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between border-b border-border-default/30 pb-1.5">
                      <span className="text-text-secondary font-medium flex items-center gap-1">
                        Active Exposure
                        <span className="cursor-help" title="Margin required for open futures/options/MIS trades">
                          <HelpCircle size={10} className="text-text-muted" />
                        </span>
                      </span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(marginsData.equity?.utilised.exposure)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* TAB 2: ACTIVE POSITIONS */}
        {activeTab === 'positions' && (
          <div className="space-y-3">
            {positionsError && (
              <div className="flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-400">
                <AlertCircle size={14} />
                <span>{positionsError}</span>
              </div>
            )}

            {positionsLoading && !positionsData && (
              <div className="flex h-24 items-center justify-center text-xs text-text-muted">
                <RefreshCw size={14} className="animate-spin mr-2 text-emerald-400" />
                Loading portfolio positions...
              </div>
            )}

            {positionsData && (
              <>
                {/* Positions Sub-Tabs */}
                <div className="flex items-center gap-1 border-b border-border-default/40 pb-2">
                  <button
                    type="button"
                    onClick={() => setPositionsSubTab('net')}
                    className={`rounded px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider transition-all ${
                      positionsSubTab === 'net'
                        ? 'bg-elevated text-emerald-400 border border-border-default'
                        : 'text-text-muted hover:text-text-secondary'
                    }`}
                  >
                    Net Positions ({positionsData.net?.length || 0})
                  </button>
                  <button
                    type="button"
                    onClick={() => setPositionsSubTab('day')}
                    className={`rounded px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider transition-all ${
                      positionsSubTab === 'day'
                        ? 'bg-elevated text-emerald-400 border border-border-default'
                        : 'text-text-muted hover:text-text-secondary'
                    }`}
                  >
                    Day Positions ({positionsData.day?.length || 0})
                  </button>
                </div>

                {/* Positions Data Grid */}
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead>
                      <tr className="border-b border-border-default/60 text-text-muted text-[10px] uppercase font-bold tracking-wider">
                        <th className="py-2">Symbol</th>
                        <th className="py-2">Product</th>
                        <th className="py-2 text-right">Qty</th>
                        <th className="py-2 text-right">Avg Price</th>
                        <th className="py-2 text-right">LTP</th>
                        <th className="py-2 text-right">M2M / PnL</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-default/20">
                      {(positionsSubTab === 'net' ? positionsData.net : positionsData.day)?.map((pos, idx) => {
                        const isShort = pos.quantity < 0;
                        const qtyText = isShort ? `${pos.quantity}` : `+${pos.quantity}`;
                        const qtyClass = isShort ? 'text-rose-400 font-semibold' : 'text-emerald-400 font-semibold';
                        
                        return (
                          <tr key={`${pos.tradingsymbol}-${idx}`} className="hover:bg-elevated/20 transition-colors">
                            <td className="py-2.5 font-bold text-white flex items-center gap-1.5">
                              <span>{pos.tradingsymbol}</span>
                              <span className="text-[8px] bg-surface-elevated text-text-secondary px-1 py-0.5 rounded font-mono">
                                {pos.exchange}
                              </span>
                            </td>
                            <td className="py-2.5 text-text-secondary font-mono">{pos.product}</td>
                            <td className={`py-2.5 text-right font-mono ${qtyClass}`}>{qtyText}</td>
                            <td className="py-2.5 text-right font-mono text-text-secondary">
                              {pos.average_price.toFixed(2)}
                            </td>
                            <td className="py-2.5 text-right font-mono text-white">
                              {pos.last_price.toFixed(2)}
                            </td>
                            <td className={`py-2.5 text-right font-mono font-bold ${getPnlClass(pos.pnl)}`}>
                              {pos.pnl >= 0 ? '+' : ''}{pos.pnl.toFixed(2)}
                            </td>
                          </tr>
                        );
                      })}

                      {(!positionsData.net || (positionsSubTab === 'net' ? positionsData.net.length : positionsData.day.length) === 0) && (
                        <tr>
                          <td colSpan={6} className="py-8 text-center text-text-muted italic">
                            No active {positionsSubTab} positions.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        )}

        {/* TAB 3: ORDER BOOK */}
        {activeTab === 'orders' && (
          <div className="space-y-3">
            {ordersError && (
              <div className="flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-400">
                <AlertCircle size={14} />
                <span>{ordersError}</span>
              </div>
            )}

            {ordersLoading && !ordersData.length && (
              <div className="flex h-24 items-center justify-center text-xs text-text-muted">
                <RefreshCw size={14} className="animate-spin mr-2 text-emerald-400" />
                Loading orders...
              </div>
            )}

            {ordersData && ordersData.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="border-b border-border-default/60 text-text-muted text-[10px] uppercase font-bold tracking-wider">
                      <th className="py-2">Time</th>
                      <th className="py-2">Type</th>
                      <th className="py-2">Symbol</th>
                      <th className="py-2 text-right">Qty</th>
                      <th className="py-2 text-right">Price</th>
                      <th className="py-2 text-center">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-default/20">
                    {ordersData.map((order) => {
                      const timeStr = order.order_timestamp ? order.order_timestamp.split(' ')[1] : '--:--:--';
                      const isBuy = order.transaction_type.toUpperCase() === 'BUY';
                      
                      return (
                        <tr key={order.order_id} className="hover:bg-elevated/20 transition-colors">
                          <td className="py-2.5 font-mono text-text-muted flex items-center gap-1.5">
                            <Clock size={10} className="text-text-muted/50" />
                            <span>{timeStr}</span>
                          </td>
                          <td className="py-2.5">
                            <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${
                              isBuy ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'
                            }`}>
                              {order.transaction_type}
                            </span>
                          </td>
                          <td className="py-2.5 font-bold text-white">
                            {order.tradingsymbol}
                            <span className="text-[8px] text-text-secondary ml-1 bg-surface-elevated px-1 py-0.5 rounded font-mono">
                              {order.product}
                            </span>
                          </td>
                          <td className="py-2.5 text-right font-mono text-white">{order.quantity}</td>
                          <td className="py-2.5 text-right font-mono text-text-secondary">
                            {order.average_price > 0 ? order.average_price.toFixed(2) : order.price.toFixed(2)}
                          </td>
                          <td className="py-2.5 text-center">
                            {order.status === 'REJECTED' && order.status_message ? (
                              <div className="relative inline-block group cursor-help">
                                <span className={`inline-flex rounded-full px-2 py-0.5 text-[9px] font-bold ${getOrderStatusClass(order.status)}`}>
                                  {order.status}
                                </span>
                                
                                {/* Custom Premium Tooltip */}
                                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-56 hidden group-hover:block z-50 transition-all duration-200">
                                  <div className="bg-[#0f172a] border border-[#f43f5e]/30 text-rose-300 rounded-lg p-2 shadow-2xl text-[10px] text-center font-semibold leading-normal">
                                    {order.status_message}
                                    <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-1 border-4 border-transparent border-t-[#0f172a]" />
                                  </div>
                                </div>
                              </div>
                            ) : (
                              <span className={`inline-flex rounded-full px-2 py-0.5 text-[9px] font-bold ${getOrderStatusClass(order.status)}`}>
                                {order.status}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              !ordersLoading && (
                <div className="py-8 text-center text-text-muted italic">
                  No orders registered today.
                </div>
              )
            )}
          </div>
        )}

      </div>
    </div>
  );
}
