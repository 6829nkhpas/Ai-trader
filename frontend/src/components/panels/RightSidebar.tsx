'use client';

import React, { useState } from 'react';
import { SVGS } from '../chart/toolbarIcons';
import { SIDEBAR_CONFIG, type SidebarTab } from '../../types/home';
import type { TradeProfile } from '../../store/useTradeStore';
import DeepQuantPanel from '../quant/DeepQuantPanel';
import OrderBook from '../OrderBook';
import { SwingConfluencePanel } from '../layouts/SwingLayout';
import { MacroSentimentPanel } from '../layouts/InvestorLayout';
import FnoSidebarPanel from '../fno/FnoSidebarPanel';

interface RightSidebarProps {
  activeProfile: TradeProfile;
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  sidebarWidth: number;
  isResizingSidebar: boolean;
  startResizingSidebar: (e: React.MouseEvent) => void;
}

/** Collapsible right sidebar with profile-driven + Deep Quant tabs. */
const RightSidebar: React.FC<RightSidebarProps> = ({
  activeProfile,
  sidebarOpen,
  setSidebarOpen,
  sidebarWidth,
  isResizingSidebar,
  startResizingSidebar,
}) => {
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>('profile');
  const sidebarCfg = SIDEBAR_CONFIG[activeProfile];

  const renderSidebarContent = () => {
    if (sidebarTab === 'deepquant') return <DeepQuantPanel />;
    switch (activeProfile) {
      case 'INTRADAY': return <OrderBook />;
      case 'SWING': return <SwingConfluencePanel />;
      case 'INVESTOR': return <MacroSentimentPanel />;
      case 'FNO': return <FnoSidebarPanel />;
      default: return null;
    }
  };

  const sidebarTitle = sidebarTab === 'deepquant' ? 'Deep Quant' : sidebarCfg.label;

  return (
    <div
      className={`
        relative flex flex-col shrink-0 min-h-0 overflow-hidden border-l border-border-default bg-surface
        ${isResizingSidebar ? '' : 'transition-all duration-300 ease-out'}
        ${sidebarOpen
          ? 'opacity-100'
          : 'w-0 min-w-0 max-w-0 opacity-0 pointer-events-none border-l-0'
        }
      `}
      style={{ width: sidebarOpen ? `${sidebarWidth}px` : '0px' }}
    >
      {/* Resize Handle */}
      {sidebarOpen && (
        <div
          onMouseDown={startResizingSidebar}
          className={`
            absolute top-0 bottom-0 -left-1.5 w-3 cursor-col-resize z-20 hover:bg-emerald-500/10 transition-colors duration-150 rounded-none
            flex items-center justify-center group
            ${isResizingSidebar ? 'bg-emerald-500/20' : 'bg-transparent'}
          `}
          title="Drag to resize panel"
        >
          <div className={`
            w-0.5 h-6 bg-border-default rounded-[1px] group-hover:bg-emerald-400 transition-colors
            ${isResizingSidebar ? 'bg-emerald-400' : ''}
          `} />
        </div>
      )}

      {/* Sidebar Header with Tab Switcher */}
      <div className="flex shrink-0 flex-col border-b border-border-default bg-surface rounded-none">
        <div className="flex items-center justify-between px-3 py-1.5">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-text-primary tracking-wide">{sidebarTitle}</span>
          </div>
          <button
            type="button"
            onClick={() => setSidebarOpen(false)}
            className="rounded-none p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary flex items-center justify-center"
            title="Collapse sidebar"
          >
            <span dangerouslySetInnerHTML={{ __html: SVGS.sidebarClose }} className="flex items-center justify-center" />
          </button>
        </div>

        {/* Tab row */}
        <div className="flex border-t border-border-default bg-muted/20">
          {([
            { key: 'profile' as SidebarTab, label: sidebarCfg.badge },
            { key: 'deepquant' as SidebarTab, label: 'AI Agent' },
          ]).map(({ key, label }, idx) => (
            <button
              key={key}
              type="button"
              onClick={() => setSidebarTab(key)}
              className={`flex-1 rounded-none px-1.5 py-2 text-[9px] font-bold uppercase tracking-wider transition-all duration-200 ${
                idx > 0 ? 'border-l border-border-default' : ''
              } ${
                sidebarTab === key
                  ? 'bg-surface text-text-primary border-b-2 border-emerald-500 dark:border-emerald-400'
                  : 'text-text-muted hover:text-text-secondary bg-transparent hover:bg-muted/10'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Sidebar Content */}
      <div className="flex-1 min-h-0 w-full max-w-full overflow-x-hidden overflow-y-auto scrollbar-none bg-surface rounded-none">
        {renderSidebarContent()}
      </div>
    </div>
  );
};

export default RightSidebar;
