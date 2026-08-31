'use client';

import React, { useState } from 'react';
import type { IconType } from 'react-icons';
import { RiBrainAi3Line } from 'react-icons/ri';
import { MdLibraryBooks } from 'react-icons/md';
import { SIDEBAR_CONFIG, type SidebarTab } from '../../types/home';
import type { TradeProfile } from '../../store/useTradeStore';
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

/**
 * The right-hand workspace: a persistent icon rail, and ONE full-height panel.
 *
 * The rail is always on screen and is the only switcher. The panel it opens gets
 * the entire column height, because there is nothing above it — no title strip
 * and, more to the point, no tab bar.
 *
 * It used to work the other way round: the rail appeared only while the sidebar
 * was CLOSED, and once open a two-row header took over — a title row, then an
 * `INTRADAY | AI AGENT` tab bar. So the switcher existed twice in two different
 * shapes, and both of the things it switched between were permanently ~62px
 * shorter than the column they sat in. The panels are self-identifying (the order
 * book opens on its Price/Size/Total head, Deep Quant on its FIND QUANT TRADE
 * control, F&O on its underlying and expiry selectors), so the header was
 * spending that height on a label the content already carried.
 */
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

  // The two destinations. `label` stays per-profile (INTRADAY / SWING /
  // INVESTOR / F&O) so the tooltip still says which workspace the left one opens,
  // even though the glyph is the same for all four.
  const destinations: { key: SidebarTab; label: string; Icon: IconType }[] = [
    { key: 'profile', label: sidebarCfg.badge, Icon: MdLibraryBooks },
    { key: 'deepquant', label: 'AI Agent', Icon: RiBrainAi3Line },
  ];

  /**
   * Pressing a rail icon: open it, switch to it, or close it.
   *
   * Pressing the destination already on screen closes the column — the rail is
   * the only control now, so it has to be able to put the width back.
   */
  const activate = (tab: SidebarTab) => {
    if (sidebarOpen && sidebarTab === tab) {
      setSidebarOpen(false);
      return;
    }
    setSidebarTab(tab);
    setSidebarOpen(true);
  };

  return (
    <div className="flex h-full shrink-0">
      {/* ── The open panel, to the LEFT of the rail ───────────────────────
          Only one is ever mounted: `renderSidebarContent` returns the AI agent
          or the active workspace's panel, never both. */}
      {sidebarOpen && (
        <div
          className="relative flex min-h-0 shrink-0 flex-col overflow-hidden border-l border-border-default bg-surface"
          style={{ width: `${sidebarWidth}px` }}
        >
          {/* Resize Handle */}
          <div
            onMouseDown={startResizingSidebar}
            className={`
              absolute top-0 bottom-0 -left-1.5 z-20 flex w-3 cursor-col-resize items-center justify-center
              rounded-none transition-colors duration-150 group hover:bg-emerald-500/10
              ${isResizingSidebar ? 'bg-emerald-500/20' : 'bg-transparent'}
            `}
            title="Drag to resize panel"
          >
            <div className={`
              h-6 w-0.5 rounded-[1px] bg-border-default transition-colors group-hover:bg-emerald-400
              ${isResizingSidebar ? 'bg-emerald-400' : ''}
            `} />
          </div>

          {/* Full height — no header above it. */}
          <div className="min-h-0 w-full max-w-full flex-1 overflow-y-auto overflow-x-hidden scrollbar-none rounded-none bg-surface">
            {renderSidebarContent()}
          </div>
        </div>
      )}

      {/* ── The rail — always visible, hard against the right edge ───────
          Styled to match `NavRail`'s collapsed state: no background behind a
          glyph in any state, colour alone for hover, and the destination
          currently on screen marked by a thin accent bar on the rail's OUTER
          edge (the mirror of NavRail's, which sits on its own outer edge). */}
      <nav
        aria-label="Confluence rail"
        className="flex h-full w-11 shrink-0 flex-col border-l border-border-default bg-surface py-2.5"
      >
        {destinations.map(({ key, label, Icon }) => {
          // What is ON SCREEN, not merely what was last picked: with the column
          // closed nothing is showing, so nothing is marked.
          const isShowing = sidebarOpen && sidebarTab === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => activate(key)}
              title={isShowing ? `Hide ${label}` : `Show ${label}`}
              aria-label={isShowing ? `Hide ${label} panel` : `Show ${label} panel`}
              aria-pressed={isShowing}
              className={`
                relative flex h-11 w-full shrink-0 cursor-pointer items-center justify-center
                rounded-none transition-colors duration-150
                focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-500/50
                ${isShowing
                  ? 'text-emerald-500 dark:text-emerald-400'
                  : 'text-text-secondary hover:text-emerald-500 dark:hover:text-emerald-400'}
              `}
            >
              {/* Active accent bar (thin, non-boxy) — mirrors NavRail's */}
              <span
                className={`absolute right-0 top-1/2 h-6 w-0.5 -translate-y-1/2 rounded-l-full bg-emerald-500 transition-opacity duration-150 ${
                  isShowing ? 'opacity-100' : 'opacity-0'
                }`}
              />
              <Icon size={22} aria-hidden="true" />
            </button>
          );
        })}
      </nav>
    </div>
  );
};

export default RightSidebar;
