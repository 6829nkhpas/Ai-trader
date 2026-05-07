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
