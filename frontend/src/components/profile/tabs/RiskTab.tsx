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
          <h2 className="text-xl font-extrabold text-text-primary tracking-tight">Margins & Risk</h2>
          <p className="text-xs text-text-secondary mt-1">Real-time Kite broker purchasing power and limits</p>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-none border border-rose-500/30 bg-rose-500/5 p-3 text-xs text-rose-400">
          <AlertCircle size={14} className="shrink-0 mt-0.5" />
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
            <div className="flex items-center gap-0 bg-muted/50 border border-border-default/20 rounded-none p-0">
              <button
                type="button"
                onClick={() => setSegment('equity')}
                className={`rounded-none px-3 py-1 text-xs font-bold uppercase tracking-wider transition-all border ${
                  segment === 'equity'
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                    : 'text-text-muted hover:text-text-secondary border-transparent'
                }`}
              >
                Equity Segment
              </button>
              <button
                type="button"
                onClick={() => setSegment('commodity')}
                className={`rounded-none px-3 py-1 text-xs font-bold uppercase tracking-wider transition-all border-y border-r border-l-0 ${
                  segment === 'commodity'
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                    : 'text-text-muted hover:text-text-secondary border-transparent'
                }`}
              >
                Commodity Segment
              </button>
            </div>

            {/* Enabled Badge */}
            {activeSegmentData && (
              <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider">
                {activeSegmentData.enabled ? (
                  <span className="flex items-center gap-1 text-emerald-400 bg-emerald-500/5 border border-emerald-500/10 px-2 py-0.5 rounded-none">
                    <CheckCircle size={10} /> Active
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-text-muted bg-surface/50 border border-border-default/50 px-2 py-0.5 rounded-none">
                    <XCircle size={10} /> Inactive
                  </span>
                )}
              </div>
            )}
          </div>

          {activeSegmentData && (
            <div className="space-y-4 flex-1 overflow-y-auto pr-1 scrollbar-none">
              {/* Giant Net Power Card */}
              <div className="border border-border-default/30 bg-muted/30 p-5 flex flex-col md:flex-row md:items-center justify-between gap-4 rounded-none">
                <div>
                  <span className="text-[9px] font-black uppercase tracking-widest text-text-secondary">Available Net Margin</span>
                  <span className="text-3xl font-black tracking-tight text-text-primary mt-1 block font-mono">
                    {formatCurrency(activeSegmentData.net)}
                  </span>
                </div>
                <div className="text-[10px] text-text-secondary font-medium md:text-right">
                  <span className="block font-semibold text-text-primary">True Purchasing Power</span>
                  <span className="text-text-muted mt-0.5 block leading-normal">
                    This net balance reflects absolute leverage power across all active open structures.
                  </span>
                </div>
              </div>

              {/* Stacked lists with border-y dividers (no side-by-side card boxes!) */}
              <div className="space-y-6">
                {/* Section 1: AVAILABLE FUNDS BREAKDOWN */}
                <div className="flex flex-col border-t border-border-default">
                  <div className="py-2 border-b border-border-default px-1 bg-elevated/30">
                    <span className="text-[10px] font-black uppercase tracking-wider text-emerald-400 block">
                      Available Limits & Cash
                    </span>
                  </div>
                  {[
                    { label: 'Opening Balance', value: formatCurrency(activeSegmentData.available?.opening_balance) },
                    { label: 'Opening Cash', value: formatCurrency(activeSegmentData.available?.cash) },
                    { 
                      label: (
                        <span className="flex items-center gap-1">
                          Collateral Margin
                          <span className="cursor-help" title="Value of securities/holdings pledged for margin trading">
                            <HelpCircle size={10} className="text-text-muted" />
                          </span>
                        </span>
                      ), 
                      value: formatCurrency(activeSegmentData.available?.collateral) 
                    },
                    { label: 'Intraday Payin', value: formatCurrency(activeSegmentData.available?.intraday_payin) },
                    { label: 'Adhoc Margin', value: formatCurrency(activeSegmentData.available?.adhoc_margin) },
                    { label: 'Live Available Balance', value: <span className="font-extrabold text-emerald-400">{formatCurrency(activeSegmentData.available?.live_balance)}</span> }
                  ].map((row, i) => (
                    <div key={i} className="flex items-center justify-between py-2.5 border-b border-border-default px-1 text-xs">
                      <span className="text-text-secondary font-medium">{row.label}</span>
                      <span className="font-mono text-text-primary font-semibold">{row.value}</span>
                    </div>
                  ))}
                </div>

                {/* Section 2: UTILIZED MARGIN DEBITS */}
                <div className="flex flex-col border-t border-border-default">
                  <div className="py-2 border-b border-border-default px-1 bg-elevated/30">
                    <span className="text-[10px] font-black uppercase tracking-wider text-rose-400 block">
                      Utilised Margin Debits
                    </span>
                  </div>
                  {[
                    { label: 'SPAN Margin', value: formatCurrency(activeSegmentData.utilised?.span) },
                    { label: 'Exposure Margin', value: formatCurrency(activeSegmentData.utilised?.exposure) },
                    { 
                      label: (
                        <span className="flex items-center gap-1">
                          Realised M2M
                          <span className="cursor-help" title="Booked profits or losses from intraday trades">
                            <HelpCircle size={10} className="text-text-muted" />
                          </span>
                        </span>
                      ), 
                      value: <span className={getPnlClass(activeSegmentData.utilised?.m2m_realised || 0)}>{formatCurrency(activeSegmentData.utilised?.m2m_realised)}</span> 
                    },
                    { 
                      label: (
                        <span className="flex items-center gap-1">
                          Unrealised M2M
                          <span className="cursor-help" title="Running float profit or loss of active structures">
                            <HelpCircle size={10} className="text-text-muted" />
                          </span>
                        </span>
                      ), 
                      value: <span className={getPnlClass(activeSegmentData.utilised?.m2m_unrealised || 0)}>{formatCurrency(activeSegmentData.utilised?.m2m_unrealised)}</span> 
                    },
                    { label: 'Option Premium', value: formatCurrency(activeSegmentData.utilised?.option_premium) },
                    { label: 'Holding Sales', value: formatCurrency(activeSegmentData.utilised?.holding_sales) },
                    { label: 'Payout P&D', value: formatCurrency(activeSegmentData.utilised?.payout) },
                    { label: 'Delivery Margin', value: formatCurrency(activeSegmentData.utilised?.delivery) },
                    { label: 'Liquid Collateral', value: formatCurrency(activeSegmentData.utilised?.liquid_collateral) },
                    { label: 'Stock Collateral', value: formatCurrency(activeSegmentData.utilised?.stock_collateral) },
                    { label: 'Total Margin Utilised (Debits)', value: <span className="font-extrabold text-rose-400">{formatCurrency(activeSegmentData.utilised?.debits)}</span> }
                  ].map((row, i) => (
                    <div key={i} className="flex items-center justify-between py-2.5 border-b border-border-default px-1 text-xs">
                      <span className="text-text-secondary font-medium">{row.label}</span>
                      <span className="font-mono text-text-primary font-semibold">{row.value}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
