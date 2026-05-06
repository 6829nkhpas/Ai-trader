'use client';

import React, { useState } from 'react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import type { Timeframe } from '../AlphaPredictiveChart';
import OrderBook from '../OrderBook';
import { TradeProfile } from '../../store/useTradeStore';

interface IntradayLayoutProps {
  activeProfile?: TradeProfile;
  timeframe?: string;
}

export default function IntradayLayout({ activeProfile = 'INTRADAY', timeframe = '1m' }: IntradayLayoutProps) {
  const [isChartExpanded, setIsChartExpanded] = useState(false);

  return (
    <div id="intraday-hud" className="grid h-full grid-cols-12 gap-3 p-3">
      {/* ── Primary Chart Area ──────────────────────────────── */}
      <div className={`flex flex-col gap-3 min-h-0 transition-all duration-300 ${isChartExpanded ? 'col-span-12' : 'col-span-9'}`}>
        {/* Chart Header Bar */}
        <div className="flex shrink-0 items-center justify-between rounded-lg border border-border-default bg-surface px-4 py-2">
          <div className="flex items-center gap-2.5">
            <h2 className="text-sm font-semibold text-text-primary tracking-wide">
              V2 Predictive Engine
            </h2>
            <span className="rounded bg-emerald-500/10 px-1.5 py-px text-[9px] font-bold text-emerald-400 uppercase tracking-widest">
              {timeframe} OHLC
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold text-emerald-400 uppercase tracking-widest">
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
          <AlphaPredictiveChart
            activeProfile={activeProfile}
            timeframe={timeframe as Timeframe}
            isExpanded={isChartExpanded}
            onToggleExpand={() => setIsChartExpanded((prev) => !prev)}
          />
        </div>
      </div>

      {/* ── Order Book Sidebar (hidden when chart is expanded) ──── */}
      {!isChartExpanded && (
        <div className="col-span-3 min-h-0">
          <OrderBook />
        </div>
      )}
    </div>
  );
}
