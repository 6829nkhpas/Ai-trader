'use client';

import React, { useState, useEffect } from 'react';
import SymbolSearchModal from './SymbolSearchModal';
import QuickStartGuide from './QuickStartGuide';
import MarketTickerStrip from './MarketTickerStrip';
import NavRail from './NavRail';
import UserProfileModal from '../profile/UserProfileModal';

interface TerminalLayoutProps {
  children: React.ReactNode;
  leftPanel: React.ReactNode;
  /**
   * Rendered as a full-height sibling of the nav rail + ticker/main column,
   * so the order book (right sidebar) panel spans the full screen height
   * instead of being pushed down below the market ticker strip.
   */
  rightPanel?: React.ReactNode;
}

export default function TerminalLayout({ children, leftPanel, rightPanel }: TerminalLayoutProps) {
  const [profileOpen, setProfileOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);

  const [leftPanelWidth, setLeftPanelWidth] = useState(224);
  const [isResizing, setIsResizing] = useState(false);
  const [leftPanelOpen, setLeftPanelOpen] = useState(true);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [initialQuery, setInitialQuery] = useState('');

  const startResizing = (mouseDownEvent: React.MouseEvent) => {
    mouseDownEvent.preventDefault();
    setIsResizing(true);

    const startWidth = leftPanelWidth;
    const startX = mouseDownEvent.clientX;

    const doDrag = (mouseMoveEvent: MouseEvent) => {
      const deltaX = mouseMoveEvent.clientX - startX;
      const newWidth = Math.max(180, Math.min(500, startWidth + deltaX));
      setLeftPanelWidth(newWidth);
    };

    const stopDrag = () => {
      setIsResizing(false);
      document.removeEventListener('mousemove', doDrag);
      document.removeEventListener('mouseup', stopDrag);
    };

    document.addEventListener('mousemove', doDrag);
    document.addEventListener('mouseup', stopDrag);
  };


  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.tagName === 'SELECT' ||
        target.isContentEditable
      ) {
        return;
      }

      // Check if Ctrl+K, Cmd+K, or "/"
      const isSearchShortcut = (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k';
      const isSlash = e.key === '/';

      if (isSearchShortcut || isSlash) {
        e.preventDefault();
        setInitialQuery('');
        setIsSearchOpen(true);
        return;
      }

      // Start typing directly: printable character triggers (a-z, A-Z, 0-9)
      if (!e.ctrlKey && !e.altKey && !e.metaKey && e.key.length === 1 && /[a-zA-Z0-9]/.test(e.key)) {
        e.preventDefault();
        setInitialQuery(e.key);
        setIsSearchOpen(true);
      }
    };

    window.addEventListener('keydown', handleGlobalKeyDown);
    return () => window.removeEventListener('keydown', handleGlobalKeyDown);
  }, []);

  return (
    <div className="flex h-screen bg-background font-sans text-text-primary">
      {/* ── Primary Navigation Rail ─────────────────────────── */}
      <NavRail
        onOpenSearch={() => { setInitialQuery(''); setIsSearchOpen(true); }}
        onOpenGuide={() => setGuideOpen(true)}
        onOpenProfile={() => setProfileOpen(true)}
        leftPanelOpen={leftPanelOpen}
        onToggleLeftPanel={() => setLeftPanelOpen((open) => !open)}
      />

      {/* ── Everything right of the rail ────────────────────── */}
      <div className="flex h-full min-w-0 flex-1 flex-row min-h-0 overflow-visible bg-background p-0 gap-0">
        {/* Watchlist / Left Panel */}
        <aside
          className={`
            relative flex shrink-0 min-h-0 flex-col border-r border-border-default rounded-none bg-surface overflow-hidden
            ${isResizing ? '' : 'transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)]'}
            ${leftPanelOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'}
          `}
          style={{ width: leftPanelOpen ? `${leftPanelWidth}px` : '0px' }}
        >
          {/* Fixed-width inner container with sliding translate effect */}
          <div
            className={`flex flex-col h-full shrink-0 ${isResizing ? '' : 'transition-transform duration-300 ease-[cubic-bezier(0.4,0,0.2,1)]'}`}
            style={{
              width: `${leftPanelWidth}px`,
              transform: leftPanelOpen ? 'translateX(0)' : 'translateX(-100%)',
            }}
          >
            {/* No section header.
                A `h-8` strip used to sit here reading "MARKET WATCH", with a
                search button and a collapse button on its right. It spent a row
                naming the column it was already on top of, and its two controls
                are now in `NavRail` — where the collapse control is reachable
                even once the column is hidden, which it was not before.
                `WatchlistBlock` brings its own header, so the panel still starts
                with a titled row. */}
            <div className="flex-1 min-h-0 w-full overflow-hidden">
              {leftPanel}
            </div>
          </div>

          {/* Resize Handle */}
          {leftPanelOpen && (
            <div
              onMouseDown={startResizing}
              className={`
                absolute top-0 bottom-0 -right-1.5 w-3 cursor-col-resize z-20 hover:bg-emerald-500/10 transition-colors duration-150 rounded-none
                flex items-center justify-center group
                ${isResizing ? 'bg-emerald-500/20' : 'bg-transparent'}
              `}
              title="Drag to resize panel"
            >
              {/* Visual handle bar */}
              <div className={`
                w-0.5 h-6 bg-border-default rounded-[1px] group-hover:bg-emerald-400 transition-colors
                ${isResizing ? 'bg-emerald-400' : ''}
              `} />
            </div>
          )}
        </aside>

        {/* Right of the left panel: ticker strip above the central area, so
            the marquee only spans the chart/content column, not the
            Market Watch panel. */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-visible">
          {/* ── Live Market Ticker Strip ──────────────────────── */}
          <MarketTickerStrip />

          {/* Central Area.
              A floating, drag-to-reposition chevron used to hover over the left
              edge of the chart here, as the only way to bring the Market Watch
              column back. It is gone: the rail's toggle is always in the same
              place, is visible in both states, and does not overlap the chart. */}
          <main className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-visible">
            {children}
          </main>
        </div>
      </div>

      {/* ── Right sidebar — full height, outside the ticker strip's column ── */}
      {rightPanel}

      {/* User Profile Modal Overlay */}
      <UserProfileModal isOpen={profileOpen} onClose={() => setProfileOpen(false)} />

      {/* Quick Start Guide */}
      <QuickStartGuide open={guideOpen} onClose={() => setGuideOpen(false)} />

      {/* Symbol Search Modal */}
      <SymbolSearchModal
        isOpen={isSearchOpen}
        onClose={() => {
          setIsSearchOpen(false);
          setInitialQuery('');
        }}
        initialQuery={initialQuery}
      />
      {isResizing && (
        <div className="fixed inset-0 z-9999 cursor-col-resize select-none pointer-events-auto bg-white/0" />
      )}
    </div>
  );
}
