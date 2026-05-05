'use client';

import React from 'react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import OrderBook from '../OrderBook';
import { TradeProfile } from '../../store/useTradeStore';

interface IntradayLayoutProps {
  activeProfile?: TradeProfile;
}

export default function IntradayLayout({ activeProfile = 'INTRADAY' }: IntradayLayoutProps) {
  return (
    <div id="intraday-hud" className="grid h-full grid-cols-12 gap-3 p-3">
      {/* ── Primary Chart Area ──────────────────────────────── */}
      <div className="col-span-9 flex flex-col gap-3 min-h-0">
        {/* Chart Header Bar */}
        <div className="flex shrink-0 items-center justify-between rounded-lg border border-border-default bg-surface px-4 py-2">
          <div className="flex items-center gap-2.5">
            <h2 className="text-sm font-semibold text-text-primary tracking-wide">
              V2 Predictive Engine
            </h2>
            <span className="rounded bg-[#ECFDF5] px-1.5 py-px text-[9px] font-bold text-[#059669] uppercase tracking-widest">
              10m OHLC
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-md border border-emerald-300 bg-[#ECFDF5] px-2 py-0.5 text-[10px] font-bold text-[#059669] uppercase tracking-widest">
              Intraday Scalp
            </span>
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
            </span>
          </div>
        </div>

        {/* Chart Canvas */}
        <div className="flex-1 min-h-0 rounded-lg border border-border-default bg-surface overflow-hidden">
          <AlphaPredictiveChart activeProfile={activeProfile} />
        </div>
      </div>

      {/* ── Order Book Sidebar ──────────────────────────────── */}
      <div className="col-span-3 min-h-0">
        <OrderBook />
      </div>
    </div>
  );
}
