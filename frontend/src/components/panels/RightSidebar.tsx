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

  // ── Collapsed — a slim always-visible rail. Not a floating chevron the user
  // has to hunt for: both confluence destinations sit here as icons, and pressing
  // either one opens straight into that section instead of opening blind and
  // re-clicking a tab.
  //
  // Styled to match `NavRail`'s collapsed state exactly, because the two rails
  // bracket the same screen and read as one component. The active destination
  // used to be a filled `bg-emerald-500/10` rounded tile and hover painted a
  // `bg-elevated` box — which made a 40px app-icon tile out of a 22px glyph and
  // looked nothing like the left edge. Same treatment as NavRail now: no
  // background in any state, colour alone carries hover, and the active
  // destination is marked by a thin accent bar on the rail's OUTER edge — the
  // mirror of NavRail's, which sits on its own outer edge.
  if (!sidebarOpen) {
    return (
      <nav
        aria-label="Confluence rail (collapsed)"
        className="flex h-full w-11 shrink-0 flex-col border-l border-border-default bg-surface py-2.5"
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
                relative flex h-11 w-full shrink-0 cursor-pointer items-center justify-center
                rounded-none transition-colors duration-150
                focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-500/50
                ${isActive
                  ? 'text-emerald-500 dark:text-emerald-400'
                  : 'text-text-secondary hover:text-emerald-500 dark:hover:text-emerald-400'}
              `}
            >
              {/* Active accent bar (thin, non-boxy) — mirrors NavRail's */}
              <span
                className={`absolute right-0 top-1/2 h-6 w-0.5 -translate-y-1/2 rounded-l-full bg-emerald-500 transition-opacity duration-150 ${
                  isActive ? 'opacity-100' : 'opacity-0'
                }`}
              />
              <Icon size={22} strokeWidth={isActive ? 2.4 : 2} />
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
            {/* Bare glyph, no tinted tile. The left panel's headers colour their
                icon and stop there; a filled swatch here was the only one of its
                kind on screen. */}
            <ProfileIcon
              size={14}
              strokeWidth={2.4}
              className="shrink-0 text-emerald-500 dark:text-emerald-400"
            />
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

        {/* Switch — the two confluence surfaces, side by side.
            Flat tabs with an inset underline, the same treatment
            `left-panel/AnalysisSheet` gives its tab bar. They were green pills
            with a tinted fill and a ring, which put a third shade of emerald in
            a header that already had two. */}
        <div className="flex items-stretch">
          {destinations.map(({ key, label, Icon }) => {
            const active = sidebarTab === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => setSidebarTab(key)}
                aria-pressed={active}
                className={`
                  flex flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-none px-2 py-2
                  text-[10px] font-bold uppercase tracking-wide transition-colors duration-150
                  focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-emerald-500/50
                  ${active
                    ? 'text-text-primary shadow-[inset_0_-2px_0_0_var(--color-primary)]'
                    : 'text-text-muted hover:text-text-secondary'}
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
