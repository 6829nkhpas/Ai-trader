'use client';

import React, { useState } from 'react';
import { flushSync } from 'react-dom';
import dynamic from 'next/dynamic';
import {
  Bell,
  ChevronDown,
  Settings,
  X as XIcon,
  Shield,
  User,
  Sun,
  Moon,
  Search,
} from 'lucide-react';
import SymbolSearchModal from './SymbolSearchModal';
import MarketTickerStrip from './MarketTickerStrip';
import { useTradeStore, TradeProfile } from '../../store/useTradeStore';
import { useAuthStore } from '../../store/useAuthStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import QuantRadar from '../quant/QuantRadar';
import UserProfileModal from '../profile/UserProfileModal';
import { PROFILES, getInitials } from '../../utils/layoutHelpers';
import { SVGS } from '../chart/toolbarIcons';

// SSR-disabled dynamic import: Tauri plugins (Stronghold, Path API) are only
// available in the desktop WebView. Loading them during Next.js server render
// triggers `Module not found: Can't resolve '@tauri-apps/plugin-stronghold'`.
// `{ ssr: false }` ensures this component is mounted strictly on the client.
const SecurityVault = dynamic(
  () => import('../settings/SecurityVault'),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-32 items-center justify-center text-[10px] text-text-muted">
        Loading vault…
      </div>
    ),
  }
);


interface TerminalLayoutProps {
  children: React.ReactNode;
  leftPanel: React.ReactNode;
}

export default function TerminalLayout({ children, leftPanel }: TerminalLayoutProps) {
  const { activeProfile, setActiveProfile, resetSession } = useTradeStore();
  const { user } = useAuthStore();
  const isFullscreen = useChartUIStore((s) => s.isFullscreen);
  const theme = useChartUIStore((s) => s.theme);
  const toggleTheme = useChartUIStore((s) => s.toggleTheme);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const handleThemeToggle = (event: React.MouseEvent<HTMLButtonElement>) => {
    const nextTheme = theme === 'dark' ? 'light' : 'dark';
    const doc = document as any;

    if (!doc.startViewTransition) {
      toggleTheme();
      return;
    }

    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX || (rect.left + rect.width / 2);
    const y = event.clientY || (rect.top + rect.height / 2);
    const endRadius = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y)
    );

    document.documentElement.style.setProperty('--theme-x', `${x}px`);
    document.documentElement.style.setProperty('--theme-y', `${y}px`);
    document.documentElement.style.setProperty('--theme-r', `${endRadius}px`);
    document.documentElement.setAttribute('data-theme-changing', 'true');

    const transition = doc.startViewTransition(() => {
      flushSync(() => {
        toggleTheme();
      });
    });

    transition.finished.finally(() => {
      document.documentElement.removeAttribute('data-theme-changing');
    });
  };
  const [profileOpen, setProfileOpen] = useState(false);

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

  const broker = user?.brokerConnection;

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

  React.useEffect(() => {
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
    <div className="flex h-screen flex-col bg-background font-sans text-text-primary">
      {/* Header */}
      <header className="z-30 flex h-12 shrink-0 items-center gap-4 border-b border-border-default bg-surface px-4 py-1.5">
        <div className="flex flex-1 items-center gap-2.5">
          <img src="/strat.svg" alt="Strat Ai Logo" className="h-4.5 w-4.5 object-contain" />
          <div className="flex items-baseline gap-1.5">
            <h1 className="text-sm font-bold tracking-tight text-text-primary">Strat AI</h1>
            <span className="text-[10px] text-text-muted border-l border-border-default pl-2">Terminal</span>
          </div>
        </div>

        {/* ── Segmented Profile Control ──────────────────────── */}
        <div className="flex shrink-0 items-center justify-center">
          <div className="flex items-center gap-0.5 rounded-none border border-border-default bg-card p-0.5 shadow-sm">
            {PROFILES.map(({ key, label, shortcut }) => {
              const isActive = activeProfile === key;
              return (
                <button
                  key={key}
                  id={`profile-btn-${key.toLowerCase()}`}
                  type="button"
                  onClick={() => setActiveProfile(key)}
                  className={`
                    relative flex items-center gap-1.5 rounded-none px-3 py-1 text-xs font-semibold
                    transition-all duration-200 ease-out select-none
                    focus-visible:outline-none
                    ${isActive
                      ? 'bg-elevated text-text-primary border border-border-default'
                      : 'text-text-secondary hover:bg-elevated/20 hover:text-text-primary border border-transparent'
                    }
                  `}
                >
                  <span>{label}</span>
                  <span
                    className={`rounded-none px-1 py-px text-[9px] font-medium leading-none ${isActive
                        ? 'bg-emerald-500/10 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                        : 'bg-elevated text-text-secondary'
                      }`}
                  >
                    {shortcut}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="flex flex-1 items-center justify-end gap-3.5 relative">
          <button 
            type="button"
            onClick={handleThemeToggle}
            className="text-text-secondary hover:text-text-primary transition-colors p-1 hover:bg-elevated/20 rounded"
            title={theme === 'dark' ? 'Switch to Light Theme' : 'Switch to Dark Theme'}
          >
            {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          </button>

          <button className="relative text-text-secondary hover:text-text-primary transition-colors p-1 hover:bg-elevated/20 rounded">
            <Bell size={15} />
            <span className="absolute top-1 right-1 h-1.5 w-1.5 rounded-full bg-red-500"></span>
          </button>

          {/* Quant Radar Dropdown */}
          <QuantRadar />

          {/* User Profile Avatar Icon */}
          <button
            onClick={() => setProfileOpen(true)}
            className="flex h-7 w-7 items-center justify-center rounded-full border border-border-default bg-surface/50 hover:bg-elevated/45 text-text-secondary hover:text-text-primary transition-all overflow-hidden"
            title="Account Profile & Settings"
          >
            {broker?.avatarUrl ? (
              <img 
                src={broker.avatarUrl} 
                alt={user?.name || 'Profile Avatar'} 
                className="h-full w-full object-cover"
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center bg-emerald-500/10 text-emerald-400 font-bold text-[10px] tracking-wider">
                {getInitials(user?.name)}
              </div>
            )}
          </button>
        </div>
      </header>

      {/* ── Live Market Ticker Strip ──────────────────────── */}
      <MarketTickerStrip />

      {/* Main Content */}
      <div className="flex flex-1 min-h-0 min-w-0 overflow-visible bg-background p-0 gap-0">
        {/* Watchlist */}
        <aside 
          className={`
            relative flex shrink-0 min-h-0 flex-col border-r border-border-default rounded-none bg-surface
            ${isResizing ? '' : 'transition-all duration-300 ease-in-out'}
            ${leftPanelOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'}
          `}
          style={{ width: leftPanelOpen ? `${leftPanelWidth}px` : '0px' }}
        >
          {/* Section Header */}
          {leftPanelOpen && (
            <div className="flex h-8 shrink-0 items-center justify-between border-b border-border-default bg-elevated/10 px-3 select-none">
              <span className="text-[10px] font-bold uppercase tracking-wider text-text-muted">Market Watch</span>
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
          )}

          <div className="flex-1 min-h-0 w-full overflow-hidden">
            {leftPanel}
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

        {/* Drawing tools are now provided natively by the TradingView
            Advanced Charts widget's left sidebar. */}

        {/* Central Area — `min-w-0` lets this flex slot actually shrink back to
            its `flex-1` allocation after fullscreen exit. Without it, the chart
            canvas's intrinsic width (set while fullscreen) becomes the column's
            min-content and pushes the whole row past the viewport. */}
        <main className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-visible">
          {!leftPanelOpen && (
            <button
              onMouseDown={handleLeftButtonMouseDown}
              style={{ top: `${leftButtonTop}px` }}
              className={`absolute left-0 z-100 flex h-7 w-6 items-center justify-center rounded-r border border-l-0 border-emerald-500/20 bg-surface/90 text-emerald-500 dark:text-emerald-400 hover:text-emerald-600 dark:hover:text-emerald-300 hover:bg-emerald-500/10 shadow-lg backdrop-blur-sm transition-all duration-200 ${
                isDraggingLeft ? 'cursor-grabbing' : 'cursor-grab'
              }`}
              title="Expand left panel (Drag to move)"
            >
              <span dangerouslySetInnerHTML={{ __html: SVGS.sidebarOpen }} className="flex items-center justify-center pointer-events-none" />
            </button>
          )}
          {children}
        </main>

      </div>

      {/* User Profile Modal Overlay */}
      <UserProfileModal isOpen={profileOpen} onClose={() => setProfileOpen(false)} />

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
