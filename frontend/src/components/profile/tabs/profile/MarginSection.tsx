'use client';

import React, { useState } from 'react';
import { Wallet, HelpCircle, CheckCircle, XCircle } from 'lucide-react';

interface MarginSectionProps {
  broker: any;
  marginsData: any;
  formatCurrency: (val: number | undefined) => string;
  getPnlClass: (val: number) => string;
}

export default function MarginSection({
  broker,
  marginsData,
  formatCurrency,
  getPnlClass,
}: MarginSectionProps) {
  const [marginSegment, setMarginSegment] = useState<'equity' | 'commodity'>('equity');
  
  if (!broker || !marginsData) return null;

  const activeSegmentData = marginsData[marginSegment];

  return (
    <div className="border border-border-default/40 bg-surface/30 p-4 space-y-4 rounded-none">
      <div className="flex items-center justify-between border-b border-border-default/20 pb-2">
        <div className="flex items-center gap-2">
          <Wallet size={14} className="text-emerald-400" />
          <h3 className="text-xs font-black uppercase tracking-wider text-text-primary">Live Segment Funds & Limits</h3>
        </div>
        
        <div className="flex items-center gap-3">
          {/* Segment Toggles */}
          <div className="flex items-center gap-0 bg-muted/50 border border-border-default/20 rounded-none p-0">
            <button
              type="button"
              onClick={() => setMarginSegment('equity')}
              className={`rounded-none px-2.5 py-0.5 text-[9px] font-black uppercase tracking-wider transition-all border ${
                marginSegment === 'equity'
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'text-text-muted hover:text-text-secondary border-transparent'
              }`}
            >
              Equity
            </button>
            <button
              type="button"
              onClick={() => setMarginSegment('commodity')}
              className={`rounded-none px-2.5 py-0.5 text-[9px] font-black uppercase tracking-wider transition-all border-y border-r border-l-0 ${
                marginSegment === 'commodity'
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                  : 'text-text-muted hover:text-text-secondary border-transparent'
              }`}
            >
              Commodity
            </button>
          </div>

          {/* Active Badge */}
          {activeSegmentData && (
            <div className="text-[9px] font-bold uppercase tracking-wider">
              {activeSegmentData.enabled ? (
                <span className="flex items-center gap-1 text-emerald-400 bg-emerald-500/5 border border-emerald-500/10 px-2 py-0.5 rounded-none">
                  <CheckCircle size={9} /> Active
                </span>
              ) : (
                <span className="flex items-center gap-1 text-text-muted bg-surface/50 border border-border-default/50 px-2 py-0.5 rounded-none">
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
          <div className="border border-border-default/30 bg-muted/30 p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4 rounded-none">
            <div>
              <span className="text-[9px] font-bold uppercase tracking-wider text-text-secondary">Available Net Margin</span>
              <span className="text-2xl font-black tracking-tight text-text-primary mt-0.5 block font-mono">
                {formatCurrency(activeSegmentData.net)}
              </span>
            </div>
            <div className="text-[9px] text-text-secondary font-medium sm:text-right">
              <span className="block font-bold text-text-primary">True Segment Purchasing Power</span>
              <span className="text-text-muted mt-0.5 block leading-normal">
                This net balance reflects absolute leverage power for the {marginSegment} segment.
              </span>
            </div>
          </div>

          {/* Stacked lists with border-y dividers */}
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
                    <span className="flex items-center gap-1 cursor-help" title="Value of securities/holdings pledged for margin trading">
                      Collateral Margin
                      <HelpCircle size={9} className="text-text-muted" />
                    </span>
                  ), 
                  value: formatCurrency(activeSegmentData.available?.collateral) 
                },
                { label: 'Intraday Payin', value: formatCurrency(activeSegmentData.available?.intraday_payin) },
                { label: 'Adhoc Margin', value: formatCurrency(activeSegmentData.available?.adhoc_margin) },
                { label: 'Live Available Balance', value: <span className="font-extrabold text-emerald-400">{formatCurrency(activeSegmentData.available?.live_balance)}</span> }
              ].map((row, i) => (
                <div key={i} className="flex items-center justify-between py-2 border-b border-border-default px-1 text-xs">
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
                    <span className="flex items-center gap-1 cursor-help" title="Booked profits or losses from intraday trades">
                      Realised M2M
                      <HelpCircle size={8} className="text-text-muted" />
                    </span>
                  ), 
                  value: <span className={getPnlClass(activeSegmentData.utilised?.m2m_realised || 0)}>{formatCurrency(activeSegmentData.utilised?.m2m_realised)}</span> 
                },
                { 
                  label: (
                    <span className="flex items-center gap-1 cursor-help" title="Running float profit or loss of active structures">
                      Unrealised M2M
                      <HelpCircle size={8} className="text-text-muted" />
                    </span>
                  ), 
                  value: <span className={getPnlClass(activeSegmentData.utilised?.m2m_unrealised || 0)}>{formatCurrency(activeSegmentData.utilised?.m2m_unrealised)}</span> 
                },
                { label: 'Option Premium', value: formatCurrency(activeSegmentData.utilised?.option_premium) },
                { label: 'Holding Sales', value: formatCurrency(activeSegmentData.utilised?.holding_sales) },
                { label: 'Payout P&D', value: formatCurrency(activeSegmentData.utilised?.payout) },
                { label: 'Delivery Margin', value: formatCurrency(activeSegmentData.utilised?.delivery) },
                { label: 'Total Margin Utilised (Debits)', value: <span className="font-extrabold text-rose-400">{formatCurrency(activeSegmentData.utilised?.debits)}</span> }
              ].map((row, i) => (
                <div key={i} className="flex items-center justify-between py-2 border-b border-border-default px-1 text-xs">
                  <span className="text-text-secondary font-medium">{row.label}</span>
                  <span className="font-mono text-text-primary font-semibold">{row.value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <p className="text-xs text-text-muted italic py-2">No active segment margins found for {marginSegment}.</p>
      )}
    </div>
  );
}
