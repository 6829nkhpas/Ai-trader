'use client';

import React, { useState } from 'react';
import { Cpu } from 'lucide-react';
import { SVGS } from '../chart/toolbarIcons';
import { SIDEBAR_CONFIG, type SidebarTab } from '../../types/home';
import type { TradeProfile } from '../../store/useTradeStore';
import { PROFILE_ICONS } from '../layout/NavRail';
import DeepQuantPanel from '../quant/DeepQuantPanel';
import OrderBook from '../OrderBook';
import SwingConfluencePanel from '../layouts/swing/SwingConfluencePanel';
import { MacroSentimentPanel } from '../layouts/MacroSentimentPanel';
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

  const ProfileIcon = PROFILE_ICONS[activeProfile];

  // The two confluence destinations, shared between the collapsed rail
  // (icon-only buttons) and the expanded switch (icon + label pills) — one
  // list, so the rail and the switch can never disagree about what opens what.
  const destinations: { key: SidebarTab; label: string; Icon: typeof ProfileIcon }[] = [
    { key: 'profile', label: sidebarCfg.badge, Icon: ProfileIcon },
    { key: 'deepquant', label: 'AI Agent', Icon: Cpu },
  ];

  const openOn = (tab: SidebarTab) => {
    setSidebarTab(tab);
    setSidebarOpen(true);
  };

  // ── Collapsed — a slim always-visible rail, mirroring the left NavRail's
  // collapsed look. Not a floating chevron the user has to hunt for: both
  // confluence destinations sit here as icons, and pressing either one opens
  // straight into that section instead of opening blind and re-clicking a tab.
  if (!sidebarOpen) {
    return (
      <nav
        aria-label="Confluence rail (collapsed)"
        className="flex h-full w-11 shrink-0 flex-col items-center gap-1 border-l border-border-default bg-surface py-2.5"
      >
        {destinations.map(({ key, label, Icon }) => {
          const isActive = sidebarTab === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => openOn(key)}
              title={`Open ${label}`}
              aria-label={`Open ${label} panel`}
              className={`
                relative flex h-10 w-10 shrink-0 items-center justify-center rounded-md transition-colors duration-150
                focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-500/50
                ${isActive
                  ? 'bg-emerald-500/10 text-emerald-500 dark:text-emerald-400'
                  : 'text-text-secondary hover:bg-elevated hover:text-emerald-500 dark:hover:text-emerald-400'}
              `}
            >
              <Icon size={18} strokeWidth={isActive ? 2.4 : 2} />
            </button>
          );
        })}
      </nav>
    );
  }

  return (
    <div
      className="relative flex flex-col shrink-0 min-h-0 overflow-hidden border-l border-border-default bg-surface"
      style={{ width: `${sidebarWidth}px` }}
    >
      {/* Resize Handle */}
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

      {/* Sidebar Header + Confluence switch (both destinations, one glance) */}
      <div className="flex shrink-0 flex-col border-b border-border-default bg-elevated/10 rounded-none">
        <div className="flex items-center justify-between px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-emerald-500/10 text-emerald-500 dark:text-emerald-400">
              <ProfileIcon size={13} strokeWidth={2.4} />
            </span>
            <span className="text-xs font-black uppercase tracking-wider text-text-primary">Confluence</span>
          </div>
          <button
            type="button"
            onClick={() => setSidebarOpen(false)}
            className="rounded-md p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary flex items-center justify-center"
            title="Collapse sidebar"
          >
            <span dangerouslySetInnerHTML={{ __html: SVGS.sidebarClose }} className="flex items-center justify-center" />
          </button>
        </div>

        {/* Switch — the two confluence surfaces, side by side as pill toggles */}
        <div className="flex gap-1 px-2.5 pb-2">
          {destinations.map(({ key, label, Icon }) => {
            const active = sidebarTab === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => setSidebarTab(key)}
                aria-pressed={active}
                className={`
                  flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5
                  text-[10px] font-bold uppercase tracking-wide transition-all duration-150
                  focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-500/50
                  ${active
                    ? 'bg-emerald-500/12 text-emerald-500 dark:text-emerald-400 shadow-[inset_0_0_0_1px_rgba(16,185,129,0.35)]'
                    : 'text-text-muted hover:bg-elevated/70 hover:text-text-secondary'}
                `}
              >
                <Icon size={12} strokeWidth={active ? 2.6 : 2.2} />
                {label}
              </button>
            );
          })}
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
