'use client';

import React from 'react';
import { Group, Panel, Separator, usePanelRef, PanelImperativeHandle } from 'react-resizable-panels';
import { PanelRightClose, PanelRightOpen } from 'lucide-react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import type { Timeframe } from '../AlphaPredictiveChart';
import OrderBook from '../OrderBook';
import { TradeProfile } from '../../store/useTradeStore';

interface IntradayLayoutProps {
  activeProfile?: TradeProfile;
  timeframe?: string;
}

export default function IntradayLayout({ activeProfile = 'INTRADAY', timeframe = '1m' }: IntradayLayoutProps) {
  const sidebarRef = usePanelRef();
  const [isCollapsed, setIsCollapsed] = React.useState(false);

  const handleToggleSidebar = () => {
    const panel = sidebarRef.current;
    if (!panel) return;
    if (panel.isCollapsed()) { panel.expand(); setIsCollapsed(false); }
    else { panel.collapse(); setIsCollapsed(true); }
  };

  const handleResize = () => {
    const panel = sidebarRef.current;
    if (panel) setIsCollapsed(panel.isCollapsed());
  };

  return (
    <div id="intraday-hud" className="h-full p-3">
      <Group orientation="horizontal">
        {/* ── Primary Chart Panel ──────────────────────────────── */}
        <Panel defaultSize={75} minSize={40}>
          <div className="flex h-full flex-col min-h-0 rounded-lg border border-border-default bg-surface overflow-hidden">
            <AlphaPredictiveChart
              activeProfile={activeProfile}
              timeframe={timeframe as Timeframe}
              isExpanded={isCollapsed}
              onToggleExpand={handleToggleSidebar}
            />
          </div>
        </Panel>

        {/* ── Resize Handle ────────────────────────────────────── */}
        <Separator className="group flex w-2 items-center justify-center cursor-col-resize">
          <div className="h-full w-[3px] rounded-full bg-slate-800 transition-colors duration-150 group-hover:bg-slate-600 group-active:bg-emerald-500/60" />
        </Separator>

        {/* ── Order Book Sidebar ────────────────────────────────── */}
        <Panel
          panelRef={sidebarRef}
          defaultSize={25}
          minSize={15}
          collapsible
          collapsedSize={0}
          onResize={handleResize}
        >
          <div className="flex h-full flex-col min-h-0">
            {/* Collapse toggle */}
            <div className="flex shrink-0 items-center justify-end px-2 py-1">
              <button
                type="button"
                onClick={handleToggleSidebar}
                className="rounded p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
                title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              >
                {isCollapsed ? <PanelRightOpen size={14} /> : <PanelRightClose size={14} />}
              </button>
            </div>
            <div className="flex-1 min-h-0">
              <OrderBook />
            </div>
          </div>
        </Panel>
      </Group>
    </div>
  );
}
