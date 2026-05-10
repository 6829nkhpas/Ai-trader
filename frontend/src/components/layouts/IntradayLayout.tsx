'use client';

import React from 'react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import type { Timeframe } from '../AlphaPredictiveChart';
import { TradeProfile } from '../../store/useTradeStore';

interface IntradayLayoutProps {
  activeProfile?: TradeProfile;
  timeframe?: string;
  isExpanded?: boolean;
  onToggleExpand?: () => void;
}

export default function IntradayLayout({ activeProfile = 'INTRADAY', timeframe = '1m', isExpanded = false, onToggleExpand }: IntradayLayoutProps) {
  return (
    <div id="intraday-hud" className="flex h-full flex-col min-h-0 rounded-lg border border-border-default bg-surface overflow-hidden">
      <AlphaPredictiveChart
        activeProfile={activeProfile}
        timeframe={timeframe as Timeframe}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    </div>
  );
}
