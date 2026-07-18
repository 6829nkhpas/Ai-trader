'use client';

import React from 'react';
import MainTerminalChart from '../MainTerminalChart';
import type { Timeframe } from '../../utils/chartTypes';
import { TradeProfile } from '../../store/useTradeStore';

interface SwingLayoutProps { activeProfile?: TradeProfile; timeframe?: string; isExpanded?: boolean; onToggleExpand?: () => void; }

export { default as SwingConfluencePanel } from './swing/SwingConfluencePanel';

// ── Layout ──────────────────────────────────────────────────────────────

export default function SwingLayout({ activeProfile = 'SWING', timeframe = '1h', isExpanded = false, onToggleExpand }: SwingLayoutProps) {
  return (
    <div id="swing-hud" className="flex h-full flex-col min-h-0 rounded-none border-none bg-surface overflow-hidden">
      <MainTerminalChart
        activeProfile={activeProfile}
        timeframe={timeframe as Timeframe}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    </div>
  );
}
