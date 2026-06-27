'use client';

import React, { useState } from 'react';
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
} from 'lucide-react';
import { useTradeStore, TradeProfile } from '../../store/useTradeStore';
import { useAuthStore } from '../../store/useAuthStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import QuantRadar from '../quant/QuantRadar';
import UserProfileModal from '../profile/UserProfileModal';
import ChartToolsBar from '../chart/ChartToolsBar';

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

const PROFILES: { key: TradeProfile; label: string; shortcut: string }[] = [
  { key: 'INTRADAY', label: 'Intraday', shortcut: 'Scalp' },
  { key: 'SWING', label: 'Swing', shortcut: '1H-4H' },
  { key: 'INVESTOR', label: 'Investor', shortcut: 'Macro' },
];

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
  const [profileOpen, setProfileOpen] = useState(false);

  const [leftPanelWidth, setLeftPanelWidth] = useState(224);
  const [isResizing, setIsResizing] = useState(false);

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

  const getInitials = (name: string | null | undefined) => {
    if (!name) return 'SA';
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  };

  return (
    <div className="flex h-screen flex-col bg-background font-sans text-text-primary">
      {/* Header */}
      <header className="z-30 flex h-12 shrink-0 items-center gap-4 border-b border-border-default bg-surface px-4 py-1.5">
        <div className="flex flex-1 items-center gap-2.5">
          <img src="/strat.svg" alt="Strat Ai Logo" className="h-[18px] w-[18px] object-contain" />
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
            onClick={toggleTheme}
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
            className="flex h-7 w-7 items-center justify-center rounded border border-border-default bg-surface/50 hover:bg-elevated/45 text-text-secondary hover:text-text-primary transition-all overflow-hidden"
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

      {/* Main Content */}
      <div className="flex flex-1 min-h-0 min-w-0 overflow-visible bg-background p-0 gap-0">
        {/* Watchlist */}
        <aside 
          className="relative flex shrink-0 min-h-0 flex-col overflow-visible border-r border-border-default rounded-none bg-surface"
          style={{ width: `${leftPanelWidth}px` }}
        >
          {leftPanel}

          {/* Resize Handle */}
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
        </aside>

        {/* Drawing Tools Bar — hidden in fullscreen because ChartSurface
            mounts its own copy inside the fullscreen overlay. */}
        {!isFullscreen && (
          <ChartToolsBar className="border-r border-border-default bg-surface py-2 rounded-none" />
        )}

        {/* Central Area — `min-w-0` lets this flex slot actually shrink back to
            its `flex-1` allocation after fullscreen exit. Without it, the chart
            canvas's intrinsic width (set while fullscreen) becomes the column's
            min-content and pushes the whole row past the viewport. */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-visible">
          {children}
        </main>

      </div>

      {/* User Profile Modal Overlay */}
      <UserProfileModal isOpen={profileOpen} onClose={() => setProfileOpen(false)} />
    </div>
  );
}
