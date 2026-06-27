import React, { useState } from 'react';
import { Shield, RefreshCw, AlertCircle, HelpCircle, CheckCircle, XCircle } from 'lucide-react';

interface RiskTabProps {
  marginsData: any;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export default function RiskTab({ marginsData, loading, error, refetch }: RiskTabProps) {
  const [segment, setSegment] = useState<'equity' | 'commodity'>('equity');

  const formatCurrency = (val: number | undefined) => {
    if (val === undefined) return '₹0.00';
    return `₹${val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  const getPnlClass = (val: number) => {
    if (val > 0) return 'text-bull font-bold';
    if (val < 0) return 'text-[#ef4444] font-bold';
    return 'text-text-secondary';
  };

  const activeSegmentData = marginsData?.[segment];

  return (
    <div className="space-y-5 flex flex-col h-full">
      {/* Title Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-extrabold text-white tracking-tight">Margins & Risk</h2>
          <p className="text-xs text-text-secondary mt-1">Real-time Kite broker purchasing power and limits</p>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-400">
          <AlertCircle size={14} />
          <span>{error}</span>
        </div>
      )}

      {loading && !marginsData && (
        <div className="flex h-24 items-center justify-center text-xs text-text-muted">
          <RefreshCw size={14} className="animate-spin mr-2 text-emerald-400" />
          Loading margins...
        </div>
      )}

      {marginsData && (
        <div className="flex flex-col h-full min-h-0 space-y-4">
          {/* Symmetrical Segment Switcher Tabs */}
          <div className="flex items-center justify-between border-b border-border-default/40 pb-2">
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setSegment('equity')}
                className={`rounded px-3 py-1 text-xs font-bold uppercase tracking-wider transition-all ${
                  segment === 'equity'
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                    : 'text-text-muted hover:text-text-secondary border border-transparent'
                }`}
              >
                Equity Segment
              </button>
              <button
                type="button"
                onClick={() => setSegment('commodity')}
                className={`rounded px-3 py-1 text-xs font-bold uppercase tracking-wider transition-all ${
                  segment === 'commodity'
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                    : 'text-text-muted hover:text-text-secondary border border-transparent'
                }`}
              >
                Commodity Segment
              </button>
            </div>

            {/* Enabled Badge */}
            {activeSegmentData && (
              <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider">
                {activeSegmentData.enabled ? (
                  <span className="flex items-center gap-1 text-emerald-400 bg-emerald-500/5 border border-emerald-500/10 px-2 py-0.5 rounded-full">
                    <CheckCircle size={10} /> Active
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-text-muted bg-surface/50 border border-border-default/50 px-2 py-0.5 rounded-full">
                    <XCircle size={10} /> Inactive
                  </span>
                )}
              </div>
            )}
          </div>

          {activeSegmentData && (
            <div className="space-y-4 flex-1 overflow-y-auto pr-1 scrollbar-none">
              {/* Giant Net Power Card */}
              <div className="border border-border-default/40 rounded-xl bg-surface/40 p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                  <span className="text-[9px] font-black uppercase tracking-widest text-text-secondary">Available Net Margin</span>
                  <span className="text-3xl font-black tracking-tight text-white mt-1 block font-mono">
                    {formatCurrency(activeSegmentData.net)}
                  </span>
                </div>
                <div className="text-[10px] text-text-secondary font-medium md:text-right">
                  <span className="block font-semibold text-white">True Purchasing Power</span>
                  <span className="text-text-muted mt-0.5 block leading-normal">
                    This net balance reflects absolute leverage power across all active open structures.
                  </span>
                </div>
              </div>

              {/* Two Column Grid displaying ALL data points */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {/* COLUMN 1: AVAILABLE FUNDS BREAKDOWN */}
                <div className="border border-border-default/40 rounded-xl bg-surface/40 p-4">
                  <span className="text-[10px] font-black uppercase tracking-wider text-emerald-400 block mb-3 border-b border-border-default/40 pb-1.5">
                    AVAILABLE LIMITS & CASH
                  </span>
                  <div className="space-y-2.5 text-xs">
                    <div className="flex items-center justify-between pb-1">
                      <span className="text-text-secondary font-medium">Opening Balance</span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(activeSegmentData.available?.opening_balance)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between pb-1">
                      <span className="text-text-secondary font-medium">Opening Cash</span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(activeSegmentData.available?.cash)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between pb-1">
                      <span className="text-text-secondary font-medium flex items-center gap-1">
                        Collateral Margin
                        <span className="cursor-help" title="Value of securities/holdings pledged for margin trading">
                          <HelpCircle size={10} className="text-text-muted" />
                        </span>
                      </span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(activeSegmentData.available?.collateral)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between pb-1">
                      <span className="text-text-secondary font-medium">Intraday Payin</span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(activeSegmentData.available?.intraday_payin)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between pb-1">
                      <span className="text-text-secondary font-medium">Adhoc Margin</span>
                      <span className="font-mono text-white font-semibold">
                        {formatCurrency(activeSegmentData.available?.adhoc_margin)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between border-t border-border-default/30 pt-2 pb-0.5">
                      <span className="text-text-primary font-bold">Live Available Balance</span>
                      <span className="font-mono text-emerald-400 font-extrabold">
                        {formatCurrency(activeSegmentData.available?.live_balance)}
                      </span>
                    </div>
                  </div>
                </div>

                {/* COLUMN 2: UTILISHED MARGINS BREAKDOWN */}
                <div className="border border-border-default/40 rounded-xl bg-surface/40 p-4">
                  <span className="text-[10px] font-black uppercase tracking-wider text-rose-400 block mb-3 border-b border-border-default/40 pb-1.5">
                    UTILISHED MARGIN DEBITS
                  </span>
                  <div className="space-y-2 text-xs">
                    <div className="grid grid-cols-2 gap-x-6 gap-y-2">
                      <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5">
                        <span className="text-text-secondary font-medium">SPAN Margin</span>
                        <span className="font-mono text-white font-semibold">
                          {formatCurrency(activeSegmentData.utilised?.span)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5">
                        <span className="text-text-secondary font-medium">Exposure Margin</span>
                        <span className="font-mono text-white font-semibold">
                          {formatCurrency(activeSegmentData.utilised?.exposure)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5">
                        <span className="text-text-secondary font-medium flex items-center gap-1">
                          Realised M2M
                          <span className="cursor-help" title="Booked profits or losses from intraday trades">
                            <HelpCircle size={10} className="text-text-muted" />
                          </span>
                        </span>
                        <span className={`font-mono ${getPnlClass(activeSegmentData.utilised?.m2m_realised || 0)}`}>
                          {formatCurrency(activeSegmentData.utilised?.m2m_realised)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5">
                        <span className="text-text-secondary font-medium flex items-center gap-1">
                          Unrealised M2M
                          <span className="cursor-help" title="Running float profit or loss of active structures">
                            <HelpCircle size={10} className="text-text-muted" />
                          </span>
                        </span>
                        <span className={`font-mono ${getPnlClass(activeSegmentData.utilised?.m2m_unrealised || 0)}`}>
                          {formatCurrency(activeSegmentData.utilised?.m2m_unrealised)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5">
                        <span className="text-text-secondary font-medium">Option Premium</span>
                        <span className="font-mono text-white font-semibold">
                          {formatCurrency(activeSegmentData.utilised?.option_premium)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5">
                        <span className="text-text-secondary font-medium">Holding Sales</span>
                        <span className="font-mono text-white font-semibold">
                          {formatCurrency(activeSegmentData.utilised?.holding_sales)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5">
                        <span className="text-text-secondary font-medium">Payout P&D</span>
                        <span className="font-mono text-white font-semibold">
                          {formatCurrency(activeSegmentData.utilised?.payout)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5">
                        <span className="text-text-secondary font-medium">Delivery Margin</span>
                        <span className="font-mono text-white font-semibold">
                          {formatCurrency(activeSegmentData.utilised?.delivery)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5">
                        <span className="text-text-secondary font-medium">Liquid Collateral</span>
                        <span className="font-mono text-white font-semibold">
                          {formatCurrency(activeSegmentData.utilised?.liquid_collateral)}
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5">
                        <span className="text-text-secondary font-medium">Stock Collateral</span>
                        <span className="font-mono text-white font-semibold">
                          {formatCurrency(activeSegmentData.utilised?.stock_collateral)}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center justify-between border-t border-border-default/30 pt-2 pb-0.5">
                      <span className="text-text-primary font-bold">Total Margin Utilised (Debits)</span>
                      <span className="font-mono text-rose-400 font-extrabold">
                        {formatCurrency(activeSegmentData.utilised?.debits)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
