import React, { useState } from 'react';
import { 
  Calendar, 
  Shield, 
  Link2, 
  Activity, 
  Layers, 
  ClipboardList, 
  Wallet, 
  Sparkles, 
  AlertCircle, 
  Clock, 
  ChevronDown, 
  ChevronUp, 
  CheckCircle, 
  XCircle, 
  HelpCircle, 
  Tag 
} from 'lucide-react';

interface ProfileTabProps {
  user: any;
  paperPortfolio: any;
  formatDate: (date: any) => string;
  realWalletBalance?: number;
  marginsData?: any;
  positionsData?: any;
  orders?: any[];
}

export default function ProfileTab({ 
  user, 
  paperPortfolio, 
  formatDate, 
  realWalletBalance,
  marginsData,
  positionsData,
  orders
}: ProfileTabProps) {
  const broker = user?.brokerConnection;

  // Local tab and accordion states for deep detailed sections
  const [marginSegment, setMarginSegment] = useState<'equity' | 'commodity'>('equity');
  const [positionsSubTab, setPositionsSubTab] = useState<'net' | 'day'>('net');
  const [expandedPositionSymbol, setExpandedPositionSymbol] = useState<string | null>(null);
  const [expandedOrderId, setExpandedOrderId] = useState<string | null>(null);

  // ── MARGIN CALCULATIONS ──
  const activeSegmentData = marginsData?.[marginSegment];

  // ── POSITIONS CALCULATIONS ──
  const activePositions = (positionsSubTab === 'net' ? positionsData?.net : positionsData?.day) || [];
  const positionsCount = activePositions.length;
  
  // Compute floating PnL across active Net positions
  const netPositionsList = positionsData?.net || [];
  const totalFloatingPnl = netPositionsList.reduce((sum: number, p: any) => sum + (p.pnl || 0), 0);

  // ── ORDERS CALCULATIONS ──
  const totalOrders = orders?.length || 0;
  const completedOrders = orders?.filter((o: any) => o.status === 'COMPLETE').length || 0;
  const rejectedOrders = orders?.filter((o: any) => o.status === 'REJECTED').length || 0;

  const formatCurrency = (val: number | undefined) => {
    if (val === undefined || val === null) return '₹0.00';
    return `₹${val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  const getPnlClass = (val: number) => {
    if (val > 0) return 'text-bull font-bold';
    if (val < 0) return 'text-[#ef4444] font-bold';
    return 'text-text-secondary';
  };

  const getOrderStatusClass = (status: string) => {
    switch (status?.toUpperCase()) {
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

  const toggleExpandPosition = (symbol: string) => {
    setExpandedPositionSymbol(expandedPositionSymbol === symbol ? null : symbol);
  };

  const toggleExpandOrder = (orderId: string) => {
    setExpandedOrderId(expandedOrderId === orderId ? null : orderId);
  };

  return (
    <div className="space-y-6 flex flex-col h-full overflow-y-auto pr-1 scrollbar-none">
      
      {/* ── ACCOUNT IDENTITY HEADER ── */}
      <div className="flex items-center gap-4 border-b border-border-default/20 pb-4 shrink-0">
        {broker?.avatarUrl ? (
          <img 
            src={broker.avatarUrl} 
            alt={user?.name || 'Profile Avatar'} 
            className="h-16 w-16 rounded-2xl object-cover border-2 border-emerald-500/30 shadow-lg"
          />
        ) : (
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-2xl font-black tracking-wider shadow-inner">
            {(() => {
              const name = user?.name || '';
              if (!name) return 'SA';
              const parts = name.trim().split(/\s+/);
              if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
              return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
            })()}
          </div>
        )}
        <div>
          <h2 className="text-2xl font-black text-white tracking-tight leading-none">{user?.name || 'Strat AI Client'}</h2>
          <div className="flex items-center gap-2 mt-2">
            <p className="text-xs text-text-secondary font-medium">{user?.email || 'No email registered'}</p>
            {broker && (
              <span className="flex items-center gap-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-0.5 text-[9px] font-bold text-emerald-400 uppercase tracking-wide">
                <Link2 size={10} />
                {broker.brokerUserId}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* ── METADATA ACCOUNT DETAILS CARD GRID ── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 shrink-0">
        <div className="rounded-xl border border-border-default/40 bg-surface/40 p-4">
          <span className="text-[9px] uppercase font-black tracking-widest text-text-secondary">ACCOUNT TIER LEVEL</span>
          <p className="text-base font-black text-white mt-1.5 flex items-center gap-1.5">
            <Shield size={14} className="text-emerald-400" />
            <span>{user?.tier || 'FREE'} Tier</span>
          </p>
        </div>
        <div className="rounded-xl border border-border-default/40 bg-surface/40 p-4">
          <span className="text-[9px] uppercase font-black tracking-widest text-text-secondary">MEMBER REGISTRATION</span>
          <div className="flex items-center gap-2 text-sm font-bold text-white mt-1.5">
            <Calendar size={14} className="text-text-muted" />
            <span>{formatDate(user?.createdAt)}</span>
          </div>
        </div>
        <div className="rounded-xl border border-border-default/40 bg-surface/40 p-4">
          <span className="text-[9px] uppercase font-black tracking-widest text-text-secondary">LIVE WALLET BALANCE</span>
          <p className="text-base font-black text-emerald-400 mt-1.5 font-mono">
            {formatCurrency(realWalletBalance)}
          </p>
        </div>
      </div>

      {/* ── LIVE DEEP FUNDS & MARGINS BREAKDOWN ── */}
      {broker && marginsData && (
        <div className="border border-border-default/40 rounded-xl bg-surface/30 p-4 space-y-4">
          <div className="flex items-center justify-between border-b border-border-default/20 pb-2">
            <div className="flex items-center gap-2">
              <Wallet size={14} className="text-emerald-400" />
              <h3 className="text-xs font-black uppercase tracking-wider text-white">Live Segment Funds & Limits</h3>
            </div>
            
            <div className="flex items-center gap-3">
              {/* Segment Toggles */}
              <div className="flex items-center gap-1 bg-muted/50 border border-border-default/20 rounded p-0.5">
                <button
                  type="button"
                  onClick={() => setMarginSegment('equity')}
                  className={`rounded px-2.5 py-0.5 text-[9px] font-black uppercase tracking-wider transition-all ${
                    marginSegment === 'equity'
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : 'text-text-muted hover:text-text-secondary border border-transparent'
                  }`}
                >
                  Equity
                </button>
                <button
                  type="button"
                  onClick={() => setMarginSegment('commodity')}
                  className={`rounded px-2.5 py-0.5 text-[9px] font-black uppercase tracking-wider transition-all ${
                    marginSegment === 'commodity'
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : 'text-text-muted hover:text-text-secondary border border-transparent'
                  }`}
                >
                  Commodity
                </button>
              </div>

              {/* Active Badge */}
              {activeSegmentData && (
                <div className="text-[9px] font-bold uppercase tracking-wider">
                  {activeSegmentData.enabled ? (
                    <span className="flex items-center gap-1 text-emerald-400 bg-emerald-500/5 border border-emerald-500/10 px-2 py-0.5 rounded">
                      <CheckCircle size={9} /> Active
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-text-muted bg-surface/50 border border-border-default/50 px-2 py-0.5 rounded">
                      <XCircle size={9} /> Inactive
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>
          
          {activeSegmentData ? (
            <div className="space-y-4">
              {/* Giant Net Power Card */}
              <div className="border border-border-default/30 rounded-lg bg-muted/30 p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <span className="text-[9px] font-bold uppercase tracking-wider text-text-secondary">Available Net Margin</span>
                  <span className="text-2xl font-black tracking-tight text-white mt-0.5 block font-mono">
                    {formatCurrency(activeSegmentData.net)}
                  </span>
                </div>
                <div className="text-[9px] text-text-secondary font-medium sm:text-right">
                  <span className="block font-bold text-white">True Segment Purchasing Power</span>
                  <span className="text-text-muted mt-0.5 block leading-normal">
                    This net balance reflects absolute leverage power for the {marginSegment} segment.
                  </span>
                </div>
              </div>

              {/* Two Column Grid displaying ALL data points */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* COLUMN 1: AVAILABLE FUNDS BREAKDOWN */}
                <div className="border border-border-default/30 rounded-lg bg-muted/50 p-3.5 space-y-2">
                  <span className="text-[9px] font-black uppercase tracking-wider text-emerald-400 block mb-2 border-b border-border-default/20 pb-1">
                    AVAILABLE LIMITS & CASH
                  </span>
                  <div className="space-y-1.5 text-[11px]">
                    <div className="flex items-center justify-between pb-0.5 border-b border-border-default/10">
                      <span className="text-text-secondary font-medium">Opening Balance</span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(activeSegmentData.available?.opening_balance)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between pb-0.5 border-b border-border-default/10">
                      <span className="text-text-secondary font-medium">Opening Cash</span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(activeSegmentData.available?.cash)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between pb-0.5 border-b border-border-default/10">
                      <span className="text-text-secondary font-medium flex items-center gap-1 cursor-help" title="Value of securities/holdings pledged for margin trading">
                        Collateral Margin
                        <HelpCircle size={9} className="text-text-muted" />
                      </span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(activeSegmentData.available?.collateral)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between pb-0.5 border-b border-border-default/10">
                      <span className="text-text-secondary font-medium">Intraday Payin</span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(activeSegmentData.available?.intraday_payin)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between pb-0.5 border-b border-border-default/10">
                      <span className="text-text-secondary font-medium">Adhoc Margin</span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(activeSegmentData.available?.adhoc_margin)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between pt-1">
                      <span className="text-text-primary font-bold">Live Available Balance</span>
                      <span className="font-mono text-emerald-400 font-extrabold">
                        {formatCurrency(activeSegmentData.available?.live_balance)}
                      </span>
                    </div>
                  </div>
                </div>

                {/* COLUMN 2: UTILISHED MARGINS BREAKDOWN */}
                <div className="border border-border-default/30 rounded-lg bg-muted/50 p-3.5 space-y-2">
                  <span className="text-[9px] font-black uppercase tracking-wider text-rose-400 block mb-2 border-b border-border-default/20 pb-1">
                    UTILISHED MARGIN DEBITS
                  </span>
                  <div className="space-y-1.5 text-[11px]">
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
                      <div className="flex items-center justify-between border-b border-border-default/10 pb-0.5">
                        <span className="text-text-secondary">SPAN Margin</span>
                        <span className="font-mono text-white font-semibold">{formatCurrency(activeSegmentData.utilised?.span)}</span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/10 pb-0.5">
                        <span className="text-text-secondary">Exposure Margin</span>
                        <span className="font-mono text-white font-semibold">{formatCurrency(activeSegmentData.utilised?.exposure)}</span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/10 pb-0.5">
                        <span className="text-text-secondary flex items-center gap-0.5 cursor-help" title="Booked profits or losses from intraday trades">
                          Realised M2M
                          <HelpCircle size={8} className="text-text-muted" />
                        </span>
                        <span className={`font-mono ${getPnlClass(activeSegmentData.utilised?.m2m_realised || 0)}`}>
                          {formatCurrency(activeSegmentData.utilised?.m2m_realised)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/10 pb-0.5">
                        <span className="text-text-secondary flex items-center gap-0.5 cursor-help" title="Running float profit or loss of active structures">
                          Unrealised M2M
                          <HelpCircle size={8} className="text-text-muted" />
                        </span>
                        <span className={`font-mono ${getPnlClass(activeSegmentData.utilised?.m2m_unrealised || 0)}`}>
                          {formatCurrency(activeSegmentData.utilised?.m2m_unrealised)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/10 pb-0.5">
                        <span className="text-text-secondary">Option Premium</span>
                        <span className="font-mono text-white font-semibold">{formatCurrency(activeSegmentData.utilised?.option_premium)}</span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/10 pb-0.5">
                        <span className="text-text-secondary">Holding Sales</span>
                        <span className="font-mono text-white font-semibold">{formatCurrency(activeSegmentData.utilised?.holding_sales)}</span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/10 pb-0.5">
                        <span className="text-text-secondary">Payout P&D</span>
                        <span className="font-mono text-white font-semibold">{formatCurrency(activeSegmentData.utilised?.payout)}</span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/10 pb-0.5">
                        <span className="text-text-secondary">Delivery Margin</span>
                        <span className="font-mono text-white font-semibold">{formatCurrency(activeSegmentData.utilised?.delivery)}</span>
                      </div>
                    </div>
                    <div className="flex items-center justify-between pt-1 border-t border-border-default/20 mt-1">
                      <span className="text-text-primary font-bold">Total Margin Utilised (Debits)</span>
                      <span className="font-mono text-rose-400 font-extrabold">
                        {formatCurrency(activeSegmentData.utilised?.debits)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-text-muted italic py-2">No active segment margins found for {marginSegment}.</p>
          )}
        </div>
      )}

      {/* ── LIVE ACTIVE POSITIONS LEDGER ── */}
      {broker && positionsData && (
        <div className="border border-border-default/40 rounded-xl bg-surface/30 p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-border-default/20 pb-2">
            <div className="flex items-center gap-2">
              <Layers size={14} className="text-emerald-400" />
              <h3 className="text-xs font-black uppercase tracking-wider text-white">Active Positions Ledger ({positionsCount})</h3>
            </div>
            
            <div className="flex items-center gap-4">
              {/* Positions SubTab switcher */}
              <div className="flex items-center gap-1 bg-muted/50 border border-border-default/20 rounded p-0.5">
                <button
                  type="button"
                  onClick={() => {
                    setPositionsSubTab('net');
                    setExpandedPositionSymbol(null);
                  }}
                  className={`rounded px-2 py-0.5 text-[9px] font-black uppercase tracking-wider transition-all ${
                    positionsSubTab === 'net'
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : 'text-text-muted hover:text-text-secondary border border-transparent'
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
                  className={`rounded px-2 py-0.5 text-[9px] font-black uppercase tracking-wider transition-all ${
                    positionsSubTab === 'day'
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                      : 'text-text-muted hover:text-text-secondary border border-transparent'
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

          <div className="overflow-x-auto border border-border-default/20 rounded-lg bg-muted/30 max-h-[260px] scrollbar-thin">
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
                        <td className="pl-3 py-2 text-center text-text-muted hover:text-white">
                          {isExpanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                        </td>
                        <td className="px-3 py-2 font-sans font-bold text-white flex items-center gap-1.5">
                          <span>{pos.tradingsymbol}</span>
                          <span className="text-[7px] bg-surface-elevated text-text-secondary px-1 py-0.2 rounded font-mono">
                            {pos.exchange}
                          </span>
                        </td>
                        <td className="px-3 py-2 text-text-secondary font-sans">{pos.product}</td>
                        <td className={`px-3 py-2 text-right font-semibold ${isShort ? 'text-rose-400' : pos.quantity > 0 ? 'text-emerald-400' : 'text-text-muted'}`}>
                          {isShort ? '' : pos.quantity > 0 ? '+' : ''}{pos.quantity}
                        </td>
                        <td className="px-3 py-2 text-right text-text-secondary">{(pos.average_price ?? 0).toFixed(2)}</td>
                        <td className="px-3 py-2 text-right text-white">{(pos.last_price ?? 0).toFixed(2)}</td>
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
                              <div className="border border-border-default/30 rounded-lg bg-muted/50 p-3 space-y-1.5">
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
                                  <span className="font-mono text-white font-semibold">{pos.overnight_quantity ?? 0}</span>
                                </div>
                                <div className="flex justify-between">
                                  <span className="text-text-muted">Lot Multiplier</span>
                                  <span className="font-mono text-white font-semibold">x{pos.multiplier ?? 1}</span>
                                </div>
                                <div className="flex justify-between border-t border-border-default/20 pt-1.5 mt-1">
                                  <span className="text-text-secondary font-bold">Mark to Market (M2M)</span>
                                  <span className={`font-mono font-extrabold ${getPnlClass(pos.m2m ?? 0)}`}>
                                    {formatCurrency(pos.m2m)}
                                  </span>
                                </div>
                              </div>

                              {/* Card Column 2: Buy & Sell Accumulation */}
                              <div className="border border-border-default/30 rounded-lg bg-muted/50 p-3 space-y-1.5">
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
                                    <span className="text-[7px] font-bold text-text-muted uppercase block">Avg Buy Price</span>
                                    <span className="font-mono text-text-secondary">{(pos.buy_price ?? 0).toFixed(2)}</span>
                                  </div>
                                  <div>
                                    <span className="text-[7px] font-bold text-text-muted uppercase block">Avg Sell Price</span>
                                    <span className="font-mono text-text-secondary">{(pos.sell_price ?? 0).toFixed(2)}</span>
                                  </div>
                                </div>
                                <div className="border-t border-border-default/20 pt-1.5 mt-1 flex justify-between items-center text-[9px]">
                                  <span className="text-[7px] font-bold text-text-muted uppercase">Buy / Sell Value</span>
                                  <span className="font-mono text-white font-semibold">
                                    {formatCurrency(pos.buy_value)} / {formatCurrency(pos.sell_value)}
                                  </span>
                                </div>
                              </div>

                              {/* Card Column 3: Intraday Returns Logic */}
                              <div className="border border-border-default/30 rounded-lg bg-muted/50 p-3 space-y-1.5">
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
                                  <span className="text-text-muted">Day Buy / Sell Qty</span>
                                  <span className="font-mono text-white font-semibold">
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
      )}

      {/* ── LIVE DAILY ORDERS LOG ── */}
      {broker && orders && (
        <div className="border border-border-default/40 rounded-xl bg-surface/30 p-4 space-y-3">
          <div className="flex items-center justify-between border-b border-border-default/20 pb-2">
            <div className="flex items-center gap-2">
              <ClipboardList size={14} className="text-emerald-400" />
              <h3 className="text-xs font-black uppercase tracking-wider text-white">Daily Order Execution Log ({totalOrders})</h3>
            </div>
            <div className="flex items-center gap-1.5 text-[9px] font-bold tracking-wider font-mono">
              <span className="text-emerald-400 bg-emerald-500/5 px-1.5 py-0.5 rounded border border-emerald-500/10">Done: {completedOrders}</span>
              <span className="text-rose-400 bg-rose-500/5 px-1.5 py-0.5 rounded border border-rose-500/10">Rej: {rejectedOrders}</span>
            </div>
          </div>

          <div className="overflow-x-auto border border-border-default/20 rounded-lg bg-muted/30 max-h-[260px] scrollbar-thin">
            <table className="w-full text-left text-xs border-collapse">
              <thead className="bg-elevated/80 border-b border-border-default/30 sticky top-0 z-10">
                <tr>
                  <th className="w-8 pl-3"></th>
                  <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary">Time</th>
                  <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary">Type</th>
                  <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary">Symbol</th>
                  <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary text-right">Quantity</th>
                  <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary text-right">Price</th>
                  <th className="px-3 py-2 text-[8px] font-black uppercase tracking-wider text-text-secondary text-center">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-default/10 font-mono text-[11px]">
                {orders.map((order: any, idx: number) => {
                  const timeStr = order.order_timestamp ? order.order_timestamp.split(' ')[1] : '--:--:--';
                  const isBuy = order.transaction_type?.toUpperCase() === 'BUY';
                  const isExpanded = expandedOrderId === order.order_id;
                  return (
                    <React.Fragment key={order.order_id || idx}>
                      <tr 
                        onClick={() => toggleExpandOrder(order.order_id)}
                        className={`hover:bg-elevated/5 cursor-pointer transition-colors ${isExpanded ? 'bg-elevated/5' : ''}`}
                      >
                        <td className="pl-3 py-2 text-center text-text-muted hover:text-white">
                          {isExpanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                        </td>
                        <td className="px-3 py-2 text-text-muted flex items-center gap-1 font-sans">
                          <Clock size={9} className="text-text-muted/65" />
                          <span>{timeStr}</span>
                        </td>
                        <td className="px-3 py-2 font-sans">
                          <span className={`text-[8px] font-bold px-1.5 py-0.2 rounded ${isBuy ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}`}>
                            {order.transaction_type}
                          </span>
                        </td>
                        <td className="px-3 py-2 font-sans font-bold text-white">
                          {order.tradingsymbol}
                          <span className="text-[7px] text-text-secondary ml-1 bg-surface-elevated px-1 py-0.2 rounded">{order.product}</span>
                        </td>
                        <td className="px-3 py-2 text-right text-white">{order.quantity}</td>
                        <td className="px-3 py-2 text-right text-text-secondary">
                          {order.average_price > 0 ? (order.average_price ?? 0).toFixed(2) : (order.price ?? 0).toFixed(2)}
                        </td>
                        <td className="px-3 py-2 text-center font-sans">
                          <span className={`inline-flex rounded-full px-1.5 py-0.2 text-[8px] font-bold ${getOrderStatusClass(order.status)}`}>
                            {order.status}
                          </span>
                        </td>
                      </tr>

                      {/* Expandable Order Details Drawer */}
                      {isExpanded && (
                        <tr className="bg-surface/40 border-b border-border-default/20">
                          <td colSpan={7} className="p-4">
                            <div className="space-y-3 text-left font-sans">
                              {/* Rejection Banner */}
                              {order.status === 'REJECTED' && order.status_message && (
                                <div className="flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-400">
                                  <AlertCircle size={14} className="shrink-0 mt-0.5" />
                                  <div className="space-y-0.5">
                                    <span className="font-bold block text-[11px]">OMS Rejection Reason:</span>
                                    <span className="font-medium leading-relaxed text-[10px]">{order.status_message}</span>
                                    {order.status_message_raw && (
                                      <span className="text-[8px] text-rose-500/70 block font-mono mt-1">Raw OMS log: {order.status_message_raw}</span>
                                    )}
                                  </div>
                                </div>
                              )}

                              <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-[10px] font-sans">
                                {/* Column 1: Order Properties & Route */}
                                <div className="border border-border-default/30 rounded-lg bg-muted/50 p-3 space-y-1.5">
                                  <span className="text-[8px] font-black uppercase tracking-wider text-text-secondary block border-b border-border-default/30 pb-1 mb-1">
                                    ORDER PROPERTIES & ROUTE
                                  </span>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Order Variety</span>
                                    <span className="font-mono text-white font-semibold uppercase">{order.variety ?? 'regular'}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Order ID</span>
                                    <span className="font-mono text-white font-semibold">{order.order_id}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Exchange ID</span>
                                    <span className="font-mono text-white font-semibold truncate max-w-[120px]" title={order.exchange_order_id}>
                                      {order.exchange_order_id ?? 'Pending Submission'}
                                    </span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Placed By</span>
                                    <span className="font-mono text-white font-semibold">{order.placed_by ?? 'Client Terminal'}</span>
                                  </div>
                                  {order.parent_order_id && (
                                    <div className="flex justify-between border-t border-border-default/10 pt-1 mt-0.5">
                                      <span className="text-text-muted">Parent Order ID</span>
                                      <span className="font-mono text-emerald-400 font-semibold">{order.parent_order_id}</span>
                                    </div>
                                  )}
                                </div>

                                {/* Column 2: Quantity & Slicing Ledgers */}
                                <div className="border border-border-default/30 rounded-lg bg-muted/50 p-3 space-y-1.5">
                                  <span className="text-[8px] font-black uppercase tracking-wider text-emerald-400 block border-b border-border-default/30 pb-1 mb-1">
                                    QUANTITY & SLICING LEDGER
                                  </span>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Total Ordered Qty</span>
                                    <span className="font-mono text-white font-semibold">{order.quantity}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Filled Quantity</span>
                                    <span className="font-mono text-emerald-400 font-semibold">{order.filled_quantity ?? 0}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Pending Quantity</span>
                                    <span className="font-mono text-amber-400 font-semibold">{order.pending_quantity ?? 0}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Cancelled Quantity</span>
                                    <span className="font-mono text-rose-400 font-semibold">{order.cancelled_quantity ?? 0}</span>
                                  </div>
                                  <div className="flex justify-between border-t border-border-default/20 pt-1 mt-0.5">
                                    <span className="text-text-muted">Disclosed Qty</span>
                                    <span className="font-mono text-white font-semibold">{order.disclosed_quantity ?? 0}</span>
                                  </div>
                                  
                                  {/* Iceberg metadata slicing */}
                                  {order.meta?.iceberg && (
                                    <div className="border-t border-border-default/10 pt-1 space-y-0.5">
                                      <span className="text-[7px] font-bold text-amber-400 uppercase block">Iceberg Slicing Details</span>
                                      <div className="grid grid-cols-2 gap-x-2 text-[8px] text-text-secondary font-mono">
                                        <span>Leg: {order.meta.iceberg.leg} / {order.meta.iceberg.legs}</span>
                                        <span>Leg Qty: {order.meta.iceberg.leg_quantity}</span>
                                      </div>
                                    </div>
                                  )}
                                </div>

                                {/* Column 3: Order Pricing & Validity */}
                                <div className="border border-border-default/30 rounded-lg bg-muted/50 p-3 space-y-1.5">
                                  <span className="text-[8px] font-black uppercase tracking-wider text-rose-400 block border-b border-border-default/30 pb-1 mb-1">
                                    PRICING & VALIDITY
                                  </span>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Order Type</span>
                                    <span className="font-mono text-white font-semibold uppercase">{order.order_type}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Limit Price</span>
                                    <span className="font-mono text-white font-semibold">{formatCurrency(order.price)}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Trigger Price</span>
                                    <span className="font-mono text-white font-semibold">{formatCurrency(order.trigger_price)}</span>
                                  </div>
                                  <div className="flex justify-between">
                                    <span className="text-text-muted">Average Price</span>
                                    <span className="font-mono text-white font-semibold">{formatCurrency(order.average_price)}</span>
                                  </div>
                                  <div className="flex justify-between border-t border-border-default/20 pt-1 mt-0.5">
                                    <span className="text-text-muted">Validity Range</span>
                                    <span className="font-mono text-white font-semibold uppercase">
                                      {order.validity} {order.validity_ttl > 0 ? `(${order.validity_ttl}m)` : ''}
                                    </span>
                                  </div>
                                </div>
                              </div>

                              {/* Audit footer & Tag info */}
                              <div className="border-t border-border-default/25 pt-2 flex flex-col sm:flex-row sm:justify-between gap-1.5 text-[8px] text-text-muted font-mono leading-none">
                                <div className="flex flex-wrap gap-x-3 gap-y-1">
                                  <span>OMS Time: {order.order_timestamp ?? 'N/A'}</span>
                                  {order.exchange_timestamp && <span>Exchange Time: {order.exchange_timestamp}</span>}
                                  {order.exchange_update_timestamp && <span>OMS Update: {order.exchange_update_timestamp}</span>}
                                </div>
                                {order.tag && (
                                  <div className="flex items-center gap-0.5 text-emerald-400">
                                    <Tag size={9} />
                                    <span>Tag: {order.tag}</span>
                                  </div>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
                {orders.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-6 text-center text-text-muted italic font-sans">
                      No order logs registered today.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── MEMBERSHIP Visual Card ── */}
      <div className="relative overflow-hidden rounded-2xl border border-emerald-500/20 bg-gradient-to-br from-emerald-500/10 via-surface/80 to-elevated/30 p-5 shadow-lg shrink-0">
        <div className="absolute -right-16 -top-16 h-36 w-36 rounded-full bg-emerald-500/10 blur-2xl"></div>

        <div className="flex justify-between items-start">
          <div>
            <span className="text-[9px] uppercase tracking-widest text-emerald-400 font-bold">Strat AI Membership Card</span>
            <h3 className="text-base font-black text-white mt-0.5">
              {user?.tier === 'PRO' ? 'PRO TRADER EDITION' : 'STARTER FREE EDITION'}
            </h3>
          </div>
          <Shield className="text-emerald-400 shrink-0" size={20} />
        </div>

        <div className="mt-6 flex justify-between items-end">
          <div>
            <span className="text-[8px] uppercase tracking-wider text-text-secondary block">Linked Broker Connection</span>
            <span className="text-[9px] font-bold text-[#4ade80] tracking-wider uppercase">
              {broker ? `${broker.broker} • ${broker.brokerUserId}` : 'NO LIVE BROKER CONNECTED'}
            </span>
          </div>
          <div className="text-right">
            <span className="text-[8px] uppercase tracking-wider text-text-secondary block">Simulated Balance</span>
            <span className="text-xs font-black text-white">
              ₹{paperPortfolio?.balance?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) || '1,000,000.00'}
            </span>
          </div>
        </div>
      </div>

    </div>
  );
}
