'use client';

import React, { useState, useEffect } from 'react';
import { Search } from 'lucide-react';
import SymbolSearchModal from './SymbolSearchModal';
import QuickStartGuide from './QuickStartGuide';
import MarketTickerStrip from './MarketTickerStrip';
import NavRail from './NavRail';
import UserProfileModal from '../profile/UserProfileModal';
import { SVGS } from '../chart/toolbarIcons';

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


  const [leftButtonTop, setLeftButtonTop] = useState(8);
  const [isDraggingLeft, setIsDraggingLeft] = useState(false);

  const handleLeftButtonMouseDown = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    const startY = e.clientY;
    const startTop = leftButtonTop;
    let dragged = false;

    const onMouseMove = (moveEvent: MouseEvent) => {
      const deltaY = moveEvent.clientY - startY;
      if (Math.abs(deltaY) > 4) {
        dragged = true;
        setIsDraggingLeft(true);
      }
      const newTop = Math.max(8, Math.min(window.innerHeight - 80, startTop + deltaY));
      setLeftButtonTop(newTop);
    };

    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      setIsDraggingLeft(false);
      if (!dragged) {
        setLeftPanelOpen(true);
      }
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
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
      />

      {/* ── Everything right of the rail ────────────────────── */}
      <div className="flex h-full min-w-0 flex-1 flex-col">
        {/* ── Live Market Ticker Strip ──────────────────────── */}
        <MarketTickerStrip />

        {/* Main Content */}
        <div className="flex flex-1 min-h-0 min-w-0 overflow-visible bg-background p-0 gap-0">
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
              {/* Section Header */}
              <div className="flex h-8 shrink-0 items-center justify-between border-b border-border-default bg-elevated/10 px-3 select-none">
                <span className="text-xs font-black uppercase tracking-wider text-text-primary/90">Market Watch</span>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => setIsSearchOpen(true)}
                    className="rounded p-0.5 text-text-muted hover:bg-elevated hover:text-text-primary transition-colors flex items-center justify-center"
                    title="Search NSE symbol..."
                  >
                    <Search size={16} />
                  </button>
                  <button
                    onClick={() => setLeftPanelOpen(false)}
                    className="rounded p-0.5 text-text-muted hover:bg-elevated hover:text-text-primary transition-colors flex items-center justify-center"
                    title="Collapse left panel"
                  >
                    <span dangerouslySetInnerHTML={{ __html: SVGS.sidebarClose }} className="flex items-center justify-center" />
                  </button>
                </div>
              </div>

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

          {/* Central Area */}
          <main className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-visible">
            <button
              onMouseDown={handleLeftButtonMouseDown}
              style={{ top: `${leftButtonTop}px` }}
              className={`absolute left-0 z-100 flex h-7 w-6 items-center justify-center rounded-r border border-l-0 border-emerald-500/20 bg-surface/90 text-emerald-500 dark:text-emerald-400 hover:text-emerald-600 dark:hover:text-emerald-300 hover:bg-emerald-500/10 shadow-lg backdrop-blur-sm transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] ${
                leftPanelOpen ? 'opacity-0 scale-95 pointer-events-none' : 'opacity-100 scale-100'
              } ${isDraggingLeft ? 'cursor-grabbing' : 'cursor-grab'}`}
              title="Expand left panel (Drag to move)"
            >
              <span dangerouslySetInnerHTML={{ __html: SVGS.sidebarOpen }} className="flex items-center justify-center pointer-events-none" />
            </button>
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
      {isDraggingLeft && (
        <div className="fixed inset-0 z-9999 cursor-row-resize select-none pointer-events-auto bg-white/0" />
      )}
    </div>
  );
}
